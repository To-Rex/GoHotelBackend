"""Chiqishlar va tozalashlarni solishtirish.

Savol oddiy: mehmonlar necha marta chiqib ketdi va shundan nechtasi
haqiqatan tozalandi? Ma'lumot allaqachon yig'ilyapti, faqat bir joyga
keltirilmagan edi.

Eng muhim ko'rsatkich — `auto_completed`. U `true` bo'lsa vazifani ODAM
EMAS, fon vazifasi yopgan (belgilangan vaqt o'tgani uchun). Ya'ni xona
"tozalandi" deb belgilangan, lekin buni hech kim tasdiqlamagan. Aynan shu
raqam xo'jalik ishlarining haqiqiy holatini ko'rsatadi.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.user import User

CLEANING_TYPES = ("CLEANING", "DEEP_CLEANING")


def _minutes_between(start: datetime | None, end: datetime | None) -> float | None:
    """Ikki payt orasidagi daqiqalar. Biri yo'q bo'lsa None."""
    if start is None or end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    delta = (end - start).total_seconds() / 60.0
    # Manfiy oraliq — ma'lumot buzilgan; hisobni buzmasin
    return round(delta, 1) if delta >= 0 else None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


class CleaningReportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def build(
        self, hotel_id: UUID, date_from: date, date_to: date
    ) -> dict:
        """Davr bo'yicha chiqishlar va ularning tozalanishi."""
        # --- Davrdagi chiqishlar ---
        #
        # Asos — CHIQIB KETGAN bronlar: savol aynan "chiqqandan keyin
        # tozalandimi". Bekor qilingan va kelmagan bronlarda mehmon
        # xonadan foydalanmagan.
        res_rows = (
            await self.session.execute(
                select(Reservation, Room.room_number)
                .join(Room, Room.id == Reservation.room_id, isouter=True)
                .where(
                    Reservation.hotel_id == hotel_id,
                    Reservation.is_deleted.is_(False),
                    Reservation.status == "CHECKED_OUT",
                    Reservation.check_out_date >= date_from,
                    Reservation.check_out_date <= date_to,
                )
            )
        ).all()

        reservation_ids = [row[0].id for row in res_rows]

        # --- Shu bronlarga bog'langan tozalash vazifalari ---
        tasks_by_res: dict[UUID, HousekeepingTask] = {}
        if reservation_ids:
            task_rows = (
                (
                    await self.session.execute(
                        select(HousekeepingTask).where(
                            HousekeepingTask.reservation_id.in_(reservation_ids),
                            HousekeepingTask.task_type.in_(CLEANING_TYPES),
                        )
                    )
                )
                .scalars()
                .all()
            )
            # Bitta bronda bir nechta vazifa bo'lishi mumkin (qayta ochilgan
            # bo'lsa) — yakunlangani ustun, aks holda eng oxirgisi
            for task in task_rows:
                current = tasks_by_res.get(task.reservation_id)
                if current is None:
                    tasks_by_res[task.reservation_id] = task
                    continue
                if task.status == "COMPLETED" and current.status != "COMPLETED":
                    tasks_by_res[task.reservation_id] = task
                elif (task.created_at or datetime.min.replace(tzinfo=timezone.utc)) > (
                    current.created_at or datetime.min.replace(tzinfo=timezone.utc)
                ):
                    tasks_by_res[task.reservation_id] = task

        cleaned_by_person = 0
        auto_closed = 0
        still_open = 0
        cancelled = 0
        no_task = 0

        wait_minutes: list[float] = []
        work_minutes: list[float] = []

        per_cleaner: dict[UUID | None, dict] = defaultdict(
            lambda: {"total": 0, "by_person": 0, "auto": 0, "minutes": []}
        )
        per_room: dict[str, dict] = defaultdict(
            lambda: {"checkouts": 0, "auto": 0, "no_task": 0, "minutes": []}
        )

        for reservation, room_number in res_rows:
            room_key = room_number or "—"
            per_room[room_key]["checkouts"] += 1

            task = tasks_by_res.get(reservation.id)
            if task is None:
                no_task += 1
                per_room[room_key]["no_task"] += 1
                continue

            stat = per_cleaner[task.assigned_to]
            stat["total"] += 1

            if task.status == "CANCELLED":
                cancelled += 1
                continue
            if task.status != "COMPLETED":
                still_open += 1
                continue

            if task.auto_completed:
                auto_closed += 1
                stat["auto"] += 1
                per_room[room_key]["auto"] += 1
            else:
                cleaned_by_person += 1
                stat["by_person"] += 1

            # Kutish: vazifa ochilgandan farrosh boshlagunicha.
            # `started_at` faqat farrosh "boshladim" deb belgilaganda
            # to'ladi — belgilamasa bu ko'rsatkich bo'sh qoladi.
            wait = _minutes_between(task.created_at, task.started_at)
            if wait is not None:
                wait_minutes.append(wait)

            # Davomiylik: boshlangandan yakunlangunicha. Boshlanish
            # belgilanmagan bo'lsa ochilgan paytdan.
            work = _minutes_between(task.started_at or task.created_at, task.completed_at)
            if work is not None:
                work_minutes.append(work)
                stat["minutes"].append(work)
                per_room[room_key]["minutes"].append(work)

        # --- Farroshlar kesimi ---
        cleaner_ids = [cid for cid in per_cleaner if cid is not None]
        names: dict[UUID, str] = {}
        if cleaner_ids:
            users = (
                (
                    await self.session.execute(
                        select(User).where(User.id.in_(cleaner_ids))
                    )
                )
                .scalars()
                .all()
            )
            names = {
                u.id: " ".join(p for p in (u.first_name, u.last_name) if p).strip()
                for u in users
            }

        cleaners = [
            {
                "user_id": cid,
                "name": names.get(cid) if cid else None,
                "total": v["total"],
                "by_person": v["by_person"],
                "auto": v["auto"],
                "avg_minutes": _avg(v["minutes"]),
            }
            for cid, v in per_cleaner.items()
        ]
        # Avtomatik yopilgani ko'plari yuqorida — e'tibor aynan ularga kerak
        cleaners.sort(key=lambda c: (-c["auto"], -c["total"]))

        rooms = [
            {
                "room_number": rn,
                "checkouts": v["checkouts"],
                "auto": v["auto"],
                "no_task": v["no_task"],
                "avg_minutes": _avg(v["minutes"]),
            }
            for rn, v in per_room.items()
        ]
        rooms.sort(key=lambda r: (-(r["auto"] + r["no_task"]), -r["checkouts"]))

        total = len(res_rows)
        return {
            "date_from": date_from,
            "date_to": date_to,
            "summary": {
                "checkouts": total,
                "with_task": total - no_task,
                "cleaned_by_person": cleaned_by_person,
                "auto_closed": auto_closed,
                "still_open": still_open,
                "cancelled": cancelled,
                "no_task": no_task,
                "avg_wait_minutes": _avg(wait_minutes),
                "avg_work_minutes": _avg(work_minutes),
                # Odam tasdiqlagan tozalashlar ulushi
                "verified_percent": round(cleaned_by_person / total * 100, 1)
                if total
                else None,
            },
            "cleaners": cleaners[:20],
            "rooms": rooms[:20],
        }
