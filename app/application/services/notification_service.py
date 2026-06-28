from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.repositories.notification_repo import (
    NotificationRepository,
)


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.repo = NotificationRepository(session)

    async def notify(
        self,
        hotel_id: UUID,
        user_id: UUID | None,
        title: str,
        body: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
    ):
        return await self.repo.create(
            hotel_id, user_id, title, body, entity_type, entity_id
        )

    async def notify_broadcast(
        self,
        hotel_id: UUID,
        title: str,
        body: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
    ):
        return await self.repo.create(
            hotel_id, None, title, body, entity_type, entity_id
        )

    async def get_my_notifications(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 50,
        unread_only: bool = False,
    ):
        return await self.repo.get_user_notifications(
            user_id, skip, limit, unread_only
        )

    async def mark_read(self, notification_id: UUID):
        await self.repo.mark_as_read(notification_id)

    async def get_hotel_notifications(
        self, hotel_id: UUID | None, skip: int = 0, limit: int = 50
    ):
        return await self.repo.get_hotel_broadcasts(hotel_id, skip, limit)
