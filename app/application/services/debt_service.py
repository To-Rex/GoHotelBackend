"""Mehmon qarzi — qancha va NIMA UCHUN.

Qarz bitta raqam emas: xodim (va mehmon) qaysi pul to'lanmay qolganini
bilishi kerak. Shu yerda har bron uchun "hisob varag'i" tuziladi:

* **Hisoblangan haqlar** — xona (turar joy), uzaytirilgan muddat, xizmatlar,
  jarimalar. To'lovlar ularni VAQT TARTIBIDA yopadi (avval xona — u bron
  ochilganda hisoblangan, keyin keyingi qo'shilganlar). Yopilmay qolgan
  qism — qarzning SABABI: "Jarima (kech chiqish): 50 000", "Turar joy:
  200 000 qoldi".
* **Do'kon** — bronga yozilgan do'kon savdolarining to'lanmagan qoldig'i.
  Ular bron jamisiga kirmaydi (o'z to'lovlari bor), lekin mehmonning qarzi.

KIRGAN mehmon uchun qarz "chiqishda nima bo'ladi" bo'yicha hisoblanadi —
`ReservationService.check_out` ning aynan o'sha formulasi bilan. Bron
uzaytirilganda pul faqat chiqishda qayta hisoblanadi; shusiz qarz mehmon
ketgandagina paydo bo'lardi — ya'ni juda kech.

Qarz hisobga olinadigan holatlar `debtor_service` dagi kabi: kirgan va
chiqib ketgan bronlar. Kelmagan (tasdiqlangan) bronda faqat do'kon qarzi
bo'lishi mumkin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.reservation_penalty import ReservationPenalty
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_type import RoomType
from app.infrastructure.database.models.service import (
    HotelService,
    ReservationService,
    Service,
)
from app.infrastructure.database.models.shop import ShopSale, ShopSaleItem
from app.infrastructure.database.models.user import User

#: Yaxlitlash xatosi tufayli "1 tiyinlik qarz" chiqmasligi uchun
MIN_DEBT = 0.01
#: Qayta hisob farqi shundan kichik bo'lsa e'tiborga olinmaydi (so'm)
RECALC_TOLERANCE = 0.5

#: Bron qarzi hisoblanadigan holatlar — mehmon xonadan foydalangan
STAY_STATUSES = ("CHECKED_IN", "CHECKED_OUT")
#: Do'kon qarzi bo'lishi mumkin bo'lgan holatlar (savdo shu holatlarda yoziladi)
SHOP_STATUSES = ("CONFIRMED", "CHECKED_IN", "CHECKED_OUT")

#: Sabab turlari — mijoz ilovalari shu kalitlar bo'yicha matn chiqaradi
KIND_ROOM = "room"
KIND_EXTENSION = "extension"
KIND_SERVICE = "service"
KIND_PENALTY = "penalty"
KIND_SHOP = "shop"

PENALTY_LABELS = {
    "LATE_CHECKOUT": "kech chiqish",
    "DAMAGE": "shikast",
    "OTHER": "boshqa",
}
KIND_LABELS = {
    KIND_ROOM: "Turar joy",
    KIND_EXTENSION: "Chiqishda qayta hisob",
    KIND_SERVICE: "Xizmat",
    KIND_PENALTY: "Jarima",
    KIND_SHOP: "Do'kon",
}


def money(value) -> float:
    return round(float(value or 0), 2)


def fmt_sum(value: float) -> str:
    """50000 → "50 000" (xabarnomalar matni uchun)."""
    return f"{round(float(value or 0)):,}".replace(",", " ")


@dataclass
class Charge:
    """Bitta hisoblangan haq va uning to'lanmagan qismi."""

    kind: str
    charged: float
    details: dict = field(default_factory=dict)
    unpaid: float = 0.0

    def as_dict(self) -> dict:
        return {"kind": self.kind, "charged": money(self.charged),
                "amount": money(self.unpaid), **self.details}


def allocate(charges: list[Charge], paid: float) -> list[Charge]:
    """To'lovni haqlarga vaqt tartibida taqsimlaydi; har haqning `unpaid`
    qismini to'ldiradi. Manfiy yoki nol haq e'tiborga olinmaydi."""
    left = max(float(paid or 0), 0.0)
    for charge in charges:
        amount = max(float(charge.charged or 0), 0.0)
        covered = min(amount, left)
        left -= covered
        charge.unpaid = round(amount - covered, 2)
    return charges


def reason_label(item: dict) -> str:
    """Bitta sababning qisqa o'zbekcha matni (xabarnomalar uchun)."""
    kind = item.get("kind")
    label = KIND_LABELS.get(kind, kind or "")
    if kind == KIND_PENALTY:
        sub = PENALTY_LABELS.get(item.get("penalty_kind") or "", "")
        note = (item.get("note") or "").strip()
        extra = ", ".join(p for p in (sub, note) if p)
        return f"{label} ({extra})" if extra else label
    if kind == KIND_SERVICE and item.get("name"):
        return f"{label}: {item['name']}"
    if kind == KIND_SHOP and item.get("products"):
        return f"{label}: {item['products']}"
    if kind == KIND_ROOM and item.get("room_number"):
        return f"{label} ({item['room_number']}-xona)"
    return label


def reasons_text(items: list[dict], limit: int = 3) -> str:
    """"Jarima (kech chiqish) 50 000 · Do'kon: Cola 12 000" — ro'yxat uchun."""
    parts = [f"{reason_label(i)} {fmt_sum(i.get('amount'))}" for i in items[:limit]]
    if len(items) > limit:
        parts.append(f"+{len(items) - limit}")
    return " · ".join(parts)


class DebtService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ----------------------------------------------------------- yuklash --
    async def _rooms(self, reservations: list[Reservation]) -> dict:
        room_ids = {r.room_id for r in reservations if r.room_id}
        if not room_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Room.id, Room.room_number, RoomType.base_price)
                .join(RoomType, RoomType.id == Room.room_type_id, isouter=True)
                .where(Room.id.in_(room_ids))
            )
        ).all()
        return {row.id: (row.room_number, float(row.base_price or 0)) for row in rows}

    async def _penalties(self, ids: list[UUID]) -> dict[UUID, list[ReservationPenalty]]:
        out: dict[UUID, list[ReservationPenalty]] = {}
        if not ids:
            return out
        rows = (
            await self.session.execute(
                select(ReservationPenalty)
                .where(
                    ReservationPenalty.reservation_id.in_(ids),
                    ReservationPenalty.voided_at.is_(None),
                )
                .order_by(ReservationPenalty.created_at.asc())
            )
        ).scalars().all()
        for p in rows:
            out.setdefault(p.reservation_id, []).append(p)
        return out

    async def _services(self, ids: list[UUID]) -> dict[UUID, list[dict]]:
        """Xizmatlar va ular bron jamisiga kirganmi (hisob-faktura qatori bor)."""
        out: dict[UUID, list[dict]] = {}
        if not ids:
            return out
        rows = (
            await self.session.execute(
                select(ReservationService, Service.name)
                .join(HotelService, HotelService.id == ReservationService.hotel_service_id,
                      isouter=True)
                .join(Service, Service.id == HotelService.service_id, isouter=True)
                .where(ReservationService.reservation_id.in_(ids))
                .order_by(ReservationService.created_at.asc())
            )
        ).all()
        if not rows:
            return out
        service_ids = [row[0].id for row in rows]
        billed = set(
            (
                await self.session.execute(
                    select(InvoiceLineItem.reference_id).where(
                        InvoiceLineItem.line_type == "SERVICE_CHARGE",
                        InvoiceLineItem.reference_id.in_(service_ids),
                    )
                )
            ).scalars().all()
        )
        for svc, name in rows:
            out.setdefault(svc.reservation_id, []).append(
                {
                    "id": svc.id,
                    "name": name,
                    "quantity": svc.quantity,
                    "total": money(svc.total_price),
                    "date": svc.service_date,
                    "billed": svc.id in billed,
                }
            )
        return out

    async def _shop(self, ids: list[UUID]) -> dict[UUID, list[dict]]:
        """Bronga yozilgan, to'liq to'lanmagan do'kon savdolari."""
        out: dict[UUID, list[dict]] = {}
        if not ids:
            return out
        sales = (
            await self.session.execute(
                select(ShopSale, User.first_name, User.last_name)
                .join(User, User.id == ShopSale.created_by, isouter=True)
                .where(ShopSale.reservation_id.in_(ids), ShopSale.status == "PENDING")
                .order_by(ShopSale.created_at.asc())
            )
        ).all()
        if not sales:
            return out
        items = (
            await self.session.execute(
                select(ShopSaleItem.sale_id, ShopSaleItem.product_name, ShopSaleItem.quantity)
                .where(ShopSaleItem.sale_id.in_([s[0].id for s in sales]))
            )
        ).all()
        by_sale: dict[UUID, list[str]] = {}
        for sale_id, name, qty in items:
            by_sale.setdefault(sale_id, []).append(f"{name} ×{qty}")
        for sale, first, last in sales:
            remaining = round(money(sale.total_amount) - money(sale.paid_amount), 2)
            if remaining <= MIN_DEBT:
                continue
            out.setdefault(sale.reservation_id, []).append(
                {
                    "sale_id": sale.id,
                    "total": money(sale.total_amount),
                    "paid": money(sale.paid_amount),
                    "remaining": remaining,
                    "products": ", ".join(by_sale.get(sale.id, [])),
                    "created_at": sale.created_at,
                    "created_by_name": " ".join(p for p in (first, last) if p) or None,
                }
            )
        return out

    # --------------------------------------------------------- hisob-kitob --
    async def _projected_room(self, res: Reservation, base_price: float) -> dict:
        """Chiqishda hisoblanadigan xona haqi — `check_out` bilan bir xil."""
        from app.application.services.daily_unit import reservation_daily_unit, unit_price
        from app.application.services.reservation_service import (
            ReservationService as Reservations,
        )

        calc = Reservations(self.session)
        booking_type = res.booking_type or "DAILY"
        price = unit_price(base_price, booking_type, reservation_daily_unit(res))
        room_charge, duration = await calc._calculate_price(
            price, booking_type, res.check_in_date, res.check_out_date,
            res.check_in_datetime, res.check_out_datetime,
        )
        discount, _ = await calc._compute_discount(
            room_charge, res.discount_amount or 0, res.discount_percent or 0
        )
        move_discount = await calc._effective_move_discount(res, room_charge)
        return {
            "net": max(room_charge - discount - move_discount, 0.0),
            "duration": duration,
            "unit_price": price,
            "discount": round(discount + move_discount, 2),
        }

    async def _build(
        self,
        res: Reservation,
        room: tuple[str | None, float],
        penalties: list[ReservationPenalty],
        services: list[dict],
        shop: list[dict],
        today: date,
    ) -> dict:
        room_number, base_price = room
        stays = res.status in STAY_STATUSES
        total = money(res.total_amount)
        paid = money(res.paid_amount)
        penalty_total = money(res.penalty_amount)
        billed_services = sum(s["total"] for s in services if s["billed"])

        charges: list[Charge] = []
        expected_total = 0.0
        projected = False
        if stays:
            booked_room = max(total - penalty_total - billed_services, 0.0)
            room_details = {
                "room_number": room_number,
                "booking_type": res.booking_type,
                "check_in_date": res.check_in_date,
                "check_out_date": res.check_out_date,
            }
            room_net = booked_room
            extension = 0.0
            if res.status == "CHECKED_IN":
                calc = await self._projected_room(res, base_price)
                room_details.update(
                    nights=calc["duration"], unit_price=calc["unit_price"],
                    discount=calc["discount"],
                )
                delta = calc["net"] - booked_room
                if delta > RECALC_TOLERANCE:
                    extension = round(delta, 2)
                    projected = True
                elif delta < -RECALC_TOLERANCE:
                    room_net = calc["net"]
                    projected = True
            charges.append(Charge(KIND_ROOM, room_net, room_details))
            if extension:
                charges.append(Charge(KIND_EXTENSION, extension, {
                    "room_number": room_number,
                    "check_out_date": res.check_out_date,
                }))
            for svc in services:
                charges.append(Charge(KIND_SERVICE, svc["total"], {
                    "name": svc["name"], "quantity": svc["quantity"],
                    "date": svc["date"], "billed": svc["billed"],
                }))
            listed = 0.0
            for p in penalties:
                listed += money(p.amount)
                charges.append(Charge(KIND_PENALTY, money(p.amount), {
                    "penalty_kind": p.kind, "note": p.note, "date": p.penalty_date,
                }))
            # Jarima jamisi qatorlar bilan mos kelmasa (eski yozuvlar) — farqi
            # ham ko'rinsin, aks holda qarz "sababsiz" qolardi
            if penalty_total - listed > RECALC_TOLERANCE:
                charges.append(Charge(KIND_PENALTY, round(penalty_total - listed, 2),
                                      {"penalty_kind": "OTHER", "note": None}))
            allocate(charges, paid)
            expected_total = round(sum(max(c.charged, 0) for c in charges), 2)

        reservation_debt = round(max(expected_total - paid, 0.0), 2) if stays else 0.0
        shop_debt = round(sum(s["remaining"] for s in shop), 2)
        items = [c.as_dict() for c in charges if c.unpaid > MIN_DEBT]
        for sale in shop:
            items.append({
                "kind": KIND_SHOP, "charged": sale["total"], "amount": sale["remaining"],
                "products": sale["products"], "created_at": sale["created_at"],
                "created_by_name": sale["created_by_name"], "sale_id": sale["sale_id"],
            })

        overdue_days = None
        if res.check_out_date and res.check_out_date < today and (reservation_debt or shop_debt):
            overdue_days = (today - res.check_out_date).days

        acknowledged = None
        if getattr(res, "debt_ack_at", None):
            acknowledged = {
                "at": res.debt_ack_at,
                "by": res.debt_ack_by,
                "note": res.debt_ack_note,
                "amount": money(res.debt_ack_amount),
            }

        return {
            "reservation_id": res.id,
            "status": res.status,
            "total_amount": total,
            "expected_total": expected_total if stays else total,
            "paid_amount": paid,
            "projected": projected,
            "reservation_debt": reservation_debt,
            "shop_debt": shop_debt,
            "total_debt": round(reservation_debt + shop_debt, 2),
            "items": items,
            "charges": [c.as_dict() for c in charges],
            "shop_sales": shop,
            "overdue_days": overdue_days,
            "acknowledged": acknowledged,
        }

    # ---------------------------------------------------------- tashqi API --
    async def breakdowns(
        self, reservations: list[Reservation], today: date | None = None
    ) -> dict[UUID, dict]:
        """Bir nechta bron uchun hisob varag'i — ro'yxatlar shu bilan."""
        if not reservations:
            return {}
        # Mehmonxonaning mahalliy kuni (server UTC da ishlaydi)
        today = today or local_now().date()
        ids = [r.id for r in reservations]
        rooms = await self._rooms(reservations)
        penalties = await self._penalties(ids)
        services = await self._services(ids)
        shop = await self._shop(ids)
        out = {}
        for res in reservations:
            out[res.id] = await self._build(
                res,
                rooms.get(res.room_id, (None, 0.0)),
                penalties.get(res.id, []),
                services.get(res.id, []),
                shop.get(res.id, []),
                today,
            )
        return out

    async def breakdown(self, reservation: Reservation, today: date | None = None) -> dict:
        return (await self.breakdowns([reservation], today))[reservation.id]

    async def detail(self, reservation: Reservation, today: date | None = None) -> dict:
        """Bron oynasi uchun: hisob varag'i + to'lovlar tarixi va kim qabul qilgan."""
        data = await self.breakdown(reservation, today)
        rows = (
            await self.session.execute(
                select(Payment, User.first_name, User.last_name)
                .join(Invoice, Invoice.id == Payment.invoice_id)
                .join(User, User.id == Payment.created_by, isouter=True)
                .where(Invoice.reservation_id == reservation.id)
                .order_by(Payment.created_at.asc())
            )
        ).all()
        data["payments"] = [
            {
                "amount": money(p.amount),
                "method": p.payment_method,
                "date": p.payment_date,
                "created_at": p.created_at,
                "created_by_name": " ".join(x for x in (first, last) if x) or None,
                "notes": p.notes,
            }
            for p, first, last in rows
        ]
        ack = data.get("acknowledged")
        if ack and ack.get("by"):
            user = await self.session.get(User, ack["by"])
            if user is not None:
                ack["by_name"] = " ".join(x for x in (user.first_name, user.last_name) if x) or None
        return data

    async def shop_reservation_ids(self, hotel_id: UUID) -> set[UUID]:
        """Do'kon qarzi bor bronlar (savdo to'liq to'lanmagan)."""
        rows = (
            await self.session.execute(
                select(ShopSale.reservation_id).where(
                    ShopSale.hotel_id == hotel_id,
                    ShopSale.reservation_id.is_not(None),
                    ShopSale.status == "PENDING",
                    ShopSale.total_amount > ShopSale.paid_amount + MIN_DEBT,
                )
            )
        ).scalars().all()
        return {r for r in rows if r}


def local_now() -> datetime:
    from datetime import timedelta, timezone

    from app.core.config import settings

    return datetime.now(timezone.utc) + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
