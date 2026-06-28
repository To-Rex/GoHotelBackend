from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.repositories.base import BaseRepository


class HotelRepository(BaseRepository[Hotel]):
    model = Hotel

    async def get_by_code(self, code: str) -> Hotel | None:
        stmt = select(Hotel).where(Hotel.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_active(self, skip: int = 0, limit: int = 100) -> list[Hotel]:
        stmt = (
            select(Hotel)
            .where(Hotel.status == "ACTIVE")
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
