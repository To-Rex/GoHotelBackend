from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.checklist_item import ChecklistItem
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.floor import Floor
from app.infrastructure.database.models.room_type import RoomType
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.guest import Guest


_MOBILE_STATUS_MAP = {
    "OPEN": "pending",
    "IN_PROGRESS": "inProgress",
    "COMPLETED": "completed",
    "CANCELLED": "completed",
}

_REVERSE_STATUS_MAP = {v: k for k, v in _MOBILE_STATUS_MAP.items()}


class MobileTasksService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_tasks(
        self,
        hotel_id: UUID,
        user_id: UUID,
        status: str | None = None,
        date: str | None = None,
    ) -> list[dict]:
        stmt = (
            select(HousekeepingTask)
            .options(
                selectinload(HousekeepingTask.room).selectinload(Room.floor),
                selectinload(HousekeepingTask.room).selectinload(Room.room_type),
                selectinload(HousekeepingTask.checklist_items),
            )
            .where(
                HousekeepingTask.hotel_id == hotel_id,
                HousekeepingTask.assigned_to == user_id,
            )
        )

        if status:
            db_status = _REVERSE_STATUS_MAP.get(status, status.upper())
            if db_status == "COMPLETED":
                stmt = stmt.where(HousekeepingTask.status.in_(["COMPLETED", "CANCELLED"]))
            else:
                stmt = stmt.where(HousekeepingTask.status == db_status)

        if date:
            stmt = stmt.where(func.date(HousekeepingTask.scheduled_date) == date)

        stmt = stmt.order_by(HousekeepingTask.priority.desc(), HousekeepingTask.created_at.desc())

        result = await self.session.execute(stmt)
        tasks = result.unique().scalars().all()
        return [await self._enrich_task(t, hotel_id) for t in tasks]

    async def get_task_by_id(self, task_id: UUID, hotel_id: UUID) -> dict:
        stmt = (
            select(HousekeepingTask)
            .options(
                selectinload(HousekeepingTask.room).selectinload(Room.floor),
                selectinload(HousekeepingTask.room).selectinload(Room.room_type),
                selectinload(HousekeepingTask.checklist_items),
            )
            .where(
                HousekeepingTask.id == task_id,
                HousekeepingTask.hotel_id == hotel_id,
            )
        )
        result = await self.session.execute(stmt)
        task = result.unique().scalar_one_or_none()
        if not task:
            raise NotFoundException("Task not found", "TASK_NOT_FOUND")
        return await self._enrich_task(task, hotel_id)

    async def start_task(self, task_id: UUID, hotel_id: UUID, user_id: UUID) -> dict:
        task = await self._get_task(task_id, hotel_id)
        if task.status != "OPEN":
            raise ValidationException("Task can only be started from OPEN status", "INVALID_STATUS")
        from datetime import datetime, timezone
        task.status = "IN_PROGRESS"
        task.started_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(task)
        return await self._enrich_task(task, hotel_id)

    async def update_progress(self, task_id: UUID, hotel_id: UUID, progress: int) -> dict:
        task = await self._get_task(task_id, hotel_id)
        task.progress = progress
        if progress >= 100:
            from datetime import datetime, timezone
            task.status = "COMPLETED"
            task.completed_at = datetime.now(timezone.utc)
            room = await self.session.get(Room, task.room_id)
            if room and room.current_status == "CLEANING":
                room.current_status = "AVAILABLE"
        await self.session.flush()
        await self.session.refresh(task)
        return await self._enrich_task(task, hotel_id)

    async def toggle_checklist_item(self, task_id: UUID, item_id: UUID) -> dict:
        stmt = select(ChecklistItem).where(
            ChecklistItem.id == item_id,
            ChecklistItem.task_id == task_id,
        )
        result = await self.session.execute(stmt)
        item = result.scalar_one_or_none()
        if not item:
            raise NotFoundException("Checklist item not found", "ITEM_NOT_FOUND")

        item.is_completed = not item.is_completed
        await self.session.flush()

        task = await self._get_task(task_id, None)
        total = len(task.checklist_items)
        completed = sum(1 for i in task.checklist_items if i.is_completed)
        task.progress = int((completed / total) * 100) if total > 0 else 0

        if task.progress >= 100 and task.status != "COMPLETED":
            from datetime import datetime, timezone
            task.status = "COMPLETED"
            task.completed_at = datetime.now(timezone.utc)
            room = await self.session.get(Room, task.room_id)
            if room and room.current_status == "CLEANING":
                room.current_status = "AVAILABLE"

        await self.session.flush()
        await self.session.refresh(task)
        return await self._enrich_task(task, None)

    async def _get_task(self, task_id: UUID, hotel_id: UUID | None) -> HousekeepingTask:
        stmt = (
            select(HousekeepingTask)
            .options(
                selectinload(HousekeepingTask.room).selectinload(Room.floor),
                selectinload(HousekeepingTask.room).selectinload(Room.room_type),
                selectinload(HousekeepingTask.checklist_items),
            )
            .where(HousekeepingTask.id == task_id)
        )
        if hotel_id is not None:
            stmt = stmt.where(HousekeepingTask.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        task = result.unique().scalar_one_or_none()
        if not task:
            raise NotFoundException("Task not found", "TASK_NOT_FOUND")
        return task

    async def _enrich_task(self, task: HousekeepingTask, hotel_id: UUID | None) -> dict:
        room = task.room
        floor_name = f"{room.floor.floor_number}-qavat" if room and room.floor else ""
        room_type_name = room.room_type.name if room and room.room_type else ""

        guest_name = None
        guest_status = None
        if room:
            guest_info = await self._get_last_guest(room.id, hotel_id or task.hotel_id)
            if guest_info:
                guest_name, guest_status = guest_info

        checklist = sorted(task.checklist_items, key=lambda x: x.sort_order) if task.checklist_items else []

        return {
            "id": str(task.id),
            "room_number": room.room_number if room else "",
            "floor": floor_name,
            "room_type": room_type_name,
            "guest": guest_name,
            "guest_status": guest_status,
            "status": _MOBILE_STATUS_MAP.get(task.status, task.status.lower()),
            "progress": task.progress,
            "deadline": "14:00" if task.scheduled_date else None,
            "note": task.notes,
            "is_urgent": task.priority in ("HIGH", "URGENT"),
            "checklist": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "is_completed": item.is_completed,
                }
                for item in checklist
            ],
        }

    async def _get_last_guest(self, room_id: UUID, hotel_id: UUID) -> tuple[str, str] | None:
        stmt = (
            select(Reservation)
            .join(Guest, Reservation.guest_id == Guest.id)
            .where(
                Reservation.room_id == room_id,
                Reservation.hotel_id == hotel_id,
            )
            .order_by(Reservation.check_out_date.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        reservation = result.scalar_one_or_none()
        if reservation:
            guest = reservation.guest
            name = f"{guest.first_name[0]}. {guest.last_name}" if guest.first_name and guest.last_name else ""
            gs = "Band" if reservation.status == "CHECKED_IN" else "Bo'shatilgan"
            return name, gs
        return None
