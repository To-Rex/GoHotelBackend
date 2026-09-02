"""
Authentication service — handles login, token creation, and token refresh.
Unified login: checks username against the single `users` table.
JWT contains: sub, user_type, hotel_id, branch_id, permissions[], jti, type.
"""
import logging
from uuid import UUID
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import UnauthorizedException, ForbiddenException
from app.infrastructure.auth.jwt import (
    FACE_CHALLENGE_EXPIRE_MINUTES,
    create_access_token,
    create_face_challenge_token,
    create_refresh_token,
    decode_face_challenge_token,
    decode_token,
)
from app.infrastructure.auth.password import verify_password
from app.infrastructure.database.repositories.user_repo import UserRepository, SessionRepository
from app.shared.utils import generate_jti


logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.session_repo = SessionRepository(session)

    async def login(
        self,
        username: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        fcm_token: str | None = None,
        device_id: str | None = None,
    ) -> dict:
        user = await self.user_repo.get_by_username(username)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedException("Invalid username or password", "INVALID_CREDENTIALS")

        if user.status != "ACTIVE":
            raise ForbiddenException("Account is not active", "ACCOUNT_INACTIVE")

        if user.is_deleted:
            raise ForbiddenException("Account has been deleted", "ACCOUNT_DELETED")

        # --- Qurilma tasdiqlangan bo'lishi kerak ---
        #
        # Yuz tekshiruvidan OLDIN: tasdiqlanmagan qurilmada yuz so'rashning
        # ma'nosi yo'q, u baribir kiritmaydi. Administrator bu tekshiruvdan
        # ozod — batafsil izoh device_service da.
        from app.application.services.device_service import DeviceService

        await DeviceService(self.session).ensure_allowed(
            user, device_id, user_agent=user_agent, ip_address=ip_address
        )

        # FCM token faqat so'rovda kelganda yangilanadi — token yubormaydigan
        # klientlar (masalan web) mavjud tokenni o'chirib yubormasligi uchun.
        if fcm_token:
            user.fcm_token = fcm_token

        # --- Ikkinchi bosqich: yuz tekshiruvi ---
        #
        # Yuz biriktirgan xodim uchun parol YETARLI EMAS. Tokenlar bu yerda
        # berilmaydi — o'rniga qisqa muddatli challenge qaytadi va kirish
        # `/face/verify-login` da yakunlanadi. Yuzi yo'q xodim uchun hech
        # narsa o'zgarmaydi: parol avvalgidek yetarli.
        if await self._has_face_profile(user.id):
            return {
                "face_required": True,
                "face_token": create_face_challenge_token(str(user.id)),
                "face_expires_in": FACE_CHALLENGE_EXPIRE_MINUTES * 60,
            }

        return await self.issue_tokens(user, ip_address=ip_address, user_agent=user_agent)

    async def _has_face_profile(self, user_id) -> bool:
        """Xodimda yuz biriktirilganmi."""
        from sqlalchemy import func, select

        from app.infrastructure.database.models.user_face_profile import (
            UserFaceProfile,
        )

        count = (
            await self.session.execute(
                select(func.count(UserFaceProfile.id)).where(
                    UserFaceProfile.user_id == user_id
                )
            )
        ).scalar() or 0
        return count > 0

    async def resolve_face_challenge(self, face_token: str):
        """Challenge tokendan xodimni topadi va holatini qayta tekshiradi.

        Qayta tekshirish kerak: token berilgandan keyin xodim o'chirilgan yoki
        bloklangan bo'lishi mumkin, besh daqiqa ham buning uchun yetarli.
        """
        user_id = decode_face_challenge_token(face_token)
        if not user_id:
            raise UnauthorizedException(
                "Kirish seansi tugadi — login va parolni qaytadan kiriting",
                "FACE_CHALLENGE_INVALID",
            )
        user = await self.user_repo.get_by_id(UUID(str(user_id)))
        if not user or user.is_deleted or user.status != "ACTIVE":
            raise ForbiddenException("Account is not active", "ACCOUNT_INACTIVE")
        return user

    async def complete_login_without_face(
        self,
        face_token: str,
        reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Kamerasiz qurilmada ikkinchi bosqichni o'tkazib yuborish."""
        user = await self.resolve_face_challenge(face_token)
        note = reason or "kamera topilmadi"
        logger.info(
            "Yuz tekshiruvisiz kirish: %s (sabab: %s)", user.username, note
        )
        # Sabab sessiya yozuviga tushadi — keyin kim qaysi yo'l bilan
        # kirgani ko'rinadi
        return await self.issue_tokens(
            user,
            ip_address=ip_address,
            user_agent=f"{user_agent or ''} [yuzsiz: {note}]".strip(),
        )

    async def issue_tokens(
        self,
        user,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Access/refresh token juftligini yaratib, sessiya yozuvini saqlaydi.

        Parol bilan login va WebAuthn (Face ID/passkey) login uchun umumiy —
        ikkalasi ham foydalanuvchi allaqachon tekshirilgach shu yerga keladi.
        """
        permissions: list[str] = []
        if user.user_type == "EMPLOYEE":
            permissions = [p["code"] for p in await self.user_repo.get_user_permissions(user.id)]

        user.last_login_at = datetime.now(timezone.utc)

        jti = generate_jti()
        token_data = {
            "sub": str(user.id),
            "user_type": user.user_type,
            "hotel_id": str(user.hotel_id) if user.hotel_id else None,
            "branch_id": str(user.branch_id) if user.branch_id else None,
            "permissions": permissions,
            "jti": jti,
        }

        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        await self.session_repo.create_session(
            user_id=user.id,
            token_jti=jti,
            refresh_token_hash=refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "id": str(user.id),
                "user_type": user.user_type,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "hotel_id": str(user.hotel_id) if user.hotel_id else None,
                "branch_id": str(user.branch_id) if user.branch_id else None,
                "permissions": permissions,
            },
        }

    async def refresh_token(self, refresh_token: str) -> dict:
        payload = decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise UnauthorizedException("Invalid refresh token", "INVALID_TOKEN")

        jti = payload.get("jti")
        session = await self.session_repo.get_by_jti(jti)
        if not session or session.revoked_at:
            raise UnauthorizedException("Session revoked", "SESSION_REVOKED")

        session.revoked_at = datetime.now(timezone.utc)

        user_id = UUID(payload["sub"])
        user = await self.user_repo.get_by_id(user_id)
        if not user or user.status != "ACTIVE":
            raise UnauthorizedException("User not active", "USER_INACTIVE")

        permissions: list[str] = []
        if user.user_type == "EMPLOYEE":
            permissions = [p["code"] for p in await self.user_repo.get_user_permissions(user.id)]

        new_jti = generate_jti()
        token_data = {
            "sub": str(user.id),
            "user_type": user.user_type,
            "hotel_id": str(user.hotel_id) if user.hotel_id else None,
            "branch_id": str(user.branch_id) if user.branch_id else None,
            "permissions": permissions,
            "jti": new_jti,
        }

        new_access = create_access_token(token_data)
        new_refresh = create_refresh_token(token_data)

        await self.session_repo.create_session(
            user_id=user.id,
            token_jti=new_jti,
            refresh_token_hash=new_refresh,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        )

        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
            "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

    async def logout(self, jti: str) -> None:
        await self.session_repo.revoke_session(jti)

    async def get_me(self, user_id: UUID) -> dict:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UnauthorizedException("User not found", "USER_NOT_FOUND")

        permissions: list[str] = []
        if user.user_type == "EMPLOYEE":
            permissions = [p["code"] for p in await self.user_repo.get_user_permissions(user.id)]

        # Foydalanuvchi mehmonxonasining nomi (frontend tab sarlavhasi uchun).
        # SUPER_ADMIN da hotel_id bo'lmasligi mumkin — None qoladi.
        hotel_name: str | None = None
        if user.hotel_id:
            from sqlalchemy import select
            from app.infrastructure.database.models.hotel import Hotel

            result = await self.session.execute(
                select(Hotel.name).where(Hotel.id == user.hotel_id)
            )
            hotel_name = result.scalar_one_or_none()

        return {
            "id": str(user.id),
            "user_type": user.user_type,
            "hotel_id": str(user.hotel_id) if user.hotel_id else None,
            "hotel_name": hotel_name,
            "branch_id": str(user.branch_id) if user.branch_id else None,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "status": user.status,
            "permissions": permissions,
            "work_hours_per_day": user.work_hours_per_day or 8,
            "work_start": user.work_start or "09:00",
            "work_end": user.work_end or "18:00",
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        }
