from typing import Generic, Sequence, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

Model = TypeVar("Model", bound=DeclarativeBase)


class BaseRepository(Generic[Model]):
    model: type[Model]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, id: UUID) -> Model | None:
        stmt = select(self.model).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[Model]:
        stmt = select(self.model).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self, *filters) -> int:
        stmt = select(func.count()).select_from(self.model)
        for f in filters:
            stmt = stmt.where(f)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def create(self, instance: Model) -> Model:
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def update(self, instance: Model, **values) -> Model:
        for key, value in values.items():
            if value is not None and hasattr(instance, key):
                setattr(instance, key, value)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def delete(self, instance: Model, soft: bool = False) -> None:
        if soft and hasattr(instance, "is_deleted"):
            setattr(instance, "is_deleted", True)
            await self.session.flush()
        else:
            await self.session.delete(instance)
            await self.session.flush()


class TenantBaseRepository(BaseRepository[Model]):
    """Repository that auto-filters by hotel_id."""

    async def get_by_id(self, id: UUID, hotel_id: UUID) -> Model | None:
        stmt = select(self.model).where(
            self.model.id == id,
            self.model.hotel_id == hotel_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(
        self, hotel_id: UUID, skip: int = 0, limit: int = 100, **filters
    ) -> Sequence[Model]:
        stmt = select(self.model).where(self.model.hotel_id == hotel_id)
        for key, value in filters.items():
            if value is not None and hasattr(self.model, key):
                stmt = stmt.where(getattr(self.model, key) == value)
        stmt = stmt.offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_by_hotel(self, hotel_id: UUID, **filters) -> int:
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.hotel_id == hotel_id)
        )
        for key, value in filters.items():
            if value is not None and hasattr(self.model, key):
                stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_by_id_unscoped(self, id: UUID) -> Model | None:
        stmt = select(self.model).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_unscoped(
        self, skip: int = 0, limit: int = 100, **filters
    ) -> Sequence[Model]:
        stmt = select(self.model)
        for key, value in filters.items():
            if value is not None and hasattr(self.model, key):
                stmt = stmt.where(getattr(self.model, key) == value)
        stmt = stmt.offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()
