import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.user import User
from app.infrastructure.database.repositories.notification_repo import (
    NotificationRepository,
)
from app.infrastructure.push import firebase

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = NotificationRepository(session)

    # --------------------------------------------------------------- tokens --
    async def _get_user_token(self, user_id: UUID) -> str | None:
        """Foydalanuvchining FCM device tokenini qaytaradi (bo'lmasa None)."""
        result = await self.session.execute(
            select(User.fcm_token).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def _get_hotel_tokens(self, hotel_id: UUID) -> list[str]:
        """Mehmonxonaning barcha faol foydalanuvchilarining FCM tokenlari."""
        result = await self.session.execute(
            select(User.fcm_token).where(
                User.hotel_id == hotel_id,
                User.fcm_token.is_not(None),
                User.is_deleted.is_(False),
                User.status == "ACTIVE",
            )
        )
        return [t for t in result.scalars().all() if t]

    async def _push_to_user(
        self,
        user_id: UUID,
        title: str,
        body: str | None,
        entity_type: str | None,
        entity_id: UUID | None,
    ) -> None:
        """Bitta foydalanuvchiga push yuboradi (xato bo'lsa jimgina o'tadi)."""
        try:
            token = await self._get_user_token(user_id)
            if not token:
                return
            await firebase.send_push(
                token,
                title,
                body,
                data=self._build_data(entity_type, entity_id),
            )
        except Exception:
            logger.exception("Push yuborishda xato (user_id=%s)", user_id)

    @staticmethod
    def _build_data(entity_type: str | None, entity_id: UUID | None) -> dict:
        data: dict = {}
        if entity_type:
            data["entity_type"] = entity_type
        if entity_id:
            data["entity_id"] = str(entity_id)
        return data

    # ---------------------------------------------------------------- notify --
    async def notify(
        self,
        hotel_id: UUID,
        user_id: UUID | None,
        title: str,
        body: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        send_push: bool = True,
    ):
        """DB'ga notification yozadi va (user_id bo'lsa) FCM push yuboradi.

        Push xatosi hech qachon DB yozuvini yoki chaqiruvchi oqimni buzmaydi.
        """
        notif = await self.repo.create(
            hotel_id, user_id, title, body, entity_type, entity_id
        )
        if send_push and user_id is not None:
            await self._push_to_user(user_id, title, body, entity_type, entity_id)
        return notif

    async def notify_broadcast(
        self,
        hotel_id: UUID,
        title: str,
        body: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        send_push: bool = False,
    ):
        """Mehmonxona bo'yicha umumiy (broadcast) notification.

        send_push standart holda False — mavjud chaqiruvchilar xatti-harakati
        o'zgarmaydi. Yangi "broadcast push" API'si buni ochiq True qilib chaqiradi.
        """
        notif = await self.repo.create(
            hotel_id, None, title, body, entity_type, entity_id
        )
        if send_push:
            try:
                tokens = await self._get_hotel_tokens(hotel_id)
                if tokens:
                    await firebase.send_push(
                        tokens, title, body, data=self._build_data(entity_type, entity_id)
                    )
            except Exception:
                logger.exception("Broadcast push yuborishda xato (hotel_id=%s)", hotel_id)
        return notif

    # --------------------------------------------- explicit send (yangi API) --
    async def send_to_user(
        self,
        hotel_id: UUID,
        user_id: UUID,
        title: str,
        body: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
    ) -> tuple:
        """Bitta foydalanuvchiga DB notification + push yuboradi.

        (notification, yuborilgan_push_soni) qaytaradi.
        """
        notif = await self.repo.create(
            hotel_id, user_id, title, body, entity_type, entity_id
        )
        sent = 0
        token = await self._get_user_token(user_id)
        if token:
            sent = await firebase.send_push(
                token, title, body, data=self._build_data(entity_type, entity_id)
            )
        return notif, sent

    async def send_to_hotel(
        self,
        hotel_id: UUID,
        title: str,
        body: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
    ) -> tuple:
        """Mehmonxonaning barcha faol foydalanuvchilariga DB broadcast + push.

        (notification, yuborilgan_push_soni) qaytaradi.
        """
        notif = await self.repo.create(
            hotel_id, None, title, body, entity_type, entity_id
        )
        sent = 0
        tokens = await self._get_hotel_tokens(hotel_id)
        if tokens:
            sent = await firebase.send_push(
                tokens, title, body, data=self._build_data(entity_type, entity_id)
            )
        return notif, sent

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
