from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.repositories.audit_repo import AuditRepository


class AuditService:
    def __init__(self, session: AsyncSession):
        self.repo = AuditRepository(session)

    async def log_action(
        self,
        hotel_id: UUID,
        user_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID,
        old_values: dict | None = None,
        new_values: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ):
        await self.repo.log(
            hotel_id=hotel_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def get_logs(self, hotel_id: UUID | None, **filters):
        return await self.repo.query_logs(hotel_id, **filters)
