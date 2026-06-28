import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.repositories.base import TenantBaseRepository


class HousekeepingRepository(TenantBaseRepository[HousekeepingTask]):
    model = HousekeepingTask

    async def get_tasks_by_room(
        self, hotel_id: UUID, room_id: UUID, skip: int = 0, limit: int = 50
    ) -> list[HousekeepingTask]:
        stmt = (
            select(HousekeepingTask)
            .where(
                HousekeepingTask.hotel_id == hotel_id,
                HousekeepingTask.room_id == room_id,
            )
            .order_by(HousekeepingTask.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_tasks_by_assignee(
        self,
        hotel_id: UUID | None,
        user_id: UUID,
        status: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[HousekeepingTask]:
        stmt = select(HousekeepingTask).where(
            HousekeepingTask.assigned_to == user_id,
        )
        if hotel_id is not None:
            stmt = stmt.where(HousekeepingTask.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(HousekeepingTask.status == status)
        stmt = (
            stmt.order_by(HousekeepingTask.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_open_tasks(
        self,
        hotel_id: UUID | None,
        branch_id: UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[HousekeepingTask]:
        stmt = select(HousekeepingTask).where(
            HousekeepingTask.status.in_(["OPEN", "IN_PROGRESS"]),
        )
        if hotel_id is not None:
            stmt = stmt.where(HousekeepingTask.hotel_id == hotel_id)
        if branch_id:
            stmt = stmt.where(HousekeepingTask.branch_id == branch_id)
        stmt = (
            stmt.order_by(
                HousekeepingTask.priority.desc(), HousekeepingTask.created_at
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self, task: HousekeepingTask, status: str, user_id: UUID
    ) -> HousekeepingTask:
        task.status = status
        if status == "IN_PROGRESS":
            task.started_at = datetime.datetime.now(datetime.UTC)
        elif status == "COMPLETED":
            task.completed_at = datetime.datetime.now(datetime.UTC)
        await self.session.flush()
        return task

    async def assign_task(
        self, task: HousekeepingTask, user_id: UUID
    ) -> HousekeepingTask:
        task.assigned_to = user_id
        await self.session.flush()
        return task
