"""Bron chiqishini avtomatlashtirish xizmati.

Fon rejalashtiruvchisi har tik'da `run_tick()` ni chaqiradi. CHECKED_IN holatidagi
har bir bron uchun quyidagi bosqichlar bajariladi (barchasi idempotent):

  1. Chiqish vaqtidan LEAD daqiqa oldin — farroshga tozalash tuni yaratiladi
     (bo'sh farrosh avtomatik biriktiriladi).
  2. Chiqish vaqti kelganda — xona "OCCUPIED" dan "CLEANING" ga o'tadi.
  3. Tozalash tugagach (task COMPLETED) YOKI GRACE daqiqa o'tgach (farrosh
     bajarmasa ham) — bron avtomatik "CHECKED_OUT" bo'ladi (hisob-faktura bilan).

Bu qo'lda chiqish endpointiga tegmaydi — u avvalgidek bir zumda to'liq chiqaradi.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.models.user import User
from app.infrastructure.database.repositories.room_repo import RoomRepository
from app.infrastructure.database.repositories.user_repo import UserRepository
from app.application.services.reservation_service import ReservationService

logger = logging.getLogger(__name__)


class AutomationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.room_repo = RoomRepository(session)
        self.user_repo = UserRepository(session)
        self.res_service = ReservationService(session)

    # ------------------------------------------------------------------ time --
    def _now_local(self) -> datetime:
        """Mahalliy "devor soati" (naive). Bron datetime'lari ham mahalliy devor
        soati sifatida saqlanadi, shuning uchun ular naive holda solishtiriladi."""
        return (
            datetime.now(timezone.utc) + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
        ).replace(tzinfo=None)

    def _checkout_moment(self, r: Reservation) -> datetime:
        """Bronning chiqish lahzasi (naive mahalliy devor soati)."""
        if r.check_out_datetime is not None:
            # tzinfo'ni tashlab, devor soati raqamlarini o'z holida olamiz
            return r.check_out_datetime.replace(tzinfo=None)
        hour = max(0, min(23, settings.DEFAULT_CHECKOUT_HOUR))
        return datetime.combine(r.check_out_date, time(hour=hour))

    # --------------------------------------------------------------- cleaner --
    async def _find_cleaner(self, hotel_id: UUID, branch_id: UUID) -> UUID | None:
        """Mehmonxonadagi eng kam yuklamali farroshni topadi.

        Farrosh = housekeeping.* ruxsatiga ega EMPLOYEE (alohida rol maydoni yo'q).
        Iloji bo'lsa o'sha filialdan tanlanadi. Topilmasa None (tun biriktirilmay
        yaratiladi va ro'yxatda ko'rinadi)."""
        employees = await self.user_repo.get_employees(hotel_id, limit=500)
        candidates: list[User] = []
        for e in employees:
            if getattr(e, "is_deleted", False):
                continue
            perms = await self.user_repo.get_user_permissions(e.id)
            if any(str(p.get("code", "")).startswith("housekeeping.") for p in perms):
                candidates.append(e)
        if not candidates:
            return None

        same_branch = [e for e in candidates if e.branch_id == branch_id]
        pool = same_branch or candidates
        pool_ids = [e.id for e in pool]

        # Eng kam faol (OPEN/IN_PROGRESS) tunga ega farroshni tanlaymiz
        counts: dict[UUID, int] = {pid: 0 for pid in pool_ids}
        rows = await self.session.execute(
            select(HousekeepingTask.assigned_to, func.count())
            .where(
                HousekeepingTask.assigned_to.in_(pool_ids),
                HousekeepingTask.status.in_(["OPEN", "IN_PROGRESS"]),
            )
            .group_by(HousekeepingTask.assigned_to)
        )
        for assigned_to, cnt in rows.all():
            if assigned_to in counts:
                counts[assigned_to] = cnt
        return min(pool_ids, key=lambda pid: counts.get(pid, 0))  # type: ignore[return-value]

    async def _linked_cleaning_task(self, reservation_id: UUID) -> HousekeepingTask | None:
        result = await self.session.execute(
            select(HousekeepingTask)
            .where(
                HousekeepingTask.reservation_id == reservation_id,
                HousekeepingTask.task_type == "CLEANING",
                HousekeepingTask.status != "CANCELLED",
            )
            .order_by(HousekeepingTask.created_at.desc())
        )
        return result.scalars().first()

    # ------------------------------------------------------------------ tick --
    async def run_tick(self) -> None:
        now = self._now_local()
        today = now.date()

        # Yakunlanmagan bronlar: CHECKED_IN (bosqichli oqim) va CONFIRMED
        # (kirish qilinmagan, vaqti o'tgan bronlarni yopish uchun).
        # Pastki sana chegarasi YO'Q — eski bronlar ham yopiladi. Ro'yxat o'z-o'zidan
        # qisqaradi: yopilgan bron boshqa tanlanmaydi. Yuqori chegara — kelajakdagi
        # bronlarni bekorga o'qimaslik uchun.
        id_rows = await self.session.execute(
            select(Reservation.id).where(
                Reservation.status.in_(["CHECKED_IN", "CONFIRMED"]),
                Reservation.is_deleted.is_(False),
                Reservation.check_out_date <= today + timedelta(days=1),
            )
        )
        ids = [row[0] for row in id_rows.all()]

        for rid in ids:
            try:
                reservation = await self.session.get(Reservation, rid)
                # Oraliqda holat o'zgargan bo'lishi mumkin — qayta tekshiramiz
                if (
                    reservation is None
                    or reservation.is_deleted
                    or reservation.status not in ("CHECKED_IN", "CONFIRMED")
                ):
                    continue
                await self._process(reservation, now)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception("Auto-checkout failed for reservation %s", rid)

    async def _process(self, reservation: Reservation, now: datetime) -> None:
        moment = self._checkout_moment(reservation)
        lead = timedelta(minutes=settings.HOUSEKEEPING_LEAD_MINUTES)
        grace = timedelta(minutes=settings.AUTO_CHECKOUT_GRACE_MINUTES)
        hotel_id = reservation.hotel_id
        actor = reservation.created_by  # avtomatik amallar uchun tizim aktori

        room = await self.room_repo.get_by_id(reservation.room_id, hotel_id)
        if not room:
            return

        # --- Kirish qilinmagan (CONFIRMED) bronlar ---
        # Mehmon kirmagan, demak tozalash kerak emas. Vaqti o'tgan bo'lsa bron
        # yopiladi va band turgan xona bo'shatiladi.
        if reservation.status == "CONFIRMED":
            if now < moment:
                return
            await self.res_service.check_out(
                reservation.id,
                hotel_id,
                actor,
                transition_room=False,
                create_cleaning_task=False,
                allowed_statuses=("CONFIRMED",),
            )
            # Bron uchun band qilingan xona hali RESERVED bo'lsa — bo'shatamiz.
            # Lekin o'sha xonada boshqa faol bron bo'lsa tegmaymiz (aks holda
            # kelajakdagi bronning band holati noto'g'ri tozalanib ketadi).
            other_active = await self.session.execute(
                select(Reservation.id)
                .where(
                    Reservation.room_id == room.id,
                    Reservation.id != reservation.id,
                    Reservation.status.in_(["CONFIRMED", "CHECKED_IN"]),
                    Reservation.is_deleted.is_(False),
                )
                .limit(1)
            )
            if room.current_status == "RESERVED" and other_active.first() is None:
                room.current_status = "AVAILABLE"
                await self.room_repo.update(room, current_status="AVAILABLE")
                self.session.add(
                    RoomStatusHistory(
                        hotel_id=hotel_id,
                        room_id=room.id,
                        status="AVAILABLE",
                        changed_by=actor,
                        notes=f"Auto check-out (kirilmagan bron) {reservation.reservation_number}",
                    )
                )
                await self.session.flush()
            logger.info(
                "Reservation %s auto CHECKED_OUT (was CONFIRMED, never checked in)",
                reservation.id,
            )
            return

        # --- 1-bosqich: chiqishdan LEAD daqiqa oldin tozalash tuni yaratiladi ---
        if now >= moment - lead:
            existing = await self._linked_cleaning_task(reservation.id)
            if existing is None:
                cleaner_id = await self._find_cleaner(hotel_id, reservation.branch_id)
                await self.res_service._ensure_cleaning_task(
                    reservation, hotel_id, room, actor, assigned_to=cleaner_id
                )
                logger.info(
                    "Auto cleaning task created for reservation %s (cleaner=%s)",
                    reservation.id,
                    cleaner_id,
                )

        # --- 2-bosqich: chiqish vaqti kelganda xona CLEANING ga o'tadi ---
        if now >= moment and room.current_status == "OCCUPIED":
            room.current_status = "CLEANING"
            await self.room_repo.update(room, current_status="CLEANING")
            self.session.add(
                RoomStatusHistory(
                    hotel_id=hotel_id,
                    room_id=room.id,
                    status="CLEANING",
                    changed_by=actor,
                    notes=f"Auto check-out: cleaning started for {reservation.reservation_number}",
                )
            )
            await self.session.flush()
            logger.info("Room %s -> CLEANING (reservation %s)", room.id, reservation.id)

        # --- 3-bosqich: tozalash tugagach yoki GRACE o'tgach CHECKED_OUT ---
        if now >= moment:
            task = await self._linked_cleaning_task(reservation.id)
            cleaning_done = task is not None and task.status == "COMPLETED"
            timed_out = now >= moment + grace
            if cleaning_done or timed_out:
                # Xona holati va tozalash tuni allaqachon boshqarilgan — check_out
                # ularga tegmasin (tozalash tugagan bo'lsa xona AVAILABLE, timeout
                # bo'lsa CLEANING holida qoladi).
                await self.res_service.check_out(
                    reservation.id,
                    hotel_id,
                    actor,
                    transition_room=False,
                    create_cleaning_task=False,
                )
                logger.info(
                    "Reservation %s auto CHECKED_OUT (%s)",
                    reservation.id,
                    "cleaned" if cleaning_done else "timeout",
                )
