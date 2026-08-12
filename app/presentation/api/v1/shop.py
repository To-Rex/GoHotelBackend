"""Do'kon endpointlari: mahsulotlar, FIFO partiyalar, sotuvlar.

Qoidalar:
- Ko'rish va sotish — tizimga kirgan barcha xodimlar uchun (xarajatlar kabi);
  har bir sotuvda kim sotgani (created_by) saqlanadi.
- Mahsulot/partiya boshqaruvi — ADMIN/SUPER_ADMIN yoki xizmat boshqaruvi
  ruxsatiga ega xodim (menejer) uchun.
- Narx partiyada: sotuvda eng eski (FIFO) partiyadan boshlab yechiladi, bitta
  mahsulot ikki partiyaga bo'linsa chekda ikki qator (har xil narx) bo'ladi.
- Sotuv bronga biriktirilsa PENDING (to'lov keyin, odatda chiqishda) bo'ladi;
  oddiy sotuv darhol PAID.
"""
from datetime import date, datetime, time, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.shop import (
    ShopBatch,
    ShopProduct,
    ShopSale,
    ShopSaleItem,
    ShopWriteoff,
)
from app.infrastructure.database.models.user import User
from app.presentation.middleware.auth import get_current_user
from app.presentation.api.v1._deps import require_active_hotel

router = APIRouter(dependencies=[Depends(require_active_hotel)])

# Mahsulot/partiya boshqaruviga ruxsat beruvchi kodlar — frontenddagi
# /services boshqaruvi doirasi bilan bir xil
MANAGE_CODES = (
    "service.manage",
    "service.create",
    "service.update",
    "hotel_service.manage",
)


def _get_hotel_id(current_user: dict, hotel_id: UUID | None = None) -> UUID:
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
        if not h_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        return h_id
    h_id = current_user.get("hotel_id")
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


def _ensure_manage(current_user: dict) -> None:
    if current_user["user_type"] in ("ADMIN", "SUPER_ADMIN"):
        return
    codes = current_user.get("permissions", [])
    if not any(c in codes for c in MANAGE_CODES):
        raise ForbiddenException("Shop management permission required")


# ---------------------------------------------------------------- DTO --


class ProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=50)
    emoji: str | None = Field(default=None, max_length=8)


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=50)
    emoji: str | None = Field(default=None, max_length=8)
    is_active: bool | None = None


class BatchCreateRequest(BaseModel):
    quantity: int = Field(..., gt=0, le=100000)
    sale_price: float = Field(..., gt=0)
    cost_price: float | None = Field(default=None, ge=0)


class SaleItemRequest(BaseModel):
    product_id: UUID
    quantity: int = Field(..., gt=0, le=10000)


class SaleCreateRequest(BaseModel):
    items: list[SaleItemRequest] = Field(..., min_length=1)
    payment_method: Literal["CASH", "CARD", "TRANSFER"] | None = None
    reservation_id: UUID | None = None


class SalePayRequest(BaseModel):
    payment_method: Literal["CASH", "CARD", "TRANSFER"]


# ------------------------------------------- chek dizayni (har mehmonxonaga) --

RECEIPT_SETTINGS_KEY = "receipt"

# Standart chek dizayni — sozlanmagan mehmonxonalar uchun shu ishlatiladi
DEFAULT_RECEIPT_SETTINGS = {
    "title": "",  # bo'sh -> mehmonxona nomi
    "subtitle": "Mini-do'kon cheki",
    "header_note": "",  # sarlavha ostidagi qator (manzil/telefon)
    "footer_text": "Xaridingiz uchun rahmat!",
    "footer_note": "",  # eng pastki mayda izoh (Wi-Fi, aksiya ...)
    "show_check_no": True,
    "show_seller": True,
    "show_guest": True,
    "paper": 80,  # termal qog'oz kengligi: 58 yoki 80 mm
    "qr_url": "",  # bo'sh bo'lmasa chek oxirida QR-kod
}


class ReceiptSettingsRequest(BaseModel):
    title: str = Field(default="", max_length=64)
    subtitle: str = Field(default="", max_length=64)
    header_note: str = Field(default="", max_length=200)
    footer_text: str = Field(default="", max_length=120)
    footer_note: str = Field(default="", max_length=200)
    show_check_no: bool = True
    show_seller: bool = True
    show_guest: bool = True
    paper: Literal[58, 80] = 80
    qr_url: str = Field(default="", max_length=300)


def _resolve_receipt(settings: dict | None) -> dict:
    """Saqlangan dizaynni standart qiymatlar bilan to'ldiradi
    (faqat ma'lum kalitlar olinadi — begona kalitlar o'tkazilmaydi)."""
    saved = (settings or {}).get(RECEIPT_SETTINGS_KEY) or {}
    return {
        **DEFAULT_RECEIPT_SETTINGS,
        **{k: v for k, v in saved.items() if k in DEFAULT_RECEIPT_SETTINGS},
    }


# ------------------------------------------------------------ helpers --


def _batch_dict(b: ShopBatch) -> dict:
    return {
        "id": str(b.id),
        "quantity": b.quantity,
        "remaining": b.remaining,
        "cost_price": float(b.cost_price) if b.cost_price is not None else None,
        "sale_price": float(b.sale_price),
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


def _product_dict(p: ShopProduct, batches: list[ShopBatch]) -> dict:
    ordered = sorted(batches, key=lambda b: b.created_at or datetime.min)
    stock = sum(b.remaining for b in ordered)
    # Joriy narx — FIFO bo'yicha birinchi bo'sh bo'lmagan partiya narxi
    current = next((b for b in ordered if b.remaining > 0), None)
    last = ordered[-1] if ordered else None
    return {
        "id": str(p.id),
        "name": p.name,
        "category": p.category,
        "emoji": p.emoji,
        "is_active": p.is_active,
        "stock": stock,
        "current_price": float(current.sale_price) if current else (
            float(last.sale_price) if last else None
        ),
        "batches": [_batch_dict(b) for b in ordered],
    }


def _sale_dict(
    s: ShopSale,
    creator: User | None = None,
    reservation: Reservation | None = None,
    guest: Guest | None = None,
) -> dict:
    return {
        "id": str(s.id),
        "reservation_id": str(s.reservation_id) if s.reservation_id else None,
        "reservation_number": reservation.reservation_number if reservation else None,
        # Bron kimga tegishli — chek tafsilotida mijoz ko'rsatiladi
        "guest_name": (
            f"{guest.first_name or ''} {guest.last_name or ''}".strip() or None
            if guest
            else None
        ),
        "total_amount": float(s.total_amount),
        "payment_method": s.payment_method,
        "status": s.status,
        "paid_at": s.paid_at.isoformat() if s.paid_at else None,
        "created_by": str(s.created_by),
        "created_by_name": (
            f"{creator.first_name} {creator.last_name}".strip() if creator else None
        ),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "items": [
            {
                "product_id": str(i.product_id),
                "product_name": i.product_name,
                "quantity": i.quantity,
                "unit_price": float(i.unit_price),
                "total_price": float(i.total_price),
            }
            for i in s.items
        ],
    }


# ------------------------------------------------------ chek dizayni --


@router.get("/receipt-settings")
async def get_receipt_settings(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Chek dizayni — sotuvchi ham o'qiy oladi (chek chiqarish uchun kerak)."""
    h_id = _get_hotel_id(current_user, hotel_id)
    hotel = await session.get(Hotel, h_id)
    if not hotel:
        raise NotFoundException("Hotel not found")
    return _resolve_receipt(hotel.settings)


@router.put("/receipt-settings")
async def save_receipt_settings(
    data: ReceiptSettingsRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Dizaynni saqlash — faqat admin/menejer (do'kon boshqaruvi ruxsati)."""
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    hotel = await session.get(Hotel, h_id)
    if not hotel:
        raise NotFoundException("Hotel not found")
    # JSONB YANGI dict bilan almashtiriladi — SQLAlchemy o'zgarishni sezishi uchun
    new_settings = dict(hotel.settings or {})
    new_settings[RECEIPT_SETTINGS_KEY] = data.model_dump()
    hotel.settings = new_settings
    await session.flush()
    return _resolve_receipt(new_settings)


# ----------------------------------------------------------- products --


@router.get("/products")
async def list_products(
    include_inactive: bool = Query(default=False),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _get_hotel_id(current_user, hotel_id)
    stmt = (
        select(ShopProduct)
        .options(selectinload(ShopProduct.batches))
        .where(ShopProduct.hotel_id == h_id)
        .order_by(ShopProduct.name)
    )
    if not include_inactive:
        stmt = stmt.where(ShopProduct.is_active.is_(True))
    products = (await session.execute(stmt)).scalars().all()
    return [_product_dict(p, p.batches) for p in products]


@router.post("/products")
async def create_product(
    data: ProductCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)

    dup = await session.execute(
        select(ShopProduct.id).where(
            ShopProduct.hotel_id == h_id, ShopProduct.name == data.name.strip()
        )
    )
    if dup.first():
        raise ConflictException(
            "Product with this name already exists", "SHOP_PRODUCT_EXISTS"
        )

    product = ShopProduct(
        hotel_id=h_id,
        name=data.name.strip(),
        category=(data.category or "").strip() or None,
        emoji=(data.emoji or "").strip() or None,
        created_by=current_user["id"],
    )
    session.add(product)
    await session.flush()
    return _product_dict(product, [])


@router.put("/products/{product_id}")
async def update_product(
    product_id: UUID = Path(),
    data: ProductUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    product = await session.get(
        ShopProduct, product_id, options=[selectinload(ShopProduct.batches)]
    )
    if not product or product.hotel_id != h_id:
        raise NotFoundException("Product not found", "SHOP_PRODUCT_NOT_FOUND")

    if data.name is not None and data.name.strip() != product.name:
        dup = await session.execute(
            select(ShopProduct.id).where(
                ShopProduct.hotel_id == h_id,
                ShopProduct.name == data.name.strip(),
                ShopProduct.id != product_id,
            )
        )
        if dup.first():
            raise ConflictException(
                "Product with this name already exists", "SHOP_PRODUCT_EXISTS"
            )
        product.name = data.name.strip()
    if data.category is not None:
        product.category = data.category.strip() or None
    if data.emoji is not None:
        product.emoji = data.emoji.strip() or None
    if data.is_active is not None:
        product.is_active = data.is_active
    await session.flush()
    return _product_dict(product, product.batches)


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    product = await session.get(ShopProduct, product_id)
    if not product or product.hotel_id != h_id:
        raise NotFoundException("Product not found", "SHOP_PRODUCT_NOT_FOUND")

    # Sotuv tarixi bor mahsulot o'chirilmaydi (tarix buzilmasin) — faqat
    # nofaol qilinadi; tarixi yo'q bo'lsa butunlay o'chadi (partiyalari bilan)
    has_sales = (
        await session.execute(
            select(ShopSaleItem.id).where(ShopSaleItem.product_id == product_id).limit(1)
        )
    ).first()
    if has_sales:
        product.is_active = False
        await session.flush()
        return {"message": "Product deactivated (has sales history)", "deactivated": True}
    await session.delete(product)
    await session.flush()
    return {"message": "Product deleted", "deactivated": False}


@router.post("/products/{product_id}/batches")
async def add_batch(
    product_id: UUID = Path(),
    data: BatchCreateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    product = await session.get(
        ShopProduct, product_id, options=[selectinload(ShopProduct.batches)]
    )
    if not product or product.hotel_id != h_id:
        raise NotFoundException("Product not found", "SHOP_PRODUCT_NOT_FOUND")

    batch = ShopBatch(
        hotel_id=h_id,
        product_id=product_id,
        quantity=data.quantity,
        remaining=data.quantity,
        cost_price=data.cost_price,
        sale_price=data.sale_price,
        created_by=current_user["id"],
    )
    session.add(batch)
    await session.flush()
    await session.refresh(product, ["batches"])
    return _product_dict(product, product.batches)


# -------------------------------------------------------------- sales --


@router.get("/sales")
async def list_sales(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    # Sana qaysi maydon bo'yicha filtrlanadi: "created" — sotuv qayd etilgan
    # vaqt (do'kon sahifasi); "paid" — to'lov olingan vaqt (moliya hisoboti,
    # bronga yozilib keyin to'langan sotuv o'sha kun tushumiga tushadi)
    date_by: Literal["created", "paid"] = Query(default="created"),
    hotel_id: UUID | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=1000),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _get_hotel_id(current_user, hotel_id)
    stmt = (
        select(ShopSale, User, Reservation, Guest)
        .join(User, User.id == ShopSale.created_by)
        .outerjoin(Reservation, Reservation.id == ShopSale.reservation_id)
        .outerjoin(Guest, Guest.id == Reservation.guest_id)
        .options(selectinload(ShopSale.items))
        .where(ShopSale.hotel_id == h_id)
    )
    date_col = ShopSale.paid_at if date_by == "paid" else ShopSale.created_at
    if date_from:
        stmt = stmt.where(date_col >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(date_col <= datetime.combine(date_to, time.max))
    if status:
        stmt = stmt.where(ShopSale.status == status)
    stmt = stmt.order_by(ShopSale.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [_sale_dict(s, u, r, g) for s, u, r, g in rows]


@router.post("/sales")
async def create_sale(
    data: SaleCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _get_hotel_id(current_user, hotel_id)

    reservation: Reservation | None = None
    if data.reservation_id:
        reservation = await session.get(Reservation, data.reservation_id)
        if (
            not reservation
            or reservation.hotel_id != h_id
            or getattr(reservation, "is_deleted", False)
        ):
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        if reservation.status not in ("CONFIRMED", "CHECKED_IN"):
            raise ValidationException(
                "Sale can be linked only to an active reservation",
                "RESERVATION_NOT_ACTIVE",
            )
    elif not data.payment_method:
        raise ValidationException(
            "Payment method is required for a direct sale", "PAYMENT_METHOD_REQUIRED"
        )

    # Bitta chek ichida bir mahsulot bir necha qatorda kelsa — jamlaymiz
    wanted: dict[UUID, int] = {}
    for item in data.items:
        wanted[item.product_id] = wanted.get(item.product_id, 0) + item.quantity

    sale_items: list[ShopSaleItem] = []
    total = 0.0

    for product_id, qty in wanted.items():
        product = await session.get(ShopProduct, product_id)
        if not product or product.hotel_id != h_id or not product.is_active:
            raise NotFoundException("Product not found", "SHOP_PRODUCT_NOT_FOUND")

        # FIFO: eng eski partiyalardan boshlab yechamiz. Qatorlar qulflanadi —
        # parallel sotuvlar bir qoldiqni ikki marta sotolmaydi
        batches = (
            (
                await session.execute(
                    select(ShopBatch)
                    .where(ShopBatch.product_id == product_id, ShopBatch.remaining > 0)
                    .order_by(ShopBatch.created_at)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        available = sum(b.remaining for b in batches)
        if available < qty:
            raise ConflictException(
                f"Insufficient stock for {product.name}: {available} left",
                "SHOP_INSUFFICIENT_STOCK",
            )

        left = qty
        for batch in batches:
            if left <= 0:
                break
            take = min(batch.remaining, left)
            batch.remaining -= take
            left -= take
            unit = float(batch.sale_price)
            line_total = unit * take
            total += line_total
            sale_items.append(
                ShopSaleItem(
                    product_id=product_id,
                    batch_id=batch.id,
                    product_name=product.name,
                    quantity=take,
                    unit_price=unit,
                    total_price=line_total,
                )
            )

    sale = ShopSale(
        hotel_id=h_id,
        reservation_id=data.reservation_id,
        total_amount=total,
        payment_method=data.payment_method if not data.reservation_id else None,
        status="PENDING" if data.reservation_id else "PAID",
        paid_at=None if data.reservation_id else datetime.now(timezone.utc),
        created_by=current_user["id"],
        items=sale_items,
    )
    session.add(sale)
    await session.flush()

    creator = await session.get(User, current_user["id"])
    guest = (
        await session.get(Guest, reservation.guest_id) if reservation else None
    )
    return _sale_dict(sale, creator, reservation, guest)


@router.post("/sales/{sale_id}/pay")
async def pay_sale(
    sale_id: UUID = Path(),
    data: SalePayRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _get_hotel_id(current_user, hotel_id)
    sale = await session.get(ShopSale, sale_id, options=[selectinload(ShopSale.items)])
    if not sale or sale.hotel_id != h_id:
        raise NotFoundException("Sale not found", "SHOP_SALE_NOT_FOUND")
    if sale.status == "PAID":
        raise ConflictException("Sale is already paid", "SHOP_SALE_ALREADY_PAID")

    sale.status = "PAID"
    sale.payment_method = data.payment_method
    sale.paid_at = datetime.now(timezone.utc)
    await session.flush()

    creator = await session.get(User, sale.created_by)
    reservation = (
        await session.get(Reservation, sale.reservation_id)
        if sale.reservation_id
        else None
    )
    guest = (
        await session.get(Guest, reservation.guest_id) if reservation else None
    )
    return _sale_dict(sale, creator, reservation, guest)


@router.delete("/sales/{sale_id}")
async def cancel_sale(
    sale_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Sotuvni bekor qilish (faqat boshqaruv huquqi bilan): qoldiqlar aynan
    o'z partiyalariga qaytariladi, chek o'chadi."""
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    sale = await session.get(ShopSale, sale_id, options=[selectinload(ShopSale.items)])
    if not sale or sale.hotel_id != h_id:
        raise NotFoundException("Sale not found", "SHOP_SALE_NOT_FOUND")

    for item in sale.items:
        if item.batch_id:
            batch = await session.get(ShopBatch, item.batch_id)
            if batch:
                batch.remaining += item.quantity

    await session.delete(sale)
    await session.flush()
    return {"message": "Sale cancelled and stock restored"}


# ---------------------------------------------------------- warehouse --


class WriteoffRequest(BaseModel):
    quantity: int = Field(..., gt=0, le=100000)
    # Sabab majburiy — spisaniye auditsiz bo'lmaydi
    reason: str = Field(..., min_length=3, max_length=500)


class InventoryRequest(BaseModel):
    counted: int = Field(..., ge=0, le=1000000)
    reason: str | None = Field(default=None, max_length=500)


async def _fifo_deduct(
    session: AsyncSession, product_id: UUID, qty: int, product_name: str
) -> None:
    """Sotuvdagi kabi FIFO tartibida (qulflab) partiyalardan yechish."""
    batches = (
        (
            await session.execute(
                select(ShopBatch)
                .where(ShopBatch.product_id == product_id, ShopBatch.remaining > 0)
                .order_by(ShopBatch.created_at)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    available = sum(b.remaining for b in batches)
    if available < qty:
        raise ConflictException(
            f"Insufficient stock for {product_name}: {available} left",
            "SHOP_INSUFFICIENT_STOCK",
        )
    left = qty
    for batch in batches:
        if left <= 0:
            break
        take = min(batch.remaining, left)
        batch.remaining -= take
        left -= take


@router.post("/products/{product_id}/writeoff")
async def writeoff_product(
    product_id: UUID = Path(),
    data: WriteoffRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Spisaniye: singan/muddati o'tgan mahsulotni sabab bilan chiqarish."""
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    product = await session.get(
        ShopProduct, product_id, options=[selectinload(ShopProduct.batches)]
    )
    if not product or product.hotel_id != h_id:
        raise NotFoundException("Product not found", "SHOP_PRODUCT_NOT_FOUND")

    await _fifo_deduct(session, product_id, data.quantity, product.name)
    session.add(
        ShopWriteoff(
            hotel_id=h_id,
            product_id=product_id,
            kind="WRITEOFF",
            quantity=data.quantity,
            reason=data.reason,
            created_by=current_user["id"],
        )
    )
    await session.flush()
    await session.refresh(product, ["batches"])
    return _product_dict(product, product.batches)


@router.post("/products/{product_id}/inventory")
async def inventory_product(
    product_id: UUID = Path(),
    data: InventoryRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Inventarizatsiya: haqiqiy sanalgan qoldiq kiritiladi, farq tizimda
    tuzatiladi (kamomad — FIFO chiqarish, ortiqcha — oxirgi narxda kirim)."""
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    product = await session.get(
        ShopProduct, product_id, options=[selectinload(ShopProduct.batches)]
    )
    if not product or product.hotel_id != h_id:
        raise NotFoundException("Product not found", "SHOP_PRODUCT_NOT_FOUND")

    stock = sum(b.remaining for b in product.batches)
    diff = data.counted - stock
    if diff == 0:
        return {"diff": 0, "product": _product_dict(product, product.batches)}

    reason = (
        data.reason
        or f"Inventarizatsiya: tizimda {stock} ta, sanaldi {data.counted} ta"
    )
    if diff < 0:
        # Kamomad — yetishmayotgan miqdor ombordan chiqariladi
        await _fifo_deduct(session, product_id, -diff, product.name)
    else:
        # Ortiqcha — oxirgi partiya narxlari bilan qoldiqqa qo'shiladi
        ordered = sorted(
            product.batches, key=lambda b: b.created_at or datetime.min
        )
        last = ordered[-1] if ordered else None
        if not last:
            raise ValidationException(
                "Mahsulotda partiya yo'q — qoldiqni 'Kirim' orqali qo'shing",
                "SHOP_NO_BATCH",
            )
        session.add(
            ShopBatch(
                hotel_id=h_id,
                product_id=product_id,
                quantity=diff,
                remaining=diff,
                cost_price=last.cost_price,
                sale_price=last.sale_price,
                created_by=current_user["id"],
            )
        )

    # quantity ishorali: musbat — chiqarildi (kamomad), manfiy — qo'shildi
    session.add(
        ShopWriteoff(
            hotel_id=h_id,
            product_id=product_id,
            kind="INVENTORY",
            quantity=-diff,
            reason=reason,
            created_by=current_user["id"],
        )
    )
    await session.flush()
    await session.refresh(product, ["batches"])
    return {"diff": diff, "product": _product_dict(product, product.batches)}


@router.get("/warehouse/movements")
async def warehouse_movements(
    limit: int = Query(default=100, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ombor harakatlari jurnali: kirim, sotuv, spisaniye, inventarizatsiya."""
    _ensure_manage(current_user)
    h_id = _get_hotel_id(current_user, hotel_id)
    movements: list[dict] = []

    # Kirimlar (partiyalar)
    rows = (
        await session.execute(
            select(ShopBatch, ShopProduct.name, User)
            .join(ShopProduct, ShopProduct.id == ShopBatch.product_id)
            .join(User, User.id == ShopBatch.created_by)
            .where(ShopBatch.hotel_id == h_id)
            .order_by(ShopBatch.created_at.desc())
            .limit(limit)
        )
    ).all()
    for b, pname, u in rows:
        movements.append(
            {
                "type": "KIRIM",
                "product_name": pname,
                "quantity": b.quantity,
                "amount": float(b.sale_price) * b.quantity,
                "note": (
                    f"Tannarx: {float(b.cost_price):,.0f}"
                    if b.cost_price is not None
                    else None
                ),
                "user_name": f"{u.first_name} {u.last_name}",
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
        )

    # Sotuvlar (chek qatorlari)
    rows = (
        await session.execute(
            select(ShopSaleItem, ShopSale, User)
            .join(ShopSale, ShopSale.id == ShopSaleItem.sale_id)
            .join(User, User.id == ShopSale.created_by)
            .where(ShopSale.hotel_id == h_id)
            .order_by(ShopSaleItem.created_at.desc())
            .limit(limit)
        )
    ).all()
    for i, s, u in rows:
        movements.append(
            {
                "type": "SOTUV",
                "product_name": i.product_name,
                "quantity": -i.quantity,
                "amount": float(i.total_price),
                "note": "Bronga yozilgan" if s.status == "PENDING" else None,
                "user_name": f"{u.first_name} {u.last_name}",
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
        )

    # Spisaniye va inventarizatsiya
    rows = (
        await session.execute(
            select(ShopWriteoff, ShopProduct.name, User)
            .join(ShopProduct, ShopProduct.id == ShopWriteoff.product_id)
            .join(User, User.id == ShopWriteoff.created_by)
            .where(ShopWriteoff.hotel_id == h_id)
            .order_by(ShopWriteoff.created_at.desc())
            .limit(limit)
        )
    ).all()
    for w, pname, u in rows:
        movements.append(
            {
                "type": "SPISANIYE" if w.kind == "WRITEOFF" else "INVENTAR",
                "product_name": pname,
                # Bazada musbat=chiqarilgan; jurnalda chiqim manfiy ko'rsatiladi
                "quantity": -w.quantity,
                "amount": None,
                "note": w.reason,
                "user_name": f"{u.first_name} {u.last_name}",
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
        )

    movements.sort(key=lambda m: m["created_at"] or "", reverse=True)
    return movements[:limit]
