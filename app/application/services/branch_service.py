from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.repositories.branch_repo import BranchRepository


class BranchService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BranchRepository(session)

    async def create_branch(self, hotel_id: UUID, data: dict) -> Branch:
        existing = await self.repo.get_by_code(hotel_id, data["code"])
        if existing:
            raise ConflictException(
                f"Branch code '{data['code']}' already exists", "BRANCH_CODE_EXISTS"
            )

        branch = Branch(
            hotel_id=hotel_id,
            name=data["name"],
            code=data["code"],
            address_line1=data.get("address_line1"),
            address_line2=data.get("address_line2"),
            city=data.get("city"),
            state=data.get("state"),
            country=data.get("country"),
            postal_code=data.get("postal_code"),
            phone=data.get("phone"),
            email=data.get("email"),
            status="ACTIVE",
        )
        return await self.repo.create(branch)

    async def get_branches(self, hotel_id: UUID | None, skip: int = 0, limit: int = 100) -> list[Branch]:
        if hotel_id is None:
            return await self.repo.get_all_unscoped(skip, limit)
        return await self.repo.get_all(hotel_id, skip, limit)

    async def get_branch(self, branch_id: UUID, hotel_id: UUID | None) -> Branch:
        if hotel_id is None:
            branch = await self.repo.get_by_id_unscoped(branch_id)
        else:
            branch = await self.repo.get_by_id(branch_id, hotel_id)
        if not branch:
            raise NotFoundException("Branch not found", "BRANCH_NOT_FOUND")
        return branch

    async def update_branch(self, branch_id: UUID, hotel_id: UUID, data: dict) -> Branch:
        branch = await self.get_branch(branch_id, hotel_id)
        updatable = [
            "name",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "country",
            "postal_code",
            "phone",
            "email",
            "status",
        ]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        return await self.repo.update(branch, **update_data)

    async def get_main_branch(self, hotel_id: UUID) -> Branch:
        branch = await self.repo.get_main_branch(hotel_id)
        if not branch:
            raise NotFoundException("Main branch not found", "MAIN_BRANCH_NOT_FOUND")
        return branch
