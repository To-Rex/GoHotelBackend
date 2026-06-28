from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.repositories.base import TenantBaseRepository


class BranchRepository(TenantBaseRepository[Branch]):
    model = Branch
    
    async def get_all_unscoped(self, skip: int = 0, limit: int = 100) -> list[Branch]:
        stmt = select(Branch).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_by_id_unscoped(self, id: UUID) -> Branch | None:
        stmt = select(Branch).where(Branch.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_main_branch(self, hotel_id: UUID) -> Branch | None:
        stmt = select(Branch).where(
            Branch.hotel_id == hotel_id,
            Branch.is_main_branch.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_code(self, hotel_id: UUID, code: str) -> Branch | None:
        stmt = select(Branch).where(
            Branch.hotel_id == hotel_id,
            Branch.code == code,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
