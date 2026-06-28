from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.notification import Notification


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        hotel_id: UUID,
        user_id: UUID | None,
        title: str,
        body: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
    ) -> Notification:
        notif = Notification(
            hotel_id=hotel_id,
            user_id=user_id,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        self.session.add(notif)
        await self.session.flush()
        return notif

    async def get_user_notifications(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.is_read == False)
        stmt = (
            stmt.order_by(Notification.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_as_read(self, notification_id: UUID) -> None:
        stmt = (
            update(Notification)
            .where(Notification.id == notification_id)
            .values(is_read=True)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_hotel_broadcasts(
        self, hotel_id: UUID | None, skip: int = 0, limit: int = 50
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == None)
        if hotel_id is not None:
            stmt = stmt.where(Notification.hotel_id == hotel_id)
        stmt = (
            stmt.order_by(Notification.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
