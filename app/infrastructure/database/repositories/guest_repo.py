from datetime import datetime, timezone
from uuid import UUID

from typing import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.repositories.base import TenantBaseRepository


class GuestRepository(TenantBaseRepository[Guest]):
    model = Guest

    async def get_all(
        self, hotel_id: UUID, skip: int = 0, limit: int = 100, **filters
    ) -> Sequence[Guest]:
        stmt = select(Guest).where(Guest.hotel_id == hotel_id)
        for key, value in filters.items():
            if value is not None and hasattr(Guest, key):
                stmt = stmt.where(getattr(Guest, key) == value)
        stmt = stmt.order_by(Guest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search(
        self, hotel_id: UUID | None, query: str, skip: int = 0, limit: int = 100
    ) -> list[Guest]:
        stmt = select(Guest).where(
            Guest.is_deleted.is_(False),
            or_(
                Guest.first_name.ilike(f"%{query}%"),
                Guest.last_name.ilike(f"%{query}%"),
                Guest.phone.ilike(f"%{query}%"),
                Guest.email.ilike(f"%{query}%"),
                Guest.passport_number.ilike(f"%{query}%"),
            ),
        )
        if hotel_id is not None:
            stmt = stmt.where(Guest.hotel_id == hotel_id)
        stmt = stmt.order_by(Guest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_passport(
        self, hotel_id: UUID, passport_number: str
    ) -> Guest | None:
        stmt = select(Guest).where(
            Guest.hotel_id == hotel_id,
            Guest.passport_number == passport_number,
            Guest.is_deleted.is_(False),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(self, guest_id: UUID, hotel_id: UUID) -> Guest | None:
        guest = await self.get_by_id(guest_id, hotel_id)
        if guest:
            guest.is_deleted = True
            guest.deleted_at = datetime.now(timezone.utc)
            await self.session.flush()
        return guest
