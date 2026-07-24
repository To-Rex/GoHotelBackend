import logging
from uuid import UUID
from datetime import date, datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select as sa_select, func

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.infrastructure.database.repositories.housekeeping_repo import HousekeepingRepository
from app.infrastructure.database.repositories.room_repo import RoomRepository
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


class HousekeepingService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = HousekeepingRepository(session)
        self.room_repo = RoomRepository(session)

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

        if status == "COMPLETED" and task.task_type in ("CLEANING", "DEEP_CLEANING"):
            room = await self.room_repo.get_by_id(task.room_id, hotel_id)
            if room and room.current_status == "CLEANING":
                room.current_status = "AVAILABLE"
                await self.room_repo.update(room, current_status="AVAILABLE")

                history = RoomStatusHistory(
                    hotel_id=hotel_id,
                    room_id=room.id,
                    status="AVAILABLE",
                    changed_by=user_id,
                    notes=f"Cleaning task {task.id} completed",
                )
                self.session.add(history)
                await self.session.flush()

        return task

    async def assign_task(self, task_id: UUID, hotel_id: UUID, user_id: UUID) -> HousekeepingTask:
        task = await self.get_task(task_id, hotel_id)
        previous_assignee = task.assigned_to
        assigned = await self.repo.assign_task(task, user_id)
        # Yangi farroshga (o'zgargan bo'lsa) notification + push yuboramiz
        if user_id != previous_assignee:
            await self._notify_assignment(assigned, user_id)
        return assigned
