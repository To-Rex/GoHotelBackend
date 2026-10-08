"""Bron jarimalari: kech chiqish, shikast (buzilgan narsa), boshqa.

Qoidalar:

* Jarima faqat mehmon turgan yoki chiqib ketgan bronga yoziladi
  (`CHECKED_IN`, `CHECKED_OUT`) — buzilgan narsa chiqib ketgandan keyin
  topilishi mumkin.
* Jarima bron summasiga DARHOL qo'shiladi (`reservations.penalty_amount`
  va `total_amount`), hisob-faktura bo'lsa — alohida "PENALTY" qatori.
  To'lanmasa qarz bo'lib ko'rinadi va mavjud "qo'shimcha to'lov" oqimi
  (`settle-payment`) bilan olinadi; naqd olinsa kassa smenasiga o'zi tushadi.
* Bron summasi qayta hisoblanadigan joylar (chiqish, ko'chirish,
  hisob-faktura yaratish) `penalty_amount` ni qo'shadi va PENALTY
  qatorlari yo'q bo'lsa qo'shadi (`ensure_penalty_lines`).
* O'chirilmaydi — bekor qilinadi (kim, qachon, nima uchun). Bekor qilishni
  faqat administrator yoki menejer (`shift.force_close`) qiladi.
* Kech chiqish summasi uchun sozlama (`branches.settings["penalty"]`):
  soatiga summa (0 — o'chiq) va imtiyozli daqiqalar. Server taklif qiladi,
  xodim summani o'zgartira oladi.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.reservation_penalty import ReservationPenalty
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.user import User

PENALTY_KINDS = ("LATE_CHECKOUT", "DAMAGE", "OTHER")
PENALTY_LINE_TYPE = "PENALTY"
PENALTY_REFERENCE = "reservation_penalty"
#: Jarima yoziladigan bron holatlari
PENALTY_STATUSES = ("CHECKED_IN", "CHECKED_OUT")
PENALTY_SETTINGS_KEY = "penalty"
PENALTY_DEFAULTS = {"late_hourly_amount": 0.0, "grace_minutes": 0}
MAX_AMOUNT = 1_000_000_000

KIND_LABELS = {
    "LATE_CHECKOUT": "Kech chiqish",
    "DAMAGE": "Shikast",
    "OTHER": "Boshqa",
}


def _number(value, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return 0.0
    return min(number, maximum) if maximum is not None else number


def resolve_penalty_settings(hotel_settings: dict | None) -> dict:
    """Saqlangan sozlama; yo'q yoki buzuq — o'chiq (avtomatik taklif yo'q)."""
    saved = (hotel_settings or {}).get(PENALTY_SETTINGS_KEY)
    if not isinstance(saved, dict):
        return dict(PENALTY_DEFAULTS)
    return {
        "late_hourly_amount": _number(saved.get("late_hourly_amount"), MAX_AMOUNT),
        "grace_minutes": int(_number(saved.get("grace_minutes"), 600)),
    }


def planned_checkout(reservation) -> datetime | None:
    """Mehmon CHIQISHI kerak bo'lgan vaqt (UTC).

    Soatlik bronda — `check_out_datetime`; kunlik bronda — chiqish kuni
    mahalliy `DEFAULT_CHECKOUT_HOUR` (avtomatik chiqish bilan bir xil).
    """
    if (reservation.booking_type or "DAILY") == "HOURLY" and reservation.check_out_datetime:
        value = reservation.check_out_datetime
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not reservation.check_out_date:
        return None
    hour = max(0, min(23, settings.DEFAULT_CHECKOUT_HOUR))
    local = datetime.combine(reservation.check_out_date, time(hour, 0))
    return (local - timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)).replace(tzinfo=timezone.utc)


def late_checkout_suggestion(reservation, rules: dict, now: datetime) -> dict | None:
    """Kech chiqish jarimasi taklifi: kechikkan soatlar × soatiga summa.

    Mehmon hali ichkarida bo'lsa — hozirgacha; "mehmon chiqmoqda" deb
    belgilangan bo'lsa — o'sha vaqtgacha. Sozlama o'chiq yoki kechikish
    yo'q bo'lsa — None. Imtiyozli daqiqalar ichida — jarima yo'q; oshsa
    kechikish TO'LIQ soatlarga yuqoriga yaxlitlanadi.
    """
    hourly = _number(rules.get("late_hourly_amount"))
    if hourly <= 0:
        return None
    due = planned_checkout(reservation)
    if due is None:
        return None
    if reservation.status == "CHECKED_IN":
        moment = now
    elif reservation.status == "CHECKED_OUT" and reservation.checkout_requested_at:
        moment = reservation.checkout_requested_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    else:
        return None
    late_minutes = (moment - due).total_seconds() / 60
    grace = int(_number(rules.get("grace_minutes"), 600))
    if late_minutes <= grace:
        return None
    hours = max(1, math.ceil(late_minutes / 60 - 1e-9))
    return {
        "late_minutes": int(late_minutes),
        "hours": hours,
        "hourly_amount": hourly,
        "amount": float(round(hours * hourly)),
        "due_at": due.isoformat(),
    }


def payment_status_for(total: float, paid: float) -> str:
    if paid <= 0:
        return "UNPAID"
    if paid >= total - 0.01:
        return "PAID"
    return "PARTIALLY_PAID"


def _invoice_status_for(invoice) -> str:
    total = float(invoice.total_amount or 0)
    paid = float(invoice.paid_amount or 0)
    if paid > 0 and paid >= total - 0.01:
        return "PAID"
    if paid > 0:
        return "PARTIALLY_PAID"
    # To'lanmagan — avvalgi holat saqlanadi (DRAFT/ISSUED)
    return invoice.status if invoice.status not in ("PAID", "PARTIALLY_PAID") else "ISSUED"


def penalty_line_description(kind: str, note: str | None) -> str:
    label = KIND_LABELS.get(kind, kind)
    text = f"Jarima: {label}"
    if note:
        text = f"{text} — {note.strip()}"
    return text[:500]


async def active_penalties(session: AsyncSession, reservation_id: UUID) -> list[ReservationPenalty]:
    return list(
        (
            await session.execute(
                select(ReservationPenalty)
                .where(
                    ReservationPenalty.reservation_id == reservation_id,
                    ReservationPenalty.voided_at.is_(None),
                )
                .order_by(ReservationPenalty.created_at)
            )
        ).scalars().all()
    )


async def ensure_penalty_lines(session: AsyncSession, invoice, reservation) -> None:
    """Hisob-fakturada har faol jarima uchun PENALTY qatori bo'lsin.

    Qatorlar summaga (invoice.total_amount) TEGMAYDI — jami summani
    chaqiruvchi o'zi hisoblaydi (`penalty_amount` bilan). Jarima yo'q
    bronda hech qanday so'rov qilinmaydi.
    """
    if float(getattr(reservation, "penalty_amount", 0) or 0) <= 0:
        return
    penalties = await active_penalties(session, reservation.id)
    if not penalties:
        return
    existing = {
        row
        for row in (
            await session.execute(
                select(InvoiceLineItem.reference_id).where(
                    InvoiceLineItem.invoice_id == invoice.id,
                    InvoiceLineItem.line_type == PENALTY_LINE_TYPE,
                )
            )
        ).scalars().all()
    }
    for penalty in penalties:
        if penalty.id in existing:
            continue
        session.add(
            InvoiceLineItem(
                invoice_id=invoice.id,
                hotel_id=invoice.hotel_id,
                description=penalty_line_description(penalty.kind, penalty.note),
                line_type=PENALTY_LINE_TYPE,
                reference_type=PENALTY_REFERENCE,
                reference_id=penalty.id,
                quantity=1,
                unit_price=penalty.amount,
                total_price=penalty.amount,
                created_at=datetime.now(timezone.utc),
            )
        )


def can_void(current_user: dict) -> bool:
    if current_user.get("user_type") in ("ADMIN", "SUPER_ADMIN"):
        return True
    return "shift.force_close" in (current_user.get("permissions") or [])


class ReservationPenaltyService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _reservation(self, hotel_id: UUID, reservation_id: UUID) -> Reservation:
        reservation = await self.session.get(Reservation, reservation_id)
        if reservation is None or reservation.hotel_id != hotel_id or getattr(reservation, "is_deleted", False):
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        return reservation

    async def _invoice(self, hotel_id: UUID, reservation_id: UUID):
        return (
            await self.session.execute(
                select(Invoice)
                .where(Invoice.reservation_id == reservation_id, Invoice.hotel_id == hotel_id)
                .order_by(Invoice.created_at.desc())
                .limit(1)
            )
        ).scalars().first()

    async def _names(self, ids: set) -> dict:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        rows = (await self.session.execute(select(User).where(User.id.in_(ids)))).scalars().all()
        return {u.id: f"{u.first_name} {u.last_name}".strip() for u in rows}

    def _serialize(self, p: ReservationPenalty, names: dict) -> dict:
        return {
            "id": str(p.id),
            "reservation_id": str(p.reservation_id),
            "kind": p.kind,
            "amount": float(p.amount or 0),
            "note": p.note,
            "penalty_date": p.penalty_date.isoformat() if p.penalty_date else None,
            "created_by": str(p.created_by) if p.created_by else None,
            "created_by_name": names.get(p.created_by),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "voided_at": p.voided_at.isoformat() if p.voided_at else None,
            "voided_by_name": names.get(p.voided_by),
            "void_reason": p.void_reason,
        }

    # ------------------------------------------------------------ ro'yxat --

    async def list_for_reservation(self, hotel_id: UUID, reservation_id: UUID) -> dict:
        reservation = await self._reservation(hotel_id, reservation_id)
        rows = list(
            (
                await self.session.execute(
                    select(ReservationPenalty)
                    .where(ReservationPenalty.reservation_id == reservation_id)
                    .order_by(ReservationPenalty.created_at.desc())
                )
            ).scalars().all()
        )
        names = await self._names({r.created_by for r in rows} | {r.voided_by for r in rows})
        from app.application.services.branch_settings import settings_owner

        hotel = await settings_owner(self.session, hotel_id)
        rules = resolve_penalty_settings(hotel.settings if hotel else None)
        return {
            "items": [self._serialize(r, names) for r in rows],
            "total": float(reservation.penalty_amount or 0),
            "can_add": reservation.status in PENALTY_STATUSES,
            "late_suggestion": late_checkout_suggestion(
                reservation, rules, datetime.now(timezone.utc)
            ),
        }

    # ----------------------------------------------------------- qo'shish --

    async def add(
        self,
        hotel_id: UUID,
        reservation_id: UUID,
        kind: str,
        amount: float,
        note: str | None,
        user_id: UUID,
    ) -> dict:
        reservation = await self._reservation(hotel_id, reservation_id)
        if reservation.status not in PENALTY_STATUSES:
            raise ValidationException(
                "Jarima faqat mehmon turgan yoki chiqib ketgan bronga yoziladi",
                "PENALTY_INVALID_STATUS",
            )
        kind = (kind or "").upper()
        if kind not in PENALTY_KINDS:
            raise ValidationException("Noma'lum jarima turi", "PENALTY_INVALID_KIND")
        value = float(round(_number(amount, MAX_AMOUNT)))
        if value <= 0:
            raise ValidationException("Jarima summasi 0 dan katta bo'lsin", "PENALTY_INVALID_AMOUNT")
        clean_note = (note or "").strip() or None

        penalty = ReservationPenalty(
            hotel_id=hotel_id,
            reservation_id=reservation.id,
            kind=kind,
            amount=value,
            note=clean_note,
            penalty_date=date.today(),
            created_by=user_id,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(penalty)
        await self.session.flush()

        await self._apply_delta(hotel_id, reservation, value, add_line=penalty)
        await self.session.flush()
        names = await self._names({user_id})
        return {
            "penalty": self._serialize(penalty, names),
            "reservation_total": float(reservation.total_amount or 0),
            "penalty_total": float(reservation.penalty_amount or 0),
            "payment_status": reservation.payment_status,
        }

    # ------------------------------------------------------ bekor qilish --

    async def void(
        self,
        hotel_id: UUID,
        reservation_id: UUID,
        penalty_id: UUID,
        reason: str | None,
        current_user: dict,
    ) -> dict:
        if not can_void(current_user):
            raise ForbiddenException(
                "Jarimani faqat administrator yoki menejer bekor qila oladi",
                "PENALTY_VOID_FORBIDDEN",
            )
        reservation = await self._reservation(hotel_id, reservation_id)
        penalty = await self.session.get(ReservationPenalty, penalty_id)
        if penalty is None or penalty.reservation_id != reservation.id:
            raise NotFoundException("Jarima topilmadi", "PENALTY_NOT_FOUND")
        if penalty.voided_at is not None:
            raise ValidationException("Jarima allaqachon bekor qilingan", "PENALTY_ALREADY_VOIDED")

        user_id = current_user["id"]
        if not isinstance(user_id, UUID):
            user_id = UUID(str(user_id))
        penalty.voided_at = datetime.now(timezone.utc)
        penalty.voided_by = user_id
        penalty.void_reason = (reason or "").strip() or None

        await self._apply_delta(hotel_id, reservation, -float(penalty.amount or 0), remove_line=penalty)
        await self.session.flush()
        names = await self._names({penalty.created_by, user_id})
        return {
            "penalty": self._serialize(penalty, names),
            "reservation_total": float(reservation.total_amount or 0),
            "penalty_total": float(reservation.penalty_amount or 0),
            "payment_status": reservation.payment_status,
        }

    async def _apply_delta(
        self,
        hotel_id: UUID,
        reservation: Reservation,
        delta: float,
        add_line: ReservationPenalty | None = None,
        remove_line: ReservationPenalty | None = None,
    ) -> None:
        """Bron va hisob-faktura summalarini jarima bo'yicha yangilaydi."""
        reservation.penalty_amount = max(0.0, float(reservation.penalty_amount or 0) + delta)
        reservation.total_amount = max(0.0, float(reservation.total_amount or 0) + delta)
        reservation.payment_status = payment_status_for(
            float(reservation.total_amount or 0), float(reservation.paid_amount or 0)
        )

        invoice = await self._invoice(hotel_id, reservation.id)
        if invoice is None:
            # Hisob-faktura hali yo'q — u yaratilganda (to'lov, chiqish)
            # jarimalar `ensure_penalty_lines` bilan qo'shiladi
            return
        invoice.total_amount = max(0.0, float(invoice.total_amount or 0) + delta)
        if add_line is not None:
            self.session.add(
                InvoiceLineItem(
                    invoice_id=invoice.id,
                    hotel_id=hotel_id,
                    description=penalty_line_description(add_line.kind, add_line.note),
                    line_type=PENALTY_LINE_TYPE,
                    reference_type=PENALTY_REFERENCE,
                    reference_id=add_line.id,
                    quantity=1,
                    unit_price=add_line.amount,
                    total_price=add_line.amount,
                    created_at=datetime.now(timezone.utc),
                )
            )
        if remove_line is not None:
            lines = (
                await self.session.execute(
                    select(InvoiceLineItem).where(
                        InvoiceLineItem.invoice_id == invoice.id,
                        InvoiceLineItem.line_type == PENALTY_LINE_TYPE,
                        InvoiceLineItem.reference_id == remove_line.id,
                    )
                )
            ).scalars().all()
            for line in lines:
                await self.session.delete(line)
        invoice.status = _invoice_status_for(invoice)

    # -------------------------------------------------------- jurnal --

    async def journal(self, hotel_id: UUID, date_from: date | None, date_to: date | None) -> dict:
        """Davrdagi jarimalar (bekor qilinganlar ham — alohida belgilangan)
        va faollari bo'yicha jamlanma, turlar kesimida."""
        stmt = (
            select(ReservationPenalty, Reservation.reservation_number, Room.room_number, Guest.first_name, Guest.last_name)
            .join(Reservation, Reservation.id == ReservationPenalty.reservation_id)
            .join(Room, Room.id == Reservation.room_id, isouter=True)
            .join(Guest, Guest.id == Reservation.guest_id, isouter=True)
            .where(ReservationPenalty.hotel_id == hotel_id)
        )
        if date_from:
            stmt = stmt.where(ReservationPenalty.penalty_date >= date_from)
        if date_to:
            stmt = stmt.where(ReservationPenalty.penalty_date <= date_to)
        rows = (await self.session.execute(stmt.order_by(ReservationPenalty.created_at.desc()))).all()
        names = await self._names({r[0].created_by for r in rows} | {r[0].voided_by for r in rows})

        items = []
        total = 0.0
        count = 0
        by_kind = {k: {"total": 0.0, "count": 0} for k in PENALTY_KINDS}
        for penalty, number, room_number, first, last in rows:
            item = self._serialize(penalty, names)
            item["reservation_number"] = number
            item["room_number"] = room_number
            item["guest_name"] = " ".join(p for p in (first, last) if p).strip() or None
            items.append(item)
            if penalty.voided_at is None:
                total += float(penalty.amount or 0)
                count += 1
                bucket = by_kind.setdefault(penalty.kind, {"total": 0.0, "count": 0})
                bucket["total"] += float(penalty.amount or 0)
                bucket["count"] += 1
        return {
            "summary": {"total": total, "count": count, "by_kind": by_kind},
            "items": items,
        }


async def penalty_totals(session: AsyncSession, hotel_id: UUID | None, date_from, date_to) -> dict:
    """Moliya yig'masi uchun: davrdagi faol jarimalar summasi va soni."""
    stmt = select(
        func.coalesce(func.sum(ReservationPenalty.amount), 0),
        func.count(ReservationPenalty.id),
    ).where(ReservationPenalty.voided_at.is_(None))
    if hotel_id is not None:
        stmt = stmt.where(ReservationPenalty.hotel_id == hotel_id)
    if date_from:
        stmt = stmt.where(ReservationPenalty.penalty_date >= date_from)
    if date_to:
        stmt = stmt.where(ReservationPenalty.penalty_date <= date_to)
    total, count = (await session.execute(stmt)).one()
    return {"penalty_total": float(total or 0), "penalty_count": int(count or 0)}
