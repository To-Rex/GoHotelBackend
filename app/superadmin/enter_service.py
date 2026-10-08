"""Paneldan mehmonxonaga "kirish" — asosiy tizimni sozlovchi huquqida ochish.

Panel egasi istalgan mehmonxonaning istalgan filialini bir bosishda
ochadi va u yerda HAMMA narsani boshqaradi: sozlamalar, xonalar, xodimlar
va ruxsatlar, kameralar, qurilmalar, do'kon, moliya... Panelda bu
sahifalarni qayta yozish o'rniga asosiy tizimning o'zi ochiladi.

Qanday: har panel foydalanuvchisi uchun asosiy tizimda YASHIRIN sozlovchi
hisobi (`users.user_type = CONFIGURATOR`, login `panel__<id>`) yuritiladi.
Unga parol bilan kirib bo'lmaydi (parol tasodifiy), u sozlovchilar
ro'yxatida ko'rinmaydi. Kirishda shu hisob nomidan tanlangan mehmonxona va
filial bilan token juftligi beriladi (`AuthService.issue_tokens`) — xuddi
sozlovchi `POST /auth/context` qilgandek. Panel foydalanuvchisi
to'xtatilsa yoki o'chirilsa, yashirin hisob ham to'xtatiladi va uning
sessiyalari yopiladi — token darhol ishlamay qoladi (sozlovchi tokeni har
so'rovda sessiya bilan tekshiriladi).
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.auth.password import hash_password
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User
from app.infrastructure.database.models.user_session import UserSession
from app.superadmin.models import PanelUser

logger = logging.getLogger(__name__)

#: Yashirin sozlovchi loginlarining boshlanishi — ro'yxatlardan chiqariladi
HIDDEN_PREFIX = "panel__"


def hidden_username(panel_user_id: UUID) -> str:
    return f"{HIDDEN_PREFIX}{panel_user_id.hex[:12]}"


def is_hidden_username(username: str | None) -> bool:
    return bool(username) and username.startswith(HIDDEN_PREFIX)


class EnterService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _hidden_configurator(self, actor: PanelUser) -> User:
        """Panel foydalanuvchisining yashirin sozlovchi hisobi (yo'q bo'lsa ochiladi)."""
        username = hidden_username(actor.id)
        user = (
            await self.session.execute(select(User).where(User.username == username))
        ).scalars().first()
        label = (actor.label or "Tizim egasi").strip()[:100]
        if user is None:
            user = User(
                user_type="CONFIGURATOR",
                hotel_id=None,
                branch_id=None,
                username=username,
                # Parol bilan kirib bo'lmaydi — faqat paneldan token beriladi
                password_hash=hash_password(secrets.token_urlsafe(32)),
                first_name=label,
                last_name="(panel)",
                status="ACTIVE",
            )
            self.session.add(user)
            await self.session.flush()
            logger.info("Panel uchun yashirin sozlovchi ochildi: %s", username)
        else:
            if user.is_deleted:
                user.is_deleted = False
                user.deleted_at = None
            user.status = "ACTIVE"
            user.first_name = label
            await self.session.flush()
        return user

    async def enter(
        self,
        actor: PanelUser,
        hotel_id: UUID,
        branch_id: UUID | None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Tanlangan mehmonxona/filial uchun asosiy tizim tokenlari va profil."""
        from app.application.services.auth_service import AuthService

        hotel = await self.session.get(Hotel, hotel_id)
        if hotel is None:
            raise NotFoundException("Mehmonxona topilmadi", "HOTEL_NOT_FOUND")
        branches = (
            await self.session.execute(
                select(Branch)
                .where(Branch.hotel_id == hotel_id)
                .order_by(Branch.is_main_branch.desc(), Branch.created_at, Branch.id)
            )
        ).scalars().all()
        if branch_id is not None:
            if not any(b.id == branch_id for b in branches):
                raise ValidationException("Filial bu mehmonxonaga tegishli emas", "BRANCH_NOT_IN_HOTEL")
        elif branches:
            branch_id = branches[0].id

        user = await self._hidden_configurator(actor)
        auth = AuthService(self.session)
        tokens = await auth.issue_tokens(
            user,
            ip_address=ip_address,
            user_agent=user_agent,
            context=(hotel_id, branch_id),
        )
        me = await auth.get_me(user.id, hotel_id, branch_id)
        logger.warning(
            "Panel foydalanuvchisi mehmonxonaga kirdi: %s -> %s / %s (filial %s)",
            actor.label or actor.id, hotel.name, hotel.code, branch_id,
        )
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "user": me,
            "hotel": {"id": str(hotel.id), "name": hotel.name, "code": hotel.code},
        }

    async def disable_for(self, panel_user_id: UUID) -> None:
        """Panel foydalanuvchisi to'xtatilganda/o'chirilganda yashirin hisob
        ham to'xtaydi va sessiyalari yopiladi."""
        username = hidden_username(panel_user_id)
        user = (
            await self.session.execute(select(User).where(User.username == username))
        ).scalars().first()
        if user is None:
            return
        user.status = "INACTIVE"
        await self.session.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await self.session.flush()
