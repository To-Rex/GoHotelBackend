"""Paneldan e'lon — barcha mehmonxonalarga yoki tanlangan mehmonxona/filialga.

Egasi texnik ishlar, yangi versiya yoki muhim xabarni bir joydan yuboradi.
Oluvchilar: faqat administratorlar yoki filialning barcha faol xodimlari.
Har oluvchiga bildirishnoma (ilovadagi ro'yxat) yoziladi va xohlasa push
ketadi. Xabar har filial ichida yuboriladi (filiallar ajratilgan).
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User
from app.infrastructure.tenant.branch_scope import set_branch_scope

logger = logging.getLogger(__name__)

AUDIENCES = ("admins", "staff")


class BroadcastService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def targets(self, hotel_id: UUID | None, branch_id: UUID | None) -> list[tuple[Hotel, Branch]]:
        """(mehmonxona, filial) juftliklari: hammasi / bitta mehmonxona / bitta filial."""
        stmt = (
            select(Hotel, Branch)
            .join(Branch, Branch.hotel_id == Hotel.id)
            .where(Hotel.status == "ACTIVE")
            .order_by(Hotel.name, Branch.is_main_branch.desc(), Branch.name)
        )
        if hotel_id is not None:
            if await self.session.get(Hotel, hotel_id) is None:
                raise NotFoundException("Mehmonxona topilmadi", "HOTEL_NOT_FOUND")
            stmt = stmt.where(Hotel.id == hotel_id)
        if branch_id is not None:
            stmt = stmt.where(Branch.id == branch_id)
        rows = (await self.session.execute(stmt)).all()
        if branch_id is not None and not rows:
            raise NotFoundException("Filial topilmadi", "BRANCH_NOT_FOUND")
        return [(h, b) for h, b in rows]

    async def _recipients(self, hotel: Hotel, branch: Branch, audience: str) -> list[User]:
        stmt = select(User).where(
            User.hotel_id == hotel.id,
            User.is_deleted.is_(False),
            User.status == "ACTIVE",
        )
        if audience == "admins":
            stmt = stmt.where(User.user_type == "ADMIN")
        else:
            # Filial xodimlari va mehmonxona administratorlari
            stmt = stmt.where((User.branch_id == branch.id) | (User.user_type == "ADMIN"))
        return list((await self.session.execute(stmt)).scalars().all())

    async def send(
        self,
        *,
        title: str,
        body: str | None,
        audience: str,
        hotel_id: UUID | None = None,
        branch_id: UUID | None = None,
        send_push: bool = True,
        actor: str | None = None,
    ) -> dict:
        from app.application.services.notification_service import NotificationService

        title = (title or "").strip()
        if not title:
            raise ValidationException("Sarlavha kerak", "TITLE_REQUIRED")
        if audience not in AUDIENCES:
            raise ValidationException("Noma'lum auditoriya", "INVALID_AUDIENCE")
        if branch_id is not None and hotel_id is None:
            raise ValidationException("Filial uchun mehmonxona ham kerak", "HOTEL_REQUIRED")

        notifier = NotificationService(self.session)
        sent_hotels: set = set()
        notified = 0
        seen_users: set = set()
        for hotel, branch in await self.targets(hotel_id, branch_id):
            # Xabar filial ichida yoziladi (bildirishnoma o'sha filialniki)
            set_branch_scope(self.session, hotel.id, branch.id)
            try:
                for user in await self._recipients(hotel, branch, audience):
                    # Administrator bir necha filialda takror chiqmasin
                    if user.id in seen_users and user.user_type == "ADMIN":
                        continue
                    seen_users.add(user.id)
                    await notifier.notify(
                        hotel_id=hotel.id,
                        user_id=user.id,
                        title=title,
                        body=(body or "").strip() or None,
                        entity_type="announcement",
                        entity_id=None,
                        send_push=send_push,
                    )
                    notified += 1
                sent_hotels.add(hotel.id)
            finally:
                set_branch_scope(self.session, None, None)
        logger.warning(
            "Panel e'loni: %r -> %s mehmonxona, %s oluvchi (auditoriya %s); yubordi: %s",
            title, len(sent_hotels), notified, audience, actor or "?",
        )
        return {"hotels": len(sent_hotels), "recipients": notified, "push": bool(send_push)}
