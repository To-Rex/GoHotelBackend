"""Qarz eslatmalari — qarz unutilib qolmasin.

Kim oladi: mehmonxona administratorlari va to'lov qabul qila oladigan
xodimlar (`finance.payment.create`). Xabar bazaga yoziladi va telefonga
push bo'lib boradi (mobil ilova).

Uch xil eslatma (`AutomationService.run_tick` dan, har necha daqiqada):

* **Qarz bilan chiqib ketdi** — bron qarzi bilan yopilgan zahoti (qo'lda,
  farrosh tozalab yakunlaganda yoki avtomatik). Har bron uchun bir marta.
  Faqat oxirgi sutkada yopilganlar: ishga tushirilganda eski qarzlar
  bo'yicha xabarlar yog'ilib ketmasin.
* **Bugun chiqadi, qarzi bor** — kirgan mehmon chiqish kuni (soatlikda —
  chiqishiga bir soat qolganda). Har bron uchun bir marta.
* **Qarzdorlar ro'yxati** — sozlamadagi oraliqda (standart 2 soat), faqat
  kunduzi (08:00–22:00): nechta, jami qancha va eng kattalari SABABI bilan.

Resepsiya qarzni tasdiqlab mehmonni chiqarganda va farrosh qarzdor
mehmonning xonasini tozalashni boshlaganda xabar darhol ketadi
(:func:`notify_checkout_with_debt`).

Takrorlanmaslik bildirishnomalar jadvalining o'zi orqali: shu turdagi
xabar shu bron (yoki ro'yxat uchun — shu oraliqda shu mehmonxona) bo'yicha
allaqachon yozilganmi. Alohida holat jadvali kerak emas.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.debt_service import (
    MIN_DEBT,
    DebtService,
    fmt_sum,
    local_now,
    reason_label,
    reasons_text,
)
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.notification import Notification
from app.infrastructure.database.models.permission import Permission, UserPermission
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.shop import ShopSale
from app.infrastructure.database.models.user import User

logger = logging.getLogger(__name__)

DEBT_SETTINGS_KEY = "debt_reminders"
DEFAULT_INTERVAL_MINUTES = 120
ALLOWED_INTERVALS = (30, 60, 120, 240, 480)
DEFAULT_DEBT_SETTINGS = {"enabled": True, "interval_minutes": DEFAULT_INTERVAL_MINUTES}

#: Bildirishnoma turlari (`notifications.entity_type`)
TYPE_LEFT = "debt_left"
TYPE_SOON = "debt_checkout_soon"
TYPE_DIGEST = "debt_digest"
TYPE_ACK = "debt_checkout_ack"
TYPE_CLEANER = "debt_cleaner_checkout"

#: Ro'yxat faqat kunduzi (mahalliy vaqt)
DAY_START_HOUR = 8
DAY_END_HOUR = 22
#: "Qarz bilan chiqdi" xabari — shu oraliqda yopilgan bronlar uchun
LEFT_WINDOW_HOURS = 24
#: Soatlik bronda chiqishga shuncha qolganda ogohlantiriladi
SOON_MINUTES = 60
#: Tekshiruv tik'dan siyrakroq — har daqiqada butun qarz ro'yxati behuda
PASS_SECONDS = 300

_last_pass = 0.0


def resolve_debt_settings(settings: dict | None) -> dict:
    saved = (settings or {}).get(DEBT_SETTINGS_KEY) or {}
    enabled = saved.get("enabled")
    interval = saved.get("interval_minutes")
    return {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "interval_minutes": interval if interval in ALLOWED_INTERVALS else DEFAULT_INTERVAL_MINUTES,
    }


def _name(first, last) -> str | None:
    return " ".join(p for p in (first, last) if p).strip() or None


async def recipients(session: AsyncSession, hotel_id: UUID) -> set[UUID]:
    """Administratorlar va to'lov qabul qila oladigan xodimlar."""
    admins = await session.execute(
        select(User.id).where(
            User.hotel_id == hotel_id,
            User.user_type == "ADMIN",
            User.is_deleted.is_(False),
            User.status == "ACTIVE",
        )
    )
    cashiers = await session.execute(
        select(User.id)
        .join(UserPermission, UserPermission.user_id == User.id)
        .join(Permission, Permission.id == UserPermission.permission_id)
        .where(
            User.hotel_id == hotel_id,
            User.user_type == "EMPLOYEE",
            User.is_deleted.is_(False),
            User.status == "ACTIVE",
            Permission.code == "finance.payment.create",
        )
    )
    return {r[0] for r in admins.all()} | {r[0] for r in cashiers.all()}


async def _send(
    session: AsyncSession,
    hotel_id: UUID,
    targets: set[UUID],
    title: str,
    body: str,
    entity_type: str,
    entity_id: UUID | None,
) -> int:
    from app.application.services.notification_service import NotificationService

    notifier = NotificationService(session)
    sent = 0
    for user_id in targets:
        try:
            await notifier.notify(
                hotel_id=hotel_id, user_id=user_id, title=title, body=body,
                entity_type=entity_type, entity_id=entity_id,
            )
            sent += 1
        except Exception:
            logger.exception("Qarz eslatmasini yuborib bo'lmadi (user %s)", user_id)
    return sent


async def _already_sent(
    session: AsyncSession, hotel_id: UUID, entity_type: str, entity_id: UUID | None = None,
    since: datetime | None = None,
) -> bool:
    stmt = select(Notification.id).where(
        Notification.hotel_id == hotel_id, Notification.entity_type == entity_type
    )
    if entity_id is not None:
        stmt = stmt.where(Notification.entity_id == entity_id)
    if since is not None:
        stmt = stmt.where(Notification.created_at >= since)
    return (await session.execute(stmt.limit(1))).first() is not None


async def _guest_and_room(session: AsyncSession, res: Reservation) -> tuple[str, str | None]:
    guest = await session.get(Guest, res.guest_id) if res.guest_id else None
    room = await session.get(Room, res.room_id) if res.room_id else None
    name = _name(guest.first_name, guest.last_name) if guest else None
    return name or "Mehmon", room.room_number if room else None


def _who(name: str, room_number: str | None) -> str:
    return f"{name} ({room_number}-xona)" if room_number else name


async def notify_checkout_with_debt(
    session: AsyncSession,
    reservation: Reservation,
    debt: dict | None,
    room_number: str | None,
    *,
    event: str,
    actor_id: UUID | None,
) -> None:
    """Qarz bilan chiqarish / farrosh boshlagan chiqish — darhol xabar.

    Hech qachon xato tashlamaydi: chiqish oqimi xabar tufayli buzilmasin.
    """
    try:
        if not debt:
            return
        guest_name, number = await _guest_and_room(session, reservation)
        room_number = room_number or number
        actor = await session.get(User, actor_id) if actor_id else None
        actor_name = _name(actor.first_name, actor.last_name) if actor else None
        amount = fmt_sum(debt["total_debt"])
        why = reasons_text(debt["items"])
        if event == "acknowledged":
            title = "Mehmon qarz bilan chiqarildi"
            note = (reservation.debt_ack_note or "").strip()
            body = f"{_who(guest_name, room_number)}: {amount} so'm — {why}"
            if actor_name:
                body += f". Chiqargan: {actor_name}"
            if note:
                body += f". Sabab: {note}"
            entity_type = TYPE_ACK
        else:
            title = "Qarzdor mehmon chiqmoqda"
            body = (
                f"Farrosh {_who(guest_name, room_number)} xonasini tozalashni "
                f"boshladi — mehmonning qarzi {amount} so'm ({why}). "
                "Mehmon ketmasidan to'lovni oling"
            )
            entity_type = TYPE_CLEANER
        targets = await recipients(session, reservation.hotel_id)
        if actor_id is not None and event == "acknowledged":
            targets.discard(actor_id)  # o'zi bilib turibdi
        await _send(session, reservation.hotel_id, targets, title, body,
                    entity_type, reservation.id)
    except Exception:
        logger.exception("Chiqishdagi qarz xabarini yuborib bo'lmadi (%s)", reservation.id)


class DebtReminderService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.debts = DebtService(session)

    async def run(self, now_local: datetime | None = None, *, force: bool = False) -> dict:
        """Bitta tekshiruv. Natija — qaysi turdan nechta xabar ketgani."""
        global _last_pass
        if not force and time.monotonic() - _last_pass < PASS_SECONDS:
            return {}
        _last_pass = time.monotonic()
        now_local = now_local or local_now()
        stats = {TYPE_LEFT: 0, TYPE_SOON: 0, TYPE_DIGEST: 0}
        for hotel_id in await self._hotels_with_debt():
            try:
                hotel = await self.session.get(Hotel, hotel_id)
                config = resolve_debt_settings(hotel.settings if hotel else None)
                if not config["enabled"]:
                    continue
                targets = await recipients(self.session, hotel_id)
                if not targets:
                    continue
                stats[TYPE_LEFT] += await self._left(hotel_id, targets, now_local)
                stats[TYPE_SOON] += await self._soon(hotel_id, targets, now_local)
                stats[TYPE_DIGEST] += await self._digest(
                    hotel_id, targets, now_local, config["interval_minutes"]
                )
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception("Qarz eslatmalari (mehmonxona %s) bajarilmadi", hotel_id)
        return stats

    async def _hotels_with_debt(self) -> list[UUID]:
        rows = await self.session.execute(
            select(Reservation.hotel_id)
            .where(
                Reservation.is_deleted.is_(False),
                or_(
                    Reservation.status == "CHECKED_IN",
                    and_(
                        Reservation.status == "CHECKED_OUT",
                        Reservation.total_amount > Reservation.paid_amount + MIN_DEBT,
                    ),
                ),
            )
            .distinct()
        )
        hotels = {r[0] for r in rows.all()}
        shop = await self.session.execute(
            select(ShopSale.hotel_id)
            .where(ShopSale.status == "PENDING", ShopSale.reservation_id.is_not(None))
            .distinct()
        )
        hotels |= {r[0] for r in shop.all()}
        return sorted(hotels, key=str)

    async def _left(self, hotel_id: UUID, targets: set[UUID], now_local: datetime) -> int:
        since = datetime.now(timezone.utc) - timedelta(hours=LEFT_WINDOW_HOURS)
        shop_ids = list(await self.debts.shop_reservation_ids(hotel_id))
        rows = (
            await self.session.execute(
                select(Reservation).where(
                    Reservation.hotel_id == hotel_id,
                    Reservation.is_deleted.is_(False),
                    Reservation.status == "CHECKED_OUT",
                    Reservation.updated_at >= since,
                    # Resepsiya tasdiqlab chiqargan — xabar o'shanda ketgan
                    Reservation.debt_ack_at.is_(None),
                    or_(
                        Reservation.total_amount > Reservation.paid_amount + MIN_DEBT,
                        Reservation.id.in_(shop_ids or [None]),
                    ),
                )
            )
        ).scalars().all()
        sent = 0
        for res in rows:
            if await _already_sent(self.session, hotel_id, TYPE_LEFT, res.id):
                continue
            debt = await self.debts.breakdown(res, now_local.date())
            if debt["total_debt"] <= MIN_DEBT:
                continue
            name, room = await _guest_and_room(self.session, res)
            await _send(
                self.session, hotel_id, targets,
                "Mehmon qarz bilan chiqib ketdi",
                f"{_who(name, room)}: {fmt_sum(debt['total_debt'])} so'm — "
                f"{reasons_text(debt['items'])}. Mehmon bilan bog'lanib, qarzni undiring",
                TYPE_LEFT, res.id,
            )
            sent += 1
        return sent

    async def _soon(self, hotel_id: UUID, targets: set[UUID], now_local: datetime) -> int:
        if now_local.hour < DAY_START_HOUR:
            return 0
        today = now_local.date()
        horizon = now_local.replace(tzinfo=None) + timedelta(minutes=SOON_MINUTES)
        rows = (
            await self.session.execute(
                select(Reservation).where(
                    Reservation.hotel_id == hotel_id,
                    Reservation.is_deleted.is_(False),
                    Reservation.status == "CHECKED_IN",
                    Reservation.check_out_date <= today,
                )
            )
        ).scalars().all()
        due = []
        for res in rows:
            if (res.booking_type or "DAILY") == "HOURLY":
                end = res.check_out_datetime
                if end is None:
                    continue
                end = end.replace(tzinfo=None) if end.tzinfo else end
                if end > horizon:
                    continue
            due.append(res)
        if not due:
            return 0
        breakdowns = await self.debts.breakdowns(due, today)
        sent = 0
        for res in due:
            debt = breakdowns[res.id]
            if debt["total_debt"] <= MIN_DEBT:
                continue
            if await _already_sent(self.session, hotel_id, TYPE_SOON, res.id):
                continue
            name, room = await _guest_and_room(self.session, res)
            await _send(
                self.session, hotel_id, targets,
                "Bugun chiqadi — qarzi bor",
                f"{_who(name, room)}: {fmt_sum(debt['total_debt'])} so'm — "
                f"{reasons_text(debt['items'])}. Chiqishdan oldin to'lovni oling",
                TYPE_SOON, res.id,
            )
            sent += 1
        return sent

    async def _digest(
        self, hotel_id: UUID, targets: set[UUID], now_local: datetime, interval: int
    ) -> int:
        if not (DAY_START_HOUR <= now_local.hour < DAY_END_HOUR):
            return 0
        since = datetime.now(timezone.utc) - timedelta(minutes=interval)
        if await _already_sent(self.session, hotel_id, TYPE_DIGEST, since=since):
            return 0
        from app.application.services.debtor_service import DebtorService

        report = await DebtorService(self.session).list_debtors(hotel_id, today=now_local.date())
        items = report["items"]
        if not items:
            return 0
        summary = report["summary"]
        top = sorted(items, key=lambda i: i["debt_amount"], reverse=True)[:3]
        lines = []
        for item in top:
            who = _who(item.get("guest_name") or "Mehmon", item.get("room_number"))
            first = item["reasons"][0] if item.get("reasons") else None
            why = f" — {reason_label(first)}" if first else ""
            lines.append(f"{who}: {fmt_sum(item['debt_amount'])}{why}")
        more = len(items) - len(top)
        body = "; ".join(lines) + (f"; yana {more} ta" if more > 0 else "")
        await _send(
            self.session, hotel_id, targets,
            f"Qarzdorlar: {summary['count']} ta · {fmt_sum(summary['total_debt'])} so'm",
            body, TYPE_DIGEST, None,
        )
        return 1
