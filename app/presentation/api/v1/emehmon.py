"""E-mehmon (davlat ro'yxati) — sozlama, mehmonni ro'yxatga olish va chiqarish.

Sozlama har FILIAL uchun alohida (so'rov filiali) va uni faqat sozlovchi /
tizim ma'muri o'zgartiradi. Ro'yxatga olish bilan bron yuritadigan xodimlar
ishlaydi. Integratsiya yoqilmagan filialda bu yo'llar hech narsa qilmaydi.

`/gate/...` — E-mehmon serveriga so'rov ketadigan YOZUVCHI yo'llar: ular
filial yozuv qulfini olmaydi (auth.LOCK_EXEMPT_PREFIXES) — tashqi so'rov bir
necha soniya davom etsa ham filialdagi boshqa yozuvlar (bron, to'lov)
kutib qolmaydi.

Batafsil: app/application/services/emehmon_service.py.
"""
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import emehmon_payload as ep
from app.application.services.configurator_access import (
    assert_can_manage_settings,
    can_manage_settings,
)
from app.application.services.emehmon_service import EmehmonService
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.presentation.middleware.auth import get_current_user

router = APIRouter()

#: E-mehmon bilan ishlay oladiganlar: bron yuritadigan xodimlar
OPERATOR_CODES = ("reservation.create", "reservation.update")


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


def _can_operate(current_user: dict) -> bool:
    if current_user["user_type"] in ("ADMIN", "SUPER_ADMIN"):
        return True
    codes = current_user.get("permissions", [])
    return any(code in codes for code in OPERATOR_CODES)


def _ensure_operator(current_user: dict) -> None:
    if not _can_operate(current_user):
        raise ForbiddenException(
            "E-mehmon bilan faqat bron yuritadigan xodim ishlaydi", "EMEHMON_FORBIDDEN"
        )


def _ensure_reader(current_user: dict) -> None:
    """Ma'lumotnomalar: xodimga ham (shakl uchun), sozlovchiga ham (sozlama uchun)."""
    if not can_manage_settings(current_user):
        _ensure_operator(current_user)


# ------------------------------------------------------------- sozlama --


class SettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    environment: Optional[str] = Field(default=None, pattern="^(sandbox|production)$")
    username: Optional[str] = Field(default=None, max_length=120)
    #: Maxfiy kalit — faqat YOZILADI (javoblarda qaytmaydi, bazada shifrlangan)
    secret: Optional[str] = Field(default=None, max_length=300)
    clear_secret: Optional[bool] = None
    default_visit_type_id: Optional[int] = Field(default=None, ge=0)
    default_guest_type_id: Optional[int] = Field(default=None, ge=0)
    card_payment_type_id: Optional[int] = Field(default=None, ge=3, le=4)
    auto_checkout: Optional[bool] = None
    prompt_on_checkin: Optional[bool] = None


@router.get("/config")
async def emehmon_config(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ekran uchun kerakli qism: yoqilganmi va kim nima qila oladi. Hamma
    xodim o'qiydi (maxfiy narsa yo'q)."""
    cfg = await EmehmonService(session).config(_hotel_id(current_user, hotel_id))
    return {
        "enabled": cfg["enabled"],
        "environment": cfg["environment"],
        "auto_checkout": cfg["auto_checkout"],
        "prompt_on_checkin": cfg["prompt_on_checkin"],
        "can_configure": can_manage_settings(current_user),
        "can_operate": _can_operate(current_user),
    }


@router.get("/settings")
async def get_emehmon_settings(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    assert_can_manage_settings(current_user)
    cfg = await EmehmonService(session).config(_hotel_id(current_user, hotel_id))
    return {**ep.public(cfg), "problems": ep.validate(cfg)}


@router.put("/settings")
async def save_emehmon_settings(
    data: SettingsRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Filialning E-mehmon sozlamasi — faqat sozlovchi va tizim ma'muri.
    Yoqishda login va maxfiy kalit majburiy."""
    assert_can_manage_settings(current_user)
    payload = data.model_dump(exclude_unset=True)
    cfg = await EmehmonService(session).save_settings(_hotel_id(current_user, hotel_id), payload)
    return {**ep.public(cfg), "problems": ep.validate(cfg)}


@router.post("/gate/test")
async def test_emehmon_connection(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ulanishni tekshirish (saqlangan login va kalit bilan): mehmonxona
    kartasi, depozit, litsenziya va ro'yxatga olish mumkinligi."""
    assert_can_manage_settings(current_user)
    return await EmehmonService(session).status(_hotel_id(current_user, hotel_id))


# ------------------------------------------------------- ma'lumotnomalar --


@router.get("/dictionaries/{name}")
async def emehmon_dictionary(
    name: str = Path(max_length=40),
    lang: str = Query(default="uz", max_length=5),
    region_id: int | None = Query(default=None, ge=1),
    refresh: bool = Query(default=False),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_reader(current_user)
    items = await EmehmonService(session).dictionary(
        _hotel_id(current_user, hotel_id), name, lang, region_id, refresh
    )
    return {"name": name, "items": items}


@router.get("/rooms")
async def emehmon_rooms(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """E-mehmon kabinetidagi xonalar (ro'yxatdagi «xona» maydoni uchun)."""
    _ensure_reader(current_user)
    return {"items": await EmehmonService(session).rooms(_hotel_id(current_user, hotel_id))}


# ---------------------------------------------------------------- navbat --


@router.get("/pending")
async def emehmon_pending(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_operator(current_user)
    return await EmehmonService(session).pending(_hotel_id(current_user, hotel_id))


@router.get("/registrations")
async def emehmon_journal(
    status: str | None = Query(
        default=None, pattern="^(sending|registered|checked_out|failed|skipped)$"
    ),
    search: str | None = Query(default=None, max_length=100),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_operator(current_user)
    return await EmehmonService(session).journal(
        _hotel_id(current_user, hotel_id), status=status, search=search, skip=skip, limit=limit
    )


@router.get("/reservations/{reservation_id}")
async def emehmon_sheet(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bronning E-mehmon varag'i: mehmonlar, holatlari, to'ldirilgan shakllar."""
    _ensure_operator(current_user)
    return await EmehmonService(session).sheet(_hotel_id(current_user, hotel_id), reservation_id)


# ------------------------------------------------------- ro'yxatga olish --


class LookupRequest(BaseModel):
    passport_number: str = Field(..., min_length=1, max_length=50)
    birth_date: str = Field(..., min_length=8, max_length=10)
    citizen_id: int = Field(..., ge=1)


class RegisterRequest(BaseModel):
    guest_id: UUID
    form: dict[str, Any]


class LinkRequest(BaseModel):
    guest_id: UUID
    listok_id: int = Field(..., ge=1)


class SkipRequest(BaseModel):
    guest_id: UUID
    note: Optional[str] = Field(default=None, max_length=300)


class CheckoutRequest(BaseModel):
    sum: Optional[float] = Field(default=None, ge=0)
    payment_type_id: Optional[int] = Field(default=None, ge=1, le=6)


@router.post("/gate/lookup")
async def emehmon_lookup(
    data: LookupRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """«Bazadan tekshirish»: pasport + tug'ilgan sana + fuqarolik bo'yicha
    rasmiy ma'lumot (mahalliy — IIV, chet ellik — Chegara xizmati)."""
    _ensure_operator(current_user)
    return await EmehmonService(session).lookup(
        _hotel_id(current_user, hotel_id), data.passport_number, data.birth_date, data.citizen_id
    )


@router.post("/gate/reservations/{reservation_id}/register")
async def emehmon_register(
    data: RegisterRequest,
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mehmonni E-mehmon'da ro'yxatga oladi. E-mehmon rad etsa ham javob 200:
    natija `status` (`registered` | `failed`) va xato matnida."""
    _ensure_operator(current_user)
    return await EmehmonService(session).register(
        _hotel_id(current_user, hotel_id), reservation_id, data.guest_id, data.form,
        current_user["id"],
    )


@router.post("/gate/reservations/{reservation_id}/link")
async def emehmon_link(
    data: LinkRequest,
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """E-mehmon'da allaqachon bor ro'yxatni shu mehmonga bog'laydi."""
    _ensure_operator(current_user)
    return await EmehmonService(session).link(
        _hotel_id(current_user, hotel_id), reservation_id, data.guest_id, data.listok_id,
        current_user["id"],
    )


@router.post("/reservations/{reservation_id}/skip")
async def emehmon_skip(
    data: SkipRequest,
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """«Kerak emas» — mehmon navbatdan chiqadi (E-mehmon'ga hech narsa ketmaydi)."""
    _ensure_operator(current_user)
    return await EmehmonService(session).skip(
        _hotel_id(current_user, hotel_id), reservation_id, data.guest_id, data.note,
        current_user["id"],
    )


@router.delete("/reservations/{reservation_id}/skip")
async def emehmon_unskip(
    reservation_id: UUID = Path(),
    guest_id: UUID = Query(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_operator(current_user)
    await EmehmonService(session).unskip(
        _hotel_id(current_user, hotel_id), reservation_id, guest_id
    )
    return {"ok": True}


@router.post("/gate/registrations/{registration_id}/checkout")
async def emehmon_checkout(
    data: CheckoutRequest,
    registration_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mehmonni E-mehmon'dan chiqaradi. Summa / to'lov turi berilmasa bron
    bo'yicha hisoblanadi."""
    _ensure_operator(current_user)
    return await EmehmonService(session).checkout(
        _hotel_id(current_user, hotel_id), registration_id, data.sum, data.payment_type_id,
        current_user["id"],
    )


@router.post("/gate/registrations/{registration_id}/refresh")
async def emehmon_refresh(
    registration_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """E-mehmon'dagi holat bilan solishtiradi (kabinetda yopilgan bo'lsa — yopadi)."""
    _ensure_operator(current_user)
    return await EmehmonService(session).refresh(
        _hotel_id(current_user, hotel_id), registration_id
    )
