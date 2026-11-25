"""Fiskal modul (E-POS) — sozlama va fiskal cheklar navbati.

Sozlama har FILIAL uchun alohida (so'rov filiali) va uni faqat sozlovchi /
tizim ma'muri o'zgartiradi. Navbat bilan kassada ishlaydigan xodimlar
ishlaydi: chekni tayyorlash, yuborishga olish va natijani yozish.
Batafsil: app/application/services/fiscal_service.py.
"""
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import fiscal_payload as fp
from app.application.services.configurator_access import (
    assert_can_manage_settings,
    can_manage_settings,
)
from app.application.services.fiscal_service import FiscalService, serialize
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.presentation.middleware.auth import get_current_user

router = APIRouter()

#: Fiskal navbat bilan ishlay oladiganlar: pul qabul qiladigan xodimlar
CASHIER_CODES = (
    "finance.payment.create",
    "reservation.create",
    "reservation.update",
    "service.manage",
    "service.create",
    "hotel_service.manage",
    "finance.view",
)


def _hotel_id(current_user: dict, hotel_id: UUID | None = None) -> UUID:
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
        if not h_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        return h_id
    h_id = current_user.get("hotel_id")
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


def _is_admin(current_user: dict) -> bool:
    return current_user["user_type"] in ("ADMIN", "SUPER_ADMIN")


def _ensure_cashier(current_user: dict) -> None:
    if _is_admin(current_user):
        return
    codes = current_user.get("permissions", [])
    if not any(c in codes for c in CASHIER_CODES):
        raise ForbiddenException(
            "Fiskal cheklar bilan faqat to'lov qabul qiladigan xodim ishlaydi", "FISCAL_FORBIDDEN"
        )


# ------------------------------------------------------------- sozlama --


class ItemCodes(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    class_code: Optional[str] = Field(default=None, max_length=20)
    package_code: Optional[str] = Field(default=None, max_length=20)


class SettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    transport: Optional[str] = Field(default=None, pattern="^(browser|server)$")
    url: Optional[str] = Field(default=None, max_length=300)
    token: Optional[str] = Field(default=None, max_length=80)
    company_name: Optional[str] = Field(default=None, max_length=200)
    company_address: Optional[str] = Field(default=None, max_length=300)
    company_inn: Optional[str] = Field(default=None, max_length=20)
    company_phone: Optional[str] = Field(default=None, max_length=30)
    printer_size: Optional[int] = None
    vat_percent: Optional[int] = None
    items: Optional[dict[str, ItemCodes]] = None
    methods: Optional[dict[str, str]] = None
    prepayment_as_advance: Optional[bool] = None
    auto_open_shift: Optional[bool] = None
    epos_prints: Optional[bool] = None


@router.get("/config")
async def fiscal_config(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Kassa uchun kerakli qism: yoqilganmi, qanday ulanadi. Hamma xodim
    o'qiydi (to'lovdan keyin chek chiqarish shunga qaraydi)."""
    cfg = await FiscalService(session).config(_hotel_id(current_user, hotel_id))
    return {
        "enabled": cfg["enabled"],
        "transport": cfg["transport"],
        "url": cfg["url"],
        "token": cfg["token"],
        "auto_open_shift": cfg["auto_open_shift"],
        "epos_prints": cfg["epos_prints"],
        "company_name": cfg["company_name"],
        "company_inn": cfg["company_inn"],
        "can_configure": can_manage_settings(current_user),
        "can_operate": _is_admin(current_user)
        or any(c in current_user.get("permissions", []) for c in CASHIER_CODES),
    }


@router.get("/settings")
async def get_fiscal_settings(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    assert_can_manage_settings(current_user)
    cfg = await FiscalService(session).config(_hotel_id(current_user, hotel_id))
    return {**cfg, "problems": fp.validate(cfg), "default_url": fp.DEFAULT_URL}


@router.put("/settings")
async def save_fiscal_settings(
    data: SettingsRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Filialning fiskal sozlamasi — faqat sozlovchi. Yoqishda majburiy
    maydonlar tekshiriladi; faqat yoqilgandan keyingi to'lovlar fiskallashadi."""
    assert_can_manage_settings(current_user)
    payload = data.model_dump(exclude_unset=True)
    cfg = await FiscalService(session).save_settings(_hotel_id(current_user, hotel_id), payload)
    return {**cfg, "problems": fp.validate(cfg), "default_url": fp.DEFAULT_URL}


# -------------------------------------------------------------- navbat --


class PrepareRequest(BaseModel):
    source: str = Field(..., pattern="^(RESERVATION|SHOP)$")
    invoice_id: Optional[UUID] = None
    reservation_id: Optional[UUID] = None
    sale_id: Optional[UUID] = None


class CompleteRequest(BaseModel):
    terminal_id: Optional[str] = Field(default=None, max_length=40)
    receipt_seq: Optional[str] = Field(default=None, max_length=20)
    fiscal_sign: str = Field(..., min_length=1, max_length=40)
    fiscal_datetime: Optional[str] = Field(default=None, max_length=20)
    qr_url: Optional[str] = Field(default=None, max_length=1000)


class FailRequest(BaseModel):
    error: str = Field(default="", max_length=2000)


@router.get("/pending")
async def fiscal_pending(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_cashier(current_user)
    return await FiscalService(session).pending(_hotel_id(current_user, hotel_id))


@router.post("/prepare")
async def fiscal_prepare(
    data: PrepareRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Chekka kirmagan to'lovlardan fiskal chek(lar) tayyorlaydi."""
    _ensure_cashier(current_user)
    h_id = _hotel_id(current_user, hotel_id)
    svc = FiscalService(session)
    if data.source == "SHOP":
        receipts = await svc.prepare_shop(h_id, current_user["id"], data.sale_id) if data.sale_id else []
    else:
        receipts = await svc.prepare_reservation(
            h_id, current_user["id"], invoice_id=data.invoice_id, reservation_id=data.reservation_id
        )
    return {"receipts": [serialize(r) for r in receipts]}


@router.post("/receipts/{receipt_id}/claim")
async def fiscal_claim(
    receipt_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_cashier(current_user)
    return await FiscalService(session).claim(_hotel_id(current_user, hotel_id), receipt_id)


@router.post("/receipts/{receipt_id}/complete")
async def fiscal_complete(
    data: CompleteRequest,
    receipt_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_cashier(current_user)
    receipt = await FiscalService(session).complete(
        _hotel_id(current_user, hotel_id), receipt_id, data.model_dump()
    )
    return serialize(receipt)


@router.post("/receipts/{receipt_id}/fail")
async def fiscal_fail(
    data: FailRequest,
    receipt_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_cashier(current_user)
    receipt = await FiscalService(session).fail(
        _hotel_id(current_user, hotel_id), receipt_id, data.error
    )
    return serialize(receipt)


@router.post("/receipts/{receipt_id}/dismiss")
async def fiscal_dismiss(
    data: FailRequest,
    receipt_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Chekni navbatdan olib tashlash — faqat administrator (sababi bilan)."""
    if not _is_admin(current_user):
        raise ForbiddenException(
            "Chekni navbatdan faqat administrator olib tashlaydi", "FISCAL_FORBIDDEN"
        )
    receipt = await FiscalService(session).dismiss(
        _hotel_id(current_user, hotel_id), receipt_id, data.error
    )
    return serialize(receipt)


@router.get("/receipts")
async def fiscal_receipts(
    status: str | None = Query(default=None, description="pending,sent,done,failed,skipped"),
    reservation_id: UUID | None = Query(default=None),
    sale_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Fiskal cheklar. `reservation_id` / `sale_id` berilsa — shu bron yoki
    sotuvning FISKALLASHGAN cheklari (chekni qayta chiqarishda QR uchun)."""
    h_id = _hotel_id(current_user, hotel_id)
    svc = FiscalService(session)
    if reservation_id or sale_id:
        items = await svc.for_target(h_id, reservation_id=reservation_id, sale_id=sale_id)
        return {"items": items, "total": len(items)}
    _ensure_cashier(current_user)
    return await svc.list(h_id, status=status, limit=limit, offset=offset)


@router.post("/relay")
async def fiscal_relay(
    body: dict[str, Any],
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """"Server orqali" rejimi: so'rovni backend E-POS manziliga yuboradi."""
    _ensure_cashier(current_user)
    return await FiscalService(session).relay(_hotel_id(current_user, hotel_id), body)
