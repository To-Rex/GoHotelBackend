from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.infrastructure.auth.password import hash_password
from app.infrastructure.database.models.permission import Permission
from app.infrastructure.database.models.user import User
from app.infrastructure.database.repositories.user_repo import UserRepository


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = UserRepository(session)

    async def create_admin(self, data: dict, hotel_id: UUID) -> User:
        existing = await self.repo.get_by_username(data["username"])
        if existing:
            raise ConflictException("Username already exists", "USERNAME_EXISTS")

        user = User(
            user_type="ADMIN",
            hotel_id=hotel_id,
            username=data["username"],
            password_hash=hash_password(data["password"]),
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data.get("email"),
            phone=data.get("phone"),
            status="ACTIVE",
        )
        return await self.repo.create(user)

    async def create_employee(self, data: dict) -> User:
        existing = await self.repo.get_by_username(data["username"])
        if existing:
            raise ConflictException("Username already exists", "USERNAME_EXISTS")

        user = User(
            user_type="EMPLOYEE",
            hotel_id=data["hotel_id"],
            branch_id=data["branch_id"],
            username=data["username"],
            password_hash=hash_password(data["password"]),
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data.get("email"),
            phone=data.get("phone"),
            hire_date=data.get("hire_date"),
            status="ACTIVE",
        )
        return await self.repo.create(user)

    async def get_employees(
        self, hotel_id: UUID | None, skip: int = 0, limit: int = 100, status: str | None = None
    ) -> list[User]:
        if hotel_id is None:
            return await self.repo.get_employees(None, skip, limit, status)
        return await self.repo.get_employees(hotel_id, skip, limit, status)

    async def get_employee(self, user_id: UUID, hotel_id: UUID | None) -> User:
        if hotel_id is None:
            user = await self.repo.get_by_id(user_id)
        else:
            user = await self.repo.get_employee(user_id, hotel_id)
        if not user:
            raise NotFoundException("Employee not found", "EMPLOYEE_NOT_FOUND")
        return user

    async def update_employee(self, user_id: UUID, hotel_id: UUID, data: dict) -> User:
        user = await self.get_employee(user_id, hotel_id)
        updatable = ["first_name", "last_name", "email", "phone", "branch_id", "status"]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        return await self.repo.update(user, **update_data)

    async def delete_employee(self, user_id: UUID, hotel_id: UUID) -> User:
        user = await self.get_employee(user_id, hotel_id)
        user.is_deleted = True
        user.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return user

    async def get_user_permissions(self, user_id: UUID) -> list[dict]:
        return await self.repo.get_user_permissions(user_id)

    async def assign_permissions(
        self, user_id: UUID, permission_ids: list[UUID], hotel_id: UUID, granted_by: UUID
    ) -> None:
        await self.repo.assign_permissions(user_id, permission_ids, hotel_id, granted_by)

    async def grant_permission(
        self, user_id: UUID, permission_id: UUID, hotel_id: UUID, granted_by: UUID
    ):
        return await self.repo.grant_permission(user_id, permission_id, hotel_id, granted_by)

    async def revoke_permission(
        self, user_id: UUID, permission_id: UUID, hotel_id: UUID
    ) -> None:
        await self.repo.revoke_permission(user_id, permission_id, hotel_id)

    async def get_all_permissions(self) -> list[dict]:
        stmt = (
            select(Permission)
            .where(Permission.is_active.is_(True))
            .order_by(Permission.module, Permission.name)
        )
        result = await self.session.execute(stmt)
        permissions = result.scalars().all()
        return [
            {
                "id": str(p.id),
                "code": p.code,
                "name": p.name,
                "module": p.module,
                "description": p.description,
            }
            for p in permissions
        ]

    async def get_permission_modules(self) -> list[dict]:
        stmt = (
            select(Permission)
            .where(Permission.is_active.is_(True))
            .order_by(Permission.module, Permission.name)
        )
        result = await self.session.execute(stmt)
        permissions = result.scalars().all()

        modules: dict[str, list] = {}
        for p in permissions:
            if p.module not in modules:
                modules[p.module] = []
            modules[p.module].append(
                {"id": str(p.id), "code": p.code, "name": p.name, "description": p.description}
            )

        return [{"module": k, "permissions": v} for k, v in modules.items()]
