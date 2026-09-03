import logging
from uuid import UUID
from datetime import date, datetime, time, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select as sa_select, func

from app.core.config import settings
from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.models.checklist_item import ChecklistItem
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.infrastructure.database.repositories.housekeeping_repo import HousekeepingRepository
from app.infrastructure.database.repositories.room_repo import RoomRepository
from app.application.services.checklist_template_service import (
    ChecklistTemplateService,
)
from app.application.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# Vazifa turi kodini o'zbekcha ko'rinishga o'giradi (notification matni uchun)
TASK_TYPE_LABELS = {
    "CLEANING": "Tozalash",
    "DEEP_CLEANING": "Chuqur tozalash",
    "MAINTENANCE": "Ta'mirlash",
    "INSPECTION": "Tekshiruv",
    "TURN_DOWN": "Xona tayyorlash",
}

# Status o'zgarganda yuboriladigan notification sarlavhasi
TASK_STATUS_TITLES = {
    "OPEN": "Vazifa qayta ochildi",
    "IN_PROGRESS": "Vazifa boshlandi",
    "COMPLETED": "Vazifa yakunlandi",
    "CANCELLED": "Vazifa bekor qilindi",
}

# Vazifa turlari bo'yicha AVTOMATIK YAKUNLASH vaqtlari (daqiqalarda) —
# xodim qo'lda yakunlamasa scheduler shu vaqtdan keyin o'zi yopadi.
# Mehmonxona bo'yicha o'zgartirilishi mumkin (hotels.settings JSON'ida
# "hk_auto_complete" kaliti ostida saqlanadi). 0 — o'chirilgan.
HK_AUTO_COMPLETE_DEFAULTS: dict[str, int] = {
    "CLEANING": 20,
    "DEEP_CLEANING": 30,
    "MAINTENANCE": 60,
    "INSPECTION": 15,
    "TURN_DOWN": 15,
}

HK_SETTINGS_KEY = "hk_auto_complete"

# Vazifa turi -> xona qanday holatga o'tishi.
#
# Vazifa ochilishi xona bilan nima bo'layotganini bildiradi, shuning uchun
# xona holati ham shuni ko'rsatishi kerak. Ilgari vazifa yaratilsa ham xona
# "Bo'sh" bo'lib turaverardi va ta'mirdagi xonaga bron qilish mumkin edi:
# bron tekshiruvi xona holatiga qaraydi, vazifaga emas.
#
# TURN_DOWN ro'yxatda yo'q — u mehmon ichkarida turganda bajariladi va
# xonani band qilmaydi.
TASK_ROOM_STATUS: dict[str, str] = {
    "CLEANING": "CLEANING",
    "DEEP_CLEANING": "CLEANING",
    "MAINTENANCE": "MAINTENANCE",
    "INSPECTION": "INSPECTION",
}

# Vazifa faqat shu holatlardagi xonani "egallay" oladi.
#
# OCCUPIED va RESERVED yo'q: u yerda mehmon bor va uni xo'jalik vazifasi
# quvib chiqarmasligi kerak. OUT_OF_SERVICE ham yo'q — u ataylab qo'yilgan
# qaror, vazifa uni yumshatib yubormasin.
ROOM_STATUS_TAKEABLE = ("AVAILABLE", "CLEANING", "MAINTENANCE", "INSPECTION")


def resolve_auto_complete_minutes(
    hotel_settings: dict | None, task_type: str
) -> int:
    """Mehmonxona sozlamasi (bo'lsa) yoki standart qiymatni qaytaradi."""
    overrides = (hotel_settings or {}).get(HK_SETTINGS_KEY) or {}
    try:
        value = int(overrides.get(task_type, HK_AUTO_COMPLETE_DEFAULTS.get(task_type, 0)))
        return max(value, 0)
    except (TypeError, ValueError):
        return HK_AUTO_COMPLETE_DEFAULTS.get(task_type, 0)


class HousekeepingService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = HousekeepingRepository(session)
        self.room_repo = RoomRepository(session)

    async def get_occupied_rooms(
        self, hotel_id: UUID | None, include_reserved: bool = False
    ) -> list[dict]:
        """Farrosh uchun: hozir band xonalar va ularning chiqish vaqtlari.

        Chiqishga eng yaqin bron BIRINCHI ko'rsatiladi (vaqti o'tib ketganlar
        undan ham oldin). DAILY bronlarda chiqish vaqti DEFAULT_CHECKOUT_HOUR
        (standart 12:00) deb olinadi, HOURLY bronlarda aniq check_out_datetime —
        avtomatik chiqish (automation_service) bilan bir xil mantiq.
        """
        statuses = ["CHECKED_IN"] + (["CONFIRMED"] if include_reserved else [])
        stmt = (
            sa_select(Reservation, Room, Guest)
            .join(Room, Room.id == Reservation.room_id)
            .join(Guest, Guest.id == Reservation.guest_id)
            .where(
                Reservation.is_deleted.is_(False),
                Reservation.status.in_(statuses),
            )
        )
        if hotel_id is not None:
            stmt = stmt.where(Reservation.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        rows = result.all()

        # Bron vaqtlari mahalliy "devor soati" sifatida saqlanadi — naive
        # holda solishtiramiz (automation_service._local_now bilan bir xil)
        local_now = (
            datetime.now(timezone.utc) + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
        ).replace(tzinfo=None)

        def checkout_moment(r: Reservation) -> datetime:
            if r.check_out_datetime is not None:
                return r.check_out_datetime.replace(tzinfo=None)
            hour = max(0, min(23, settings.DEFAULT_CHECKOUT_HOUR))
            return datetime.combine(r.check_out_date, time(hour=hour))

        items: list[dict] = []
        for r, room, guest in rows:
            co = checkout_moment(r)
            minutes_left = int((co - local_now).total_seconds() // 60)
            items.append(
                {
                    "room_id": str(room.id),
                    "room_number": room.room_number,
                    "room_status": room.current_status,
                    "floor_id": str(room.floor_id) if room.floor_id else None,
                    "reservation_id": str(r.id),
                    "reservation_number": r.reservation_number,
                    "reservation_status": r.status,
                    "booking_type": r.booking_type,
                    "guest_name": f"{guest.first_name} {guest.last_name or ''}".strip(),
                    "check_in_date": str(r.check_in_date),
                    "check_out_date": str(r.check_out_date),
                    "check_out_datetime": (
                        r.check_out_datetime.replace(tzinfo=None).isoformat()
                        if r.check_out_datetime
                        else None
                    ),
                    "expected_checkout": co.isoformat(),
                    "minutes_until_checkout": minutes_left,
                    "is_overdue": minutes_left < 0,
                }
            )

        # Chiqishga eng yaqini birinchi (kechikkanlar ro'yxat boshida)
        items.sort(key=lambda x: x["expected_checkout"])
        return items

    async def _notify_assignment(
        self,
        task: HousekeepingTask,
        assignee_id: UUID | None,
        title: str = "Yangi vazifa biriktirildi",
    ) -> None:
        """Vazifa biriktirilgan/o'zgartirilgan farroshga notification + push yuboradi.

        Xato bo'lsa jimgina o'tadi — asosiy vazifa operatsiyasini hech qachon
        buzmaydi.
        """
        if not assignee_id:
            return
        try:
            room = getattr(task, "room", None)
            room_number = room.room_number if room else None
            type_label = TASK_TYPE_LABELS.get(task.task_type, task.task_type)
            body = (
                f"{type_label} — {room_number}-xona" if room_number else type_label
            )
            await NotificationService(self.session).notify(
                hotel_id=task.hotel_id,
                user_id=assignee_id,
                title=title,
                body=body,
                entity_type="task",
                entity_id=task.id,
                send_push=True,
            )
        except Exception:
            logger.exception(
                "Vazifa notification yuborilmadi (task=%s, user=%s)",
                task.id,
                assignee_id,
            )

    async def _enrich_checklists(self, tasks: list[HousekeepingTask]) -> None:
        """Vazifa bandlarini javobga qo'shadi.

        Boshqaruv veb ekranida farrosh nimani bajarganini ko'radi: bu
        fotohisobotdan ham aniqroq javob beradi — qaysi ish qilinmagani
        darhol ko'rinadi.
        """
        if not tasks:
            return
        rows = (
            await self.session.execute(
                sa_select(ChecklistItem)
                .where(ChecklistItem.task_id.in_([t.id for t in tasks]))
                .order_by(ChecklistItem.task_id, ChecklistItem.sort_order)
            )
        ).scalars().all()

        by_task: dict = {}
        for row in rows:
            by_task.setdefault(row.task_id, []).append(row)

        for task in tasks:
            items = by_task.get(task.id, [])
            task.checklist = [
                {
                    "id": str(i.id),
                    "title": i.title,
                    "is_completed": i.is_completed,
                    "sort_order": i.sort_order,
                }
                for i in items
            ]
            task.checklist_total = len(items)
            task.checklist_done = sum(1 for i in items if i.is_completed)

    async def _enrich_photo_counts(self, tasks: list[HousekeepingTask]) -> None:
        if not tasks:
            return
        task_ids = [t.id for t in tasks]
        stmt = (
            sa_select(FileAttachment.entity_id, func.count(FileAttachment.id).label("cnt"))
            .where(
                FileAttachment.entity_id.in_(task_ids),
                FileAttachment.entity_type == "task_report",
                FileAttachment.is_deleted == False,
            )
            .group_by(FileAttachment.entity_id)
        )
        result = await self.session.execute(stmt)
        counts = {row[0]: row[1] for row in result}
        for task in tasks:
            task.photo_count = counts.get(task.id, 0)

    async def _set_room_status(
        self,
        room: Room,
        status: str,
        hotel_id: UUID,
        user_id: UUID,
        note: str,
    ) -> None:
        """Xona holatini o'zgartiradi va tarixga yozadi."""
        room.current_status = status
        await self.room_repo.update(room, current_status=status)
        self.session.add(
            RoomStatusHistory(
                hotel_id=hotel_id,
                room_id=room.id,
                status=status,
                changed_by=user_id,
                notes=note,
            )
        )
        await self.session.flush()

    async def _apply_task_room_status(
        self, task: HousekeepingTask, hotel_id: UUID, user_id: UUID
    ) -> None:
        """Yangi vazifa xonani o'z holatiga o'tkazadi.

        Kelgusi kunga rejalashtirilgan vazifa xonani hozir band qilmaydi —
        aks holda keyingi haftaga qo'yilgan ta'mir bugundan xonani yopib
        qo'yardi.
        """
        target = TASK_ROOM_STATUS.get(task.task_type)
        if target is None:
            return

        if task.scheduled_date is not None:
            local_today = (
                datetime.now(timezone.utc)
                + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
            ).date()
            if task.scheduled_date > local_today:
                return

        room = await self.room_repo.get_by_id(task.room_id, hotel_id)
        if room is None or room.current_status == target:
            return
        if room.current_status not in ROOM_STATUS_TAKEABLE:
            # Mehmon ichkarida yoki xona ataylab yopilgan — tegmaymiz.
            # Vazifa baribir yaratiladi va ro'yxatda ko'rinadi.
            return

        await self._set_room_status(
            room,
            target,
            hotel_id,
            user_id,
            f"{task.task_type} task {task.id} opened",
        )

    async def _release_task_room_status(
        self, task: HousekeepingTask, hotel_id: UUID, user_id: UUID
    ) -> None:
        """Vazifa yopilgach xona bo'shaydi — boshqa ochiq vazifa qolmagan bo'lsa.

        Ikkinchi shart muhim: bitta xonada ikki ta'mir vazifasi bo'lsa,
        birini yopish xonani ochib yuborsa, ikkinchisi e'tibordan qolardi.
        """
        target = TASK_ROOM_STATUS.get(task.task_type)
        if target is None:
            return

        room = await self.room_repo.get_by_id(task.room_id, hotel_id)
        if room is None or room.current_status != target:
            return

        # Shu holatni talab qiladigan boshqa faol vazifa bormi
        same_status_types = [t for t, st in TASK_ROOM_STATUS.items() if st == target]
        other = (
            await self.session.execute(
                sa_select(HousekeepingTask.id)
                .where(
                    HousekeepingTask.room_id == task.room_id,
                    HousekeepingTask.id != task.id,
                    HousekeepingTask.task_type.in_(same_status_types),
                    HousekeepingTask.status.in_(["OPEN", "IN_PROGRESS"]),
                )
                .limit(1)
            )
        ).first()
        if other:
            return

        await self._set_room_status(
            room,
            "AVAILABLE",
            hotel_id,
            user_id,
            f"{task.task_type} task {task.id} {task.status.lower()}",
        )

    async def create_task(self, hotel_id: UUID, data: dict, created_by: UUID) -> HousekeepingTask:
        room = await self.room_repo.get_by_id(data["room_id"], hotel_id)
        if not room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        task = HousekeepingTask(
            hotel_id=hotel_id,
            branch_id=data["branch_id"],
            room_id=data["room_id"],
            task_type=data["task_type"],
            priority=data.get("priority", "MEDIUM"),
            assigned_to=data.get("assigned_to"),
            notes=data.get("notes"),
            scheduled_date=data.get("scheduled_date"),
            created_by=created_by,
        )
        task = await self.repo.create(task)
        # Standart ish bandlari vazifaga NUSXA bo'lib tushadi — farrosh
        # nima qilish kerakligini ro'yxatdan ko'radi
        await ChecklistTemplateService(self.session).attach_to_task(task)
        # Xona holati vazifaga mos kelsin — aks holda ta'mirdagi xona
        # "Bo'sh" bo'lib turaverardi va unga bron qilish mumkin edi
        await self._apply_task_room_status(task, hotel_id, created_by)
        full_task = await self.get_task(task.id, hotel_id)
        # Yaratishda darhol farroshga biriktirilgan bo'lsa — notification yuboramiz
        await self._notify_assignment(full_task, full_task.assigned_to)
        return full_task

    async def get_tasks(
        self,
        hotel_id: UUID | None,
        skip: int = 0,
        limit: int = 50,
        status: str | None = None,
        room_id: UUID | None = None,
        branch_id: UUID | None = None,
        assigned_to: UUID | None = None,
    ) -> list[HousekeepingTask]:
        stmt = sa_select(HousekeepingTask).options(
            selectinload(HousekeepingTask.room),
            selectinload(HousekeepingTask.assigned_user),
            selectinload(HousekeepingTask.branch),
        )
        if hotel_id is not None:
            stmt = stmt.where(HousekeepingTask.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(HousekeepingTask.status == status)
        if room_id:
            stmt = stmt.where(HousekeepingTask.room_id == room_id)
        if branch_id:
            stmt = stmt.where(HousekeepingTask.branch_id == branch_id)
        if assigned_to:
            stmt = stmt.where(HousekeepingTask.assigned_to == assigned_to)
        stmt = stmt.order_by(HousekeepingTask.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        tasks = list(result.scalars().all())
        await self._enrich_photo_counts(tasks)
        await self._enrich_checklists(tasks)
        return tasks

    async def get_my_tasks(
        self, hotel_id: UUID | None, user_id: UUID, skip: int = 0, limit: int = 50
    ) -> list[HousekeepingTask]:
        return await self.repo.get_tasks_by_assignee(hotel_id, user_id, skip=skip, limit=limit)

    async def get_open_tasks(
        self, hotel_id: UUID | None, branch_id: UUID | None = None, skip: int = 0, limit: int = 50
    ) -> list[HousekeepingTask]:
        return await self.repo.get_open_tasks(hotel_id, branch_id, skip, limit)

    async def get_task(self, task_id: UUID, hotel_id: UUID | None) -> HousekeepingTask:
        if hotel_id is None:
            stmt = sa_select(HousekeepingTask).options(
                selectinload(HousekeepingTask.room),
                selectinload(HousekeepingTask.assigned_user),
                selectinload(HousekeepingTask.branch),
            ).where(HousekeepingTask.id == task_id)
            result = await self.session.execute(stmt)
            task = result.scalar_one_or_none()
        else:
            stmt = sa_select(HousekeepingTask).options(
                selectinload(HousekeepingTask.room),
                selectinload(HousekeepingTask.assigned_user),
                selectinload(HousekeepingTask.branch),
            ).where(HousekeepingTask.id == task_id, HousekeepingTask.hotel_id == hotel_id)
            result = await self.session.execute(stmt)
            task = result.scalar_one_or_none()
        if not task:
            raise NotFoundException("Task not found", "TASK_NOT_FOUND")
        await self._enrich_photo_counts([task])
        await self._enrich_checklists([task])
        return task

    async def update_task(self, task_id: UUID, hotel_id: UUID, data: dict) -> HousekeepingTask:
        task = await self.get_task(task_id, hotel_id)
        previous_assignee = task.assigned_to
        updatable = ["task_type", "priority", "assigned_to", "notes", "scheduled_date"]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        updated = await self.repo.update(task, **update_data)

        new_assignee = update_data.get("assigned_to")
        if new_assignee is not None and new_assignee != previous_assignee:
            # Boshqa farroshga biriktirildi — yangi farroshga xabar beramiz
            await self._notify_assignment(updated, new_assignee)
        else:
            # Assignee o'zgarmadi, lekin task tahrirlandi (prioritet/izoh/vaqt/tur) —
            # joriy biriktirilgan farroshga "yangilandi" xabari yuboramiz.
            changed_fields = [k for k in update_data if k != "assigned_to"]
            if updated.assigned_to is not None and changed_fields:
                await self._notify_assignment(
                    updated, updated.assigned_to, title="Vazifa yangilandi"
                )
        return updated

    async def update_task_status(
        self,
        task_id: UUID,
        hotel_id: UUID,
        status: str,
        user_id: UUID,
        notes: str | None = None,
    ) -> HousekeepingTask:
        task = await self.get_task(task_id, hotel_id)

        valid_statuses = ["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
        if status not in valid_statuses:
            raise ValidationException(f"Invalid task status: {status}", "INVALID_STATUS")

        task = await self.repo.update_status(task, status, user_id)

        if notes:
            task.notes = (task.notes or "") + f"\n[{status}] {notes}"
            await self.session.flush()

        # Vazifa yopildi yoki bekor qilindi — xona holatini bo'shatamiz.
        # Bekor qilish ham shu yerda: vazifasiz qolgan xona CLEANING da
        # tiqilib qolardi va fon vazifasi unga yangi vazifa yasab yurardi.
        if status in ("COMPLETED", "CANCELLED"):
            await self._release_task_room_status(task, hotel_id, user_id)

        # Qayta ochilgan vazifa xonani yana o'z holatiga qaytaradi
        if status in ("OPEN", "IN_PROGRESS"):
            await self._apply_task_room_status(task, hotel_id, user_id)

        if status == "COMPLETED" and task.task_type in ("CLEANING", "DEEP_CLEANING"):
            # Resepsiya "mehmon chiqmoqda" deb belgilagan bron: farrosh
            # tozalashni yakunladi — bron avtomatik CHECKED_OUT qilinadi.
            # (Oddiy rejadagi tozalash vazifalarida checkout_requested_at bo'sh
            # bo'ladi — ularda bron holatiga tegilmaydi, avvalgi xatti-harakat
            # to'liq saqlanadi.)
            if task.reservation_id:
                from app.infrastructure.database.models.reservation import Reservation

                reservation = await self.session.get(Reservation, task.reservation_id)
                if (
                    reservation is not None
                    and not getattr(reservation, "is_deleted", False)
                    and reservation.status in ("CHECKED_IN", "CONFIRMED")
                    and reservation.checkout_requested_at is not None
                ):
                    # Aylanma importning oldini olish uchun lokal import
                    from app.application.services.reservation_service import (
                        ReservationService,
                    )

                    await ReservationService(self.session).check_out(
                        reservation.id,
                        hotel_id,
                        user_id,
                        transition_room=False,
                        create_cleaning_task=False,
                        allowed_statuses=("CHECKED_IN", "CONFIRMED"),
                    )

        # Status admin/manager tomonidan o'zgartirilganda (bekor qilish, qayta ochish
        # va h.k.) biriktirilgan farroshga xabar beramiz. Farroshning o'z amali
        # (o'ziga o'zi status o'zgartirsa) takroriy xabar bermaymiz.
        if task.assigned_to and task.assigned_to != user_id:
            title = TASK_STATUS_TITLES.get(status, "Vazifa holati o'zgardi")
            await self._notify_assignment(task, task.assigned_to, title=title)

        return task

    async def assign_task(self, task_id: UUID, hotel_id: UUID, user_id: UUID) -> HousekeepingTask:
        task = await self.get_task(task_id, hotel_id)
        previous_assignee = task.assigned_to
        assigned = await self.repo.assign_task(task, user_id)
        # Yangi farroshga (o'zgargan bo'lsa) notification + push yuboramiz
        if user_id != previous_assignee:
            await self._notify_assignment(assigned, user_id)
        return assigned
