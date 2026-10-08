from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.notification import Notification

#: Xabar qaysi yozuv haqida — filialini shu yozuvdan olamiz (fon vazifalarida
#: so'rov filiali yo'q). Kalit — `entity_type` boshlanishi.
_ENTITY_MODELS = (
    ("task", "app.infrastructure.database.models.housekeeping", "HousekeepingTask"),
    ("housekeeping", "app.infrastructure.database.models.housekeeping", "HousekeepingTask"),
    ("document_scan", "app.infrastructure.database.models.document_scan", "DocumentScan"),
    ("feedback", "app.infrastructure.database.models.guest_feedback", "GuestFeedback"),
    ("problem", "app.infrastructure.database.models.problem", "Problem"),
    ("debt", "app.infrastructure.database.models.reservation", "Reservation"),
    ("reservation", "app.infrastructure.database.models.reservation", "Reservation"),
    ("checkout", "app.infrastructure.database.models.reservation", "Reservation"),
    ("shift", "app.infrastructure.database.models.shift", "ShiftSession"),
    ("call", "app.infrastructure.database.models.incoming_call", "IncomingCall"),
    ("message", "app.infrastructure.database.models.staff_message", "StaffMessage"),
    ("sighting", "app.infrastructure.database.models.face_sighting", "FaceSighting"),
    ("shop", "app.infrastructure.database.models.shop", "ShopSale"),
    ("room", "app.infrastructure.database.models.room", "Room"),
)


async def notification_branch(
    session: AsyncSession,
    hotel_id: UUID,
    user_id: UUID | None,
    entity_type: str | None,
    entity_id: UUID | None,
) -> UUID | None:
    """Xabar filiali: so'rov filiali -> xabar haqidagi yozuv filiali ->
    oluvchi xodim filiali. Topilmasa — asosiy filial (branch_scope)."""
    import importlib

    from app.infrastructure.tenant.branch_scope import ALL_BRANCHES, scoped_branch_id

    scoped = scoped_branch_id(session, hotel_id)
    if scoped is not None:
        return scoped
    if entity_id is not None and entity_type:
        kind = entity_type.lower()
        for prefix, module, name in _ENTITY_MODELS:
            if not kind.startswith(prefix):
                continue
            model = getattr(importlib.import_module(module), name)
            row = await session.get(model, entity_id, execution_options={ALL_BRANCHES: True})
            if row is not None and getattr(row, "branch_id", None) is not None:
                return row.branch_id
            break
    if user_id is not None:
        from app.infrastructure.database.models.user import User

        user = await session.get(User, user_id)
        if user is not None and user.user_type == "EMPLOYEE" and user.branch_id:
            return user.branch_id
    return None


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
        branch_id: UUID | None = None,
    ) -> Notification:
        notif = Notification(
            hotel_id=hotel_id,
            branch_id=branch_id
            or await notification_branch(self.session, hotel_id, user_id, entity_type, entity_id),
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
