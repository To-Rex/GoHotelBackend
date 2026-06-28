"""
Authentication service — handles login, token creation, and token refresh.
Unified login: checks username against the single `users` table.
JWT contains: sub, user_type, hotel_id, branch_id, permissions[], jti, type.
"""
from uuid import UUID
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import UnauthorizedException, ForbiddenException
from app.infrastructure.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.infrastructure.auth.password import verify_password
from app.infrastructure.database.repositories.user_repo import UserRepository, SessionRepository
from app.shared.utils import generate_jti


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
    ) -> dict:
        user = await self.user_repo.get_by_username(username)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedException("Invalid username or password", "INVALID_CREDENTIALS")

        if user.status != "ACTIVE":
            raise ForbiddenException("Account is not active", "ACCOUNT_INACTIVE")

        if user.is_deleted:
            raise ForbiddenException("Account has been deleted", "ACCOUNT_DELETED")

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

        return {
            "id": str(user.id),
            "user_type": user.user_type,
            "hotel_id": str(user.hotel_id) if user.hotel_id else None,
            "branch_id": str(user.branch_id) if user.branch_id else None,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "status": user.status,
            "permissions": permissions,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        }
