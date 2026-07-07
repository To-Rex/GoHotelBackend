from uuid import UUID
from datetime import date, datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.repositories.housekeeping_repo import HousekeepingRepository
from app.infrastructure.database.repositories.room_repo import RoomRepository


class HousekeepingService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = HousekeepingRepository(session)
        self.room_repo = RoomRepository(session)

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
        return await self.repo.create(task)

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
        from sqlalchemy import select as sa_select
        stmt = sa_select(HousekeepingTask)
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
        return list(result.scalars().all())

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
            task = await self.repo.get_by_id_unscoped(task_id)
        else:
            task = await self.repo.get_by_id(task_id, hotel_id)
        if not task:
            raise NotFoundException("Task not found", "TASK_NOT_FOUND")
        return task

    async def update_task(self, task_id: UUID, hotel_id: UUID, data: dict) -> HousekeepingTask:
        task = await self.get_task(task_id, hotel_id)
        updatable = ["task_type", "priority", "assigned_to", "notes", "scheduled_date"]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        return await self.repo.update(task, **update_data)

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
        return await self.repo.assign_task(task, user_id)
