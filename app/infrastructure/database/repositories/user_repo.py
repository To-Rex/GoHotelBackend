from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.permission import Permission, UserPermission
from app.infrastructure.database.models.user import User
from app.infrastructure.database.models.user_session import UserSession
from app.infrastructure.database.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_employee(self, user_id: UUID, hotel_id: UUID) -> User | None:
        stmt = select(User).where(
            User.id == user_id,
            User.hotel_id == hotel_id,
            User.user_type == "EMPLOYEE",
            User.is_deleted.is_(False),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_admin(self, user_id: UUID, hotel_id: UUID) -> User | None:
        stmt = select(User).where(
            User.id == user_id,
            User.hotel_id == hotel_id,
            User.user_type == "ADMIN",
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_employees(
        self,
        hotel_id: UUID | None,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
    ) -> list[User]:
        stmt = select(User).where(User.user_type == "EMPLOYEE")
        if hotel_id is not None:
            stmt = stmt.where(User.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(User.status == status)
        stmt = stmt.offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_user_permissions(self, user_id: UUID) -> list[dict]:
        stmt = (
            select(Permission)
            .join(UserPermission, UserPermission.permission_id == Permission.id)
            .where(
                UserPermission.user_id == user_id,
                Permission.is_active.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        permissions = result.scalars().all()
        return [
            {"id": str(p.id), "code": p.code, "name": p.name, "module": p.module}
            for p in permissions
        ]

    async def assign_permissions(
        self,
        user_id: UUID,
        permission_ids: list[UUID],
        hotel_id: UUID,
        granted_by: UUID,
    ) -> None:
        unique_ids = list(dict.fromkeys(permission_ids))
        # user_permissions has UNIQUE(user_id, permission_id) regardless of
        # hotel, so rows granted under another hotel must go too or the
        # re-insert below would collide.
        existing_result = await self.session.execute(
            select(UserPermission).where(
                UserPermission.user_id == user_id,
                or_(
                    UserPermission.hotel_id == hotel_id,
                    UserPermission.permission_id.in_(unique_ids),
                ),
            )
        )
        for ep in existing_result.scalars().all():
            await self.session.delete(ep)
        # Flush deletes before re-inserting: within a single flush SQLAlchemy
        # emits INSERTs before DELETEs, tripping the unique constraint when a
        # permission is kept across the reassignment.
        await self.session.flush()

        for perm_id in unique_ids:
            up = UserPermission(
                user_id=user_id,
                permission_id=perm_id,
                hotel_id=hotel_id,
                granted_by=granted_by,
            )
            self.session.add(up)
        await self.session.flush()

    async def grant_permission(
        self,
        user_id: UUID,
        permission_id: UUID,
        hotel_id: UUID,
        granted_by: UUID,
    ) -> UserPermission:
        # UNIQUE(user_id, permission_id) — re-granting must stay idempotent.
        existing_result = await self.session.execute(
            select(UserPermission).where(
                UserPermission.user_id == user_id,
                UserPermission.permission_id == permission_id,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            return existing
        up = UserPermission(
            user_id=user_id,
            permission_id=permission_id,
            hotel_id=hotel_id,
            granted_by=granted_by,
        )
        self.session.add(up)
        await self.session.flush()
        return up

    async def revoke_permission(
        self, user_id: UUID, permission_id: UUID, hotel_id: UUID
    ) -> None:
        stmt = select(UserPermission).where(
            UserPermission.user_id == user_id,
            UserPermission.permission_id == permission_id,
            UserPermission.hotel_id == hotel_id,
        )
        result = await self.session.execute(stmt)
        up = result.scalar_one_or_none()
        if up:
            await self.session.delete(up)
            await self.session.flush()


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_session(self, **kwargs) -> UserSession:
        session = UserSession(**kwargs)
        self.session.add(session)
        await self.session.flush()
        return session

    async def get_by_jti(self, jti: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.token_jti == jti)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke_session(self, jti: str) -> None:
        session = await self.get_by_jti(jti)
        if session:
            session.revoked_at = datetime.now(timezone.utc)
            await self.session.flush()
