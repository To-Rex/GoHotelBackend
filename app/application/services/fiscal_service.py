"""Fiskal modul (E-POS) — fiskal cheklar navbati.

Qanday ishlaydi:

1. Filialda fiskal modul yoqilgan bo'lsa, yoqilgan paytdan keyingi har bir
   to'lov (bron to'lovi, do'kon to'lovi, qaytarim) fiskal chekka kirishi
   kerak. Qaysilari hali kirmagani — `pending()`.
2. Kassa kompyuteri (yoki "server orqali" rejimida istalgan qurilma)
   `prepare_*` chaqiradi: server to'lovlarni bitta chekka yig'adi, E-POS
   so'rovini TUZADI (summalar va IKPU kodlari bazadan — brauzerga ishonilmaydi)
   va chekni `pending` holatida saqlaydi. To'lov ↔ chek bog'lanishi unique —
   ikki qurilma bitta to'lovni ikki marta ololmaydi.
3. Qurilma `claim()` qiladi (holat `sent`), so'rovni E-POS Communicator'ga
   yuboradi va natijani `complete()` / `fail()` bilan qaytaradi.
4. `done` chekdagi soliq havolasi (`qr_url`) bosma chekda QR bo'lib chiqadi.

Fiskal modul o'chiq filialda bu servis hech narsa yozmaydi.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.services import fiscal_payload as fp
from app.application.services import payment_purpose as purpose
from app.application.services.branch_settings import branch_settings, settings_owner
from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from app.infrastructure.database.models.fiscal import FiscalReceipt, FiscalReceiptLink
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.invoice import Invoice
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.shop import (
    ShopProduct,
    ShopSale,
    ShopSalePayment,
)
from app.infrastructure.database.models.user import User

logger = logging.getLogger(__name__)

LINK_PAYMENT = "payment"
LINK_SHOP = "shop_payment"
SOURCE_RESERVATION = "RESERVATION"
SOURCE_SHOP = "SHOP"

#: Bundan eski to'lovlar navbatga olinmaydi (uzoq o'chiq turgan kassa
#: yoqilganda eski to'lovlar to'satdan fiskallashib ketmasin)
PENDING_WINDOW = timedelta(days=7)
#: `sent` holatidagi chekni boshqa qurilma shu vaqtdan keyingina qayta oladi
SENDING_LOCK = timedelta(seconds=90)

#: "Server orqali" rejimida E-POS'ga yuboriladigan usullar
RELAY_METHODS = frozenset({
    "sale", "fastSale", "advance", "refund",
    "openZreport", "closeZreport", "getZReportsStatus",
    "checkStatus", "getVersion", "getStatus", "checkReceiptIfExists",
    "getUnsentCount", "getZReportCount",
    # IKPU kodi uchun o'ram (o'lchov birligi) kodlari ro'yxati
    "getICPCPackage",
})

_ROOM_KINDS = {purpose.KIND_ROOM, purpose.KIND_EXTRA, purpose.KIND_OTHER}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize(r: FiscalReceipt) -> dict:
    return {
        "id": str(r.id),
        "kind": r.kind,
        "source": r.source,
        "number": r.number,
        "title": r.title,
        "amount": float(r.amount or 0),
        "cash_amount": float(r.cash_amount or 0),
        "card_amount": float(r.card_amount or 0),
        "status": r.status,
        "attempts": r.attempts,
        "error": r.error,
        "terminal_id": r.terminal_id,
        "receipt_seq": r.receipt_seq,
        "fiscal_sign": r.fiscal_sign,
        "fiscal_datetime": r.fiscal_datetime,
        "qr_url": r.qr_url,
        "invoice_id": str(r.invoice_id) if r.invoice_id else None,
        "reservation_id": str(r.reservation_id) if r.reservation_id else None,
        "shop_sale_id": str(r.shop_sale_id) if r.shop_sale_id else None,
        "refund_of_id": str(r.refund_of_id) if r.refund_of_id else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


class FiscalService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- sozlamalar --

    async def config(self, hotel_id: UUID | None) -> dict:
        raw = (await branch_settings(self.session, hotel_id)).get(fp.SETTINGS_KEY)
        return fp.resolve(raw)

    async def save_settings(self, hotel_id: UUID, data: dict) -> dict:
        owner = await settings_owner(self.session, hotel_id)
        if owner is None:
            raise NotFoundException("Hotel not found")
        stored = dict((owner.settings or {}).get(fp.SETTINGS_KEY) or {})
        was = fp.resolve(stored)
        merged = fp.resolve({**stored, **data})
        if merged["enabled"]:
            errors = fp.validate(merged)
            if errors:
                raise ValidationException(errors[0], "FISCAL_SETTINGS_INVALID")
            # Faqat yoqilgandan KEYINGI to'lovlar fiskallashtiriladi
            merged["enabled_at"] = (
                was["enabled_at"] if was["enabled"] and was["enabled_at"] else _now().isoformat()
            )
        else:
            merged["enabled_at"] = None
        # JSONB YANGI dict bilan almashtiriladi — SQLAlchemy o'zgarishni sezishi uchun
        new_settings = dict(owner.settings or {})
        new_settings[fp.SETTINGS_KEY] = merged
        owner.settings = new_settings
        await self.session.flush()
        return merged

    # ----------------------------------------------------------- navbat --

    def _since(self, cfg: dict) -> datetime:
        floor = _now() - PENDING_WINDOW
        enabled = fp.enabled_since(cfg)
        if enabled is None:
            return _now()
        if enabled.tzinfo is None:
            enabled = enabled.replace(tzinfo=timezone.utc)
        return max(enabled, floor)

    def _unlinked(self, source: str):
        return select(FiscalReceiptLink.source_id).where(FiscalReceiptLink.source == source)

    async def pending(self, hotel_id: UUID) -> dict:
        """Navbat: fiskal chekka kirmagan to'lovlar va tugallanmagan cheklar."""
        cfg = await self.config(hotel_id)
        if not cfg["enabled"]:
            return {"enabled": False, "targets": [], "receipts": [], "count": 0}
        since = self._since(cfg)
        targets: list[dict] = []
        for (invoice_id,) in (
            await self.session.execute(
                select(Payment.invoice_id)
                .where(
                    Payment.hotel_id == hotel_id,
                    Payment.created_at >= since,
                    Payment.id.notin_(self._unlinked(LINK_PAYMENT)),
                )
                .distinct()
            )
        ).all():
            targets.append({"source": SOURCE_RESERVATION, "invoice_id": str(invoice_id)})
        for (sale_id,) in (
            await self.session.execute(
                select(ShopSalePayment.sale_id)
                .where(
                    ShopSalePayment.hotel_id == hotel_id,
                    ShopSalePayment.paid_at >= since,
                    ShopSalePayment.id.notin_(self._unlinked(LINK_SHOP)),
                )
                .distinct()
            )
        ).all():
            targets.append({"source": SOURCE_SHOP, "sale_id": str(sale_id)})

        stale = _now() - SENDING_LOCK
        receipts = (
            await self.session.execute(
                select(FiscalReceipt)
                .where(
                    FiscalReceipt.hotel_id == hotel_id,
                    FiscalReceipt.created_at >= _now() - PENDING_WINDOW,
                    (FiscalReceipt.status.in_(("pending", "failed")))
                    | ((FiscalReceipt.status == "sent") & (FiscalReceipt.sent_at < stale)),
                )
                .order_by(FiscalReceipt.created_at)
            )
        ).scalars().all()
        return {
            "enabled": True,
            "targets": targets,
            "receipts": [serialize(r) for r in receipts],
            "count": len(targets) + len(receipts),
        }

    # -------------------------------------------------------- tayyorlash --

    async def _staff_name(self, user_id) -> str:
        user = await self.session.get(User, user_id) if user_id else None
        if not user:
            return ""
        return f"{user.first_name or ''} {user.last_name or ''}".strip()

    async def _create(
        self, receipt: FiscalReceipt, links: list[tuple[str, UUID]]
    ) -> FiscalReceipt | None:
        """Chekni to'lovlarga bog'lab saqlaydi. To'lov allaqachon boshqa
        chekda bo'lsa (poyga) — hech narsa yozilmaydi, `None`."""
        try:
            async with self.session.begin_nested():
                self.session.add(receipt)
                await self.session.flush()
                for source, source_id in links:
                    self.session.add(
                        FiscalReceiptLink(receipt_id=receipt.id, source=source, source_id=source_id)
                    )
                await self.session.flush()
        except IntegrityError:
            return None
        return receipt

    def _new(self, hotel_id: UUID, user_id, **fields) -> FiscalReceipt:
        return FiscalReceipt(
            hotel_id=hotel_id, number=fp.new_number(), created_by=user_id, attempts=0, **fields
        )

    async def prepare_reservation(
        self,
        hotel_id: UUID,
        user_id,
        *,
        invoice_id: UUID | None = None,
        reservation_id: UUID | None = None,
    ) -> list[FiscalReceipt]:
        """Bron (hisob-faktura) bo'yicha chekka kirmagan to'lovlardan cheklar:
        kirimlar — bitta sotuv/avans cheki, har bir qaytarim — alohida."""
        cfg = await self.config(hotel_id)
        if not cfg["enabled"]:
            return []
        stmt = select(Invoice).where(Invoice.hotel_id == hotel_id)
        if invoice_id:
            stmt = stmt.where(Invoice.id == invoice_id)
        elif reservation_id:
            stmt = stmt.where(Invoice.reservation_id == reservation_id)
        else:
            raise ValidationException("invoice_id yoki reservation_id kerak", "FISCAL_TARGET_REQUIRED")
        invoice = (await self.session.execute(stmt.order_by(Invoice.created_at.desc()))).scalars().first()
        if invoice is None:
            return []

        payments = (
            await self.session.execute(
                select(Payment)
                .where(
                    Payment.invoice_id == invoice.id,
                    Payment.created_at >= self._since(cfg),
                    Payment.id.notin_(self._unlinked(LINK_PAYMENT)),
                )
                .order_by(Payment.created_at, Payment.id)
            )
        ).scalars().all()
        if not payments:
            return []

        reservation = await self.session.get(Reservation, invoice.reservation_id)
        guest = await self.session.get(Guest, reservation.guest_id) if reservation else None
        room = await self.session.get(Room, reservation.room_id) if reservation else None
        client = f"{guest.first_name or ''} {guest.last_name or ''}".strip() if guest else ""
        number = reservation.reservation_number if reservation else ""
        title = " · ".join(x for x in (number, f"xona {room.room_number}" if room else "") if x)
        staff = await self._staff_name(user_id)
        base = {
            "source": SOURCE_RESERVATION,
            "invoice_id": invoice.id,
            "reservation_id": invoice.reservation_id,
            "title": title or None,
        }
        target = lambda p: cfg["methods"].get(fp.canonical_method(p.payment_method), "card")  # noqa: E731

        created: list[FiscalReceipt] = []
        incoming = [p for p in payments if float(p.amount) > 0]
        skipped = [p for p in incoming if target(p) == "skip"]
        fiscal = [p for p in incoming if target(p) != "skip"]

        if skipped:
            receipt = await self._create(
                self._new(
                    hotel_id, user_id, kind="sale", status="skipped",
                    amount=sum(float(p.amount) for p in skipped),
                    error="To'lov usuli fiskallashtirilmaydi (sozlamada belgilangan)", **base,
                ),
                [(LINK_PAYMENT, p.id) for p in skipped],
            )
            if receipt:
                created.append(receipt)

        if fiscal:
            # Sirtdan import: finance_service og'ir va aylanma importga moyil
            from app.application.services.finance_service import FinanceService

            allocations = await FinanceService(self.session)._allocations([invoice.id])
            parts: dict[str, float] = {}
            for p in fiscal:
                for part in allocations.get(p.id, []):
                    kind = (
                        "room" if part["kind"] in _ROOM_KINDS
                        else "service" if part["kind"] == purpose.KIND_SERVICE
                        else "penalty" if part["kind"] == purpose.KIND_PENALTY
                        else "room"
                    )
                    parts[kind] = round(parts.get(kind, 0.0) + float(part["amount"]), 2)
            total = round(sum(float(p.amount) for p in fiscal), 2)
            sums = fp.split_methods([(p.payment_method, float(p.amount)) for p in fiscal], cfg)
            kind = (
                "advance"
                if cfg["prepayment_as_advance"]
                and reservation is not None
                and reservation.status in ("PENDING", "CONFIRMED")
                else "sale"
            )
            draft = self._new(
                hotel_id, user_id, kind=kind, status="pending", amount=total,
                cash_amount=sums["cash"], card_amount=sums["card"], **base,
            )
            draft.payload = fp.build_request(
                cfg, kind=kind, number=draft.number,
                items=fp.reservation_items(parts, total, cfg, f"xona {room.room_number}" if room else ""),
                cash=sums["cash"], card=sums["card"], staff_name=staff, client_name=client,
                note=f"Bron {number}" if number else "",
            )
            receipt = await self._create(draft, [(LINK_PAYMENT, p.id) for p in fiscal])
            if receipt:
                created.append(receipt)

        for p in (x for x in payments if float(x.amount) < 0):
            refund = abs(float(p.amount))
            original = await self._original_for(invoice_id=invoice.id, amount=refund)
            where = target(p)
            if where == "skip" or original is None:
                draft = self._new(
                    hotel_id, user_id, kind="refund", status="skipped", amount=refund,
                    error=(
                        "To'lov usuli fiskallashtirilmaydi (sozlamada belgilangan)"
                        if where == "skip"
                        else "Asl fiskal chek topilmadi — to'lov fiskal modul yoqilishidan oldin olingan"
                    ),
                    **base,
                )
            else:
                draft = self._new(
                    hotel_id, user_id, kind="refund", status="pending", amount=refund,
                    cash_amount=refund if where == "cash" else 0,
                    card_amount=refund if where == "card" else 0,
                    refund_of_id=original.id, **base,
                )
                draft.payload = fp.build_request(
                    cfg, kind="refund", number=draft.number,
                    items=fp.refund_items(
                        ((original.payload or {}).get("params") or {}).get("items") or [], refund, cfg
                    ),
                    cash=refund if where == "cash" else 0, card=refund if where == "card" else 0,
                    staff_name=staff, client_name=client,
                    note=f"Qaytarim · bron {number}" if number else "Qaytarim",
                    refund_info=serialize(original),
                )
            receipt = await self._create(draft, [(LINK_PAYMENT, p.id)])
            if receipt:
                created.append(receipt)
        return created

    async def _original_for(
        self, *, invoice_id: UUID | None = None, amount: float = 0
    ) -> FiscalReceipt | None:
        """Qaytarim bog'lanadigan asl chek: summasi yetadigan eng oxirgisi,
        bo'lmasa eng kattasi."""
        rows = (
            await self.session.execute(
                select(FiscalReceipt)
                .where(
                    FiscalReceipt.invoice_id == invoice_id,
                    FiscalReceipt.status == "done",
                    FiscalReceipt.kind.in_(("sale", "advance")),
                )
                .order_by(FiscalReceipt.completed_at.desc())
            )
        ).scalars().all()
        if not rows:
            return None
        for row in rows:
            if float(row.amount) + 0.005 >= amount:
                return row
        return max(rows, key=lambda r: float(r.amount))

    async def prepare_shop(self, hotel_id: UUID, user_id, sale_id: UUID) -> list[FiscalReceipt]:
        """Do'kon sotuvi bo'yicha chekka kirmagan to'lovlardan chek."""
        cfg = await self.config(hotel_id)
        if not cfg["enabled"]:
            return []
        # Aniq so'rov: sotuv sessiyada allaqachon bo'lsa ham pozitsiyalari yuklanadi
        sale = (
            await self.session.execute(
                select(ShopSale)
                .options(selectinload(ShopSale.items))
                .where(ShopSale.id == sale_id)
                .execution_options(populate_existing=True)
            )
        ).scalars().first()
        if sale is None or sale.hotel_id != hotel_id:
            return []
        pays = (
            await self.session.execute(
                select(ShopSalePayment)
                .where(
                    ShopSalePayment.sale_id == sale.id,
                    ShopSalePayment.paid_at >= self._since(cfg),
                    ShopSalePayment.id.notin_(self._unlinked(LINK_SHOP)),
                )
                .order_by(ShopSalePayment.paid_at, ShopSalePayment.id)
            )
        ).scalars().all()
        pays = [p for p in pays if float(p.amount) > 0]
        if not pays:
            return []
        target = lambda p: cfg["methods"].get(fp.canonical_method(p.payment_method), "card")  # noqa: E731
        base = {"source": SOURCE_SHOP, "shop_sale_id": sale.id, "reservation_id": sale.reservation_id,
                "title": "Do'kon"}
        created: list[FiscalReceipt] = []
        skipped = [p for p in pays if target(p) == "skip"]
        fiscal = [p for p in pays if target(p) != "skip"]
        if skipped:
            receipt = await self._create(
                self._new(
                    hotel_id, user_id, kind="sale", status="skipped",
                    amount=sum(float(p.amount) for p in skipped),
                    error="To'lov usuli fiskallashtirilmaydi (sozlamada belgilangan)", **base,
                ),
                [(LINK_SHOP, p.id) for p in skipped],
            )
            if receipt:
                created.append(receipt)
        if fiscal:
            codes = {
                pid: (ikpu, package)
                for pid, ikpu, package in (
                    await self.session.execute(
                        select(ShopProduct.id, ShopProduct.ikpu_code, ShopProduct.package_code).where(
                            ShopProduct.id.in_([i.product_id for i in sale.items])
                        )
                    )
                ).all()
            }
            rows = [
                {
                    "name": i.product_name, "quantity": i.quantity, "total": float(i.total_price),
                    "ikpu": codes.get(i.product_id, (None, None))[0],
                    "package": codes.get(i.product_id, (None, None))[1],
                }
                for i in sale.items
            ]
            total = round(sum(float(p.amount) for p in fiscal), 2)
            sums = fp.split_methods([(p.payment_method, float(p.amount)) for p in fiscal], cfg)
            draft = self._new(
                hotel_id, user_id, kind="sale", status="pending", amount=total,
                cash_amount=sums["cash"], card_amount=sums["card"], **base,
            )
            draft.payload = fp.build_request(
                cfg, kind="sale", number=draft.number,
                items=fp.shop_items(rows, total, float(sale.total_amount or 0), cfg),
                cash=sums["cash"], card=sums["card"],
                staff_name=await self._staff_name(user_id), note="Do'kon",
            )
            receipt = await self._create(draft, [(LINK_SHOP, p.id) for p in fiscal])
            if receipt:
                created.append(receipt)
        return created

    async def queue_sale_cancel(self, hotel_id: UUID, user_id, sale_id: UUID) -> int:
        """Do'kon sotuvi bekor qilinganda: fiskallashgan cheklari uchun
        qaytarim cheklari navbatga qo'yiladi, hali yuborilmaganlari bekor bo'ladi."""
        cfg = await self.config(hotel_id)
        rows = (
            await self.session.execute(
                select(FiscalReceipt).where(
                    FiscalReceipt.hotel_id == hotel_id, FiscalReceipt.shop_sale_id == sale_id
                )
            )
        ).scalars().all()
        refunded = {r.refund_of_id for r in rows if r.refund_of_id}
        staff = await self._staff_name(user_id)
        queued = 0
        for r in rows:
            if r.status in ("pending", "failed"):
                r.status = "skipped"
                r.error = "Sotuv bekor qilingan"
            elif r.status == "done" and r.kind == "sale" and r.id not in refunded and cfg["enabled"]:
                draft = self._new(
                    hotel_id, user_id, kind="refund", status="pending", source=SOURCE_SHOP,
                    shop_sale_id=sale_id, reservation_id=r.reservation_id, title="Do'kon · bekor qilish",
                    amount=float(r.amount), cash_amount=float(r.cash_amount),
                    card_amount=float(r.card_amount), refund_of_id=r.id,
                )
                draft.payload = fp.build_request(
                    cfg, kind="refund", number=draft.number,
                    items=((r.payload or {}).get("params") or {}).get("items") or [],
                    cash=float(r.cash_amount), card=float(r.card_amount), staff_name=staff,
                    note="Qaytarim · do'kon", refund_info=serialize(r),
                )
                if await self._create(draft, []):
                    queued += 1
        await self.session.flush()
        return queued

    # ---------------------------------------------------------- yuborish --

    async def _get(self, hotel_id: UUID, receipt_id: UUID) -> FiscalReceipt:
        receipt = await self.session.get(FiscalReceipt, receipt_id)
        if receipt is None or receipt.hotel_id != hotel_id:
            raise NotFoundException("Fiskal chek topilmadi", "FISCAL_RECEIPT_NOT_FOUND")
        return receipt

    async def claim(self, hotel_id: UUID, receipt_id: UUID) -> dict:
        """Chekni yuborishga oladi: `sent`, urinish +1. Javobda E-POS so'rovi
        (token bilan) va `verify` — avval yuborilgan bo'lishi mumkin, avval
        chek raqami bo'yicha E-POS'dan tekshirish kerak."""
        receipt = await self._get(hotel_id, receipt_id)
        if receipt.status in ("done", "skipped"):
            return {**serialize(receipt), "request": None, "verify": False}
        now = _now()
        sent_at = receipt.sent_at
        if sent_at is not None and sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        if receipt.status == "sent" and sent_at and now - sent_at < SENDING_LOCK:
            raise ConflictException(
                "Bu chek hozir boshqa qurilmadan yuborilmoqda", "FISCAL_BUSY"
            )
        verify = receipt.attempts > 0
        old_status = receipt.status
        result = await self.session.execute(
            update(FiscalReceipt)
            .where(
                FiscalReceipt.id == receipt.id,
                FiscalReceipt.status == old_status,
                FiscalReceipt.attempts == receipt.attempts,
            )
            .values(status="sent", attempts=FiscalReceipt.attempts + 1, sent_at=now)
            .execution_options(synchronize_session=False)
        )
        if not result.rowcount:
            raise ConflictException(
                "Bu chek hozir boshqa qurilmadan yuborilmoqda", "FISCAL_BUSY"
            )
        await self.session.refresh(receipt)
        cfg = await self.config(hotel_id)
        return {
            **serialize(receipt),
            "request": {"token": cfg["token"], **(receipt.payload or {})},
            "verify": verify,
        }

    async def complete(self, hotel_id: UUID, receipt_id: UUID, data: dict) -> FiscalReceipt:
        receipt = await self._get(hotel_id, receipt_id)
        if receipt.status == "done":
            return receipt
        if not str(data.get("fiscal_sign") or "").strip():
            raise ValidationException("Fiskal belgi yo'q", "FISCAL_SIGN_REQUIRED")
        receipt.status = "done"
        receipt.error = None
        receipt.terminal_id = str(data.get("terminal_id") or "")[:40] or None
        receipt.receipt_seq = str(data.get("receipt_seq") or "")[:20] or None
        receipt.fiscal_sign = str(data.get("fiscal_sign") or "")[:40]
        receipt.fiscal_datetime = str(data.get("fiscal_datetime") or "")[:20] or None
        receipt.qr_url = str(data.get("qr_url") or "")[:1000] or None
        receipt.completed_at = _now()
        await self.session.flush()
        return receipt

    async def fail(self, hotel_id: UUID, receipt_id: UUID, error: str) -> FiscalReceipt:
        receipt = await self._get(hotel_id, receipt_id)
        if receipt.status in ("done", "skipped"):
            return receipt
        receipt.status = "failed"
        receipt.error = " ".join(str(error or "Noma'lum xato").split())[:1000]
        await self.session.flush()
        return receipt

    async def dismiss(self, hotel_id: UUID, receipt_id: UUID, reason: str) -> FiscalReceipt:
        """Chekni navbatdan olib tashlash (administrator qarori) — to'lov
        fiskallashtirilmagan bo'lib qoladi, sababi yoziladi."""
        receipt = await self._get(hotel_id, receipt_id)
        if receipt.status == "done":
            raise ConflictException("Fiskallashgan chek o'zgartirilmaydi", "FISCAL_DONE")
        receipt.status = "skipped"
        receipt.error = (" ".join(str(reason or "").split()) or "Qo'lda o'tkazib yuborildi")[:1000]
        await self.session.flush()
        return receipt

    # ------------------------------------------------------------ o'qish --

    async def list(
        self, hotel_id: UUID, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict:
        where = [FiscalReceipt.hotel_id == hotel_id]
        if status:
            where.append(FiscalReceipt.status.in_([s for s in status.split(",") if s]))
        total = (
            await self.session.execute(select(func.count()).select_from(FiscalReceipt).where(*where))
        ).scalar() or 0
        rows = (
            await self.session.execute(
                select(FiscalReceipt)
                .where(*where)
                .order_by(FiscalReceipt.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return {"items": [serialize(r) for r in rows], "total": int(total)}

    async def for_target(
        self,
        hotel_id: UUID,
        *,
        reservation_id: UUID | None = None,
        sale_id: UUID | None = None,
    ) -> list[dict]:
        """Bron yoki sotuvning fiskallashgan cheklari (eng yangisi birinchi) —
        chekni qayta chiqarishda QR uchun."""
        where = [FiscalReceipt.hotel_id == hotel_id, FiscalReceipt.status == "done"]
        if sale_id:
            where.append(FiscalReceipt.shop_sale_id == sale_id)
        elif reservation_id:
            where += [
                FiscalReceipt.reservation_id == reservation_id,
                FiscalReceipt.source == SOURCE_RESERVATION,
            ]
        else:
            return []
        rows = (
            await self.session.execute(
                select(FiscalReceipt).where(*where).order_by(FiscalReceipt.completed_at.desc()).limit(20)
            )
        ).scalars().all()
        return [serialize(r) for r in rows]

    # ------------------------------------------------------------- relay --

    async def relay(self, hotel_id: UUID, body: dict) -> dict:
        """"Server orqali" rejimi: so'rov backenddan E-POS manziliga yuboriladi
        (manzil internetdan ko'rinadigan bo'lishi kerak — masalan sinov muhiti)."""
        cfg = await self.config(hotel_id)
        if cfg["transport"] != "server":
            raise ValidationException(
                "Server orqali yuborish bu filialda yoqilmagan", "FISCAL_RELAY_DISABLED"
            )
        method = str(body.get("method") or "")
        if method not in RELAY_METHODS:
            raise ValidationException("Bu usul ruxsat etilmagan", "FISCAL_METHOD_FORBIDDEN")
        assert_public_url(cfg["url"])
        payload = {**body, "token": cfg["token"]}
        try:
            async with httpx.AsyncClient(timeout=40.0, follow_redirects=False) as client:
                response = await client.post(cfg["url"], json=payload)
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("E-POS relay xatosi: %s", exc)
            raise ServiceUnavailableException(
                "E-POS serveriga ulanib bo'lmadi", "FISCAL_UNREACHABLE"
            ) from None


def assert_public_url(url: str) -> None:
    """Server faqat INTERNETDAGI manzilga murojaat qiladi: ichki tarmoq va
    localhost taqiqlangan (serverning o'z xizmatlariga so'rov yuborib bo'lmasin).
    Kassa kompyuteridagi E-POS uchun "brauzerdan" rejimi ishlatiladi."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not host:
        raise ValidationException("E-POS manzili noto'g'ri", "FISCAL_URL_INVALID")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError:
        raise ServiceUnavailableException(
            "E-POS manzili topilmadi", "FISCAL_UNREACHABLE"
        ) from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValidationException(
                "Server orqali rejimda ichki tarmoq manzili ishlatilmaydi — «Brauzerdan» rejimini tanlang",
                "FISCAL_URL_PRIVATE",
            )
