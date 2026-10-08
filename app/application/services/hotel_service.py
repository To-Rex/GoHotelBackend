"""Hotel service — creation includes auto-creating the main branch."""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.repositories.branch_repo import BranchRepository
from app.infrastructure.database.repositories.hotel_repo import HotelRepository
from app.application.services.audit_service import AuditService


class HotelService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.hotel_repo = HotelRepository(session)
        self.branch_repo = BranchRepository(session)
        self.audit_service = AuditService(session)

    async def create_hotel(self, data: dict, creator_id: UUID) -> Hotel:
        existing = await self.hotel_repo.get_by_code(data["code"])
        if existing:
            raise ConflictException(
                f"Hotel with code '{data['code']}' already exists", "HOTEL_CODE_EXISTS"
            )

        hotel = Hotel(
            name=data["name"],
            code=data["code"],
            description=data.get("description"),
            stars=data.get("stars", 3),
            phone=data.get("phone"),
            email=data.get("email"),
            address_line1=data.get("address_line1"),
            address_line2=data.get("address_line2"),
            city=data.get("city"),
            state=data.get("state"),
            country=data.get("country"),
            postal_code=data.get("postal_code"),
            status="ACTIVE",
        )
        hotel = await self.hotel_repo.create(hotel)

        await self.audit_service.log_action(
            hotel_id=hotel.id,
            user_id=creator_id,
            action="hotel.created",
            entity_type="Hotel",
            entity_id=hotel.id,
            new_values={
                "name": hotel.name,
                "code": hotel.code,
                "stars": hotel.stars,
                "city": hotel.city,
                "country": hotel.country,
            },
        )

        main_branch = Branch(
            hotel_id=hotel.id,
            name="Main Branch",
            code=f"{hotel.code}-MAIN",
            is_main_branch=True,
            status="ACTIVE",
        )
        await self.branch_repo.create(main_branch)
        # Filial sozlamalari mehmonxona sozlamalaridan (branch_provisioning)
        from app.application.services.branch_provisioning import provision_branch

        await provision_branch(self.session, main_branch)

        return hotel

    async def get_hotels(self, skip: int = 0, limit: int = 100, active_only: bool = False) -> list[Hotel]:
        if active_only:
            return await self.hotel_repo.get_all_active(skip, limit)
        return await self.hotel_repo.get_all(skip, limit)

    async def get_hotel(self, hotel_id: UUID) -> Hotel:
        hotel = await self.hotel_repo.get_by_id(hotel_id)
        if not hotel:
            raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
        return hotel

    async def update_hotel(self, hotel_id: UUID, data: dict) -> Hotel:
        hotel = await self.get_hotel(hotel_id)

        updatable_fields = [
            "name", "description", "stars", "phone", "email",
            "address_line1", "address_line2", "city", "state",
            "country", "postal_code", "status",
        ]
        update_data = {k: v for k, v in data.items() if k in updatable_fields and v is not None}
        return await self.hotel_repo.update(hotel, **update_data)

    async def update_hotel_status(self, hotel_id: UUID, status: str) -> Hotel:
        hotel = await self.get_hotel(hotel_id)
        if status not in ("ACTIVE", "SUSPENDED", "CLOSED"):
            raise ValidationException("Invalid status", "INVALID_STATUS")
        return await self.hotel_repo.update(hotel, status=status)

    async def delete_hotel(self, hotel_id: UUID) -> None:
        """Mehmonxonani va unga tegishli HAMMA ma'lumotni o'chiradi.

        Panel bilan bir xil xizmat (`HotelPurgeService`): o'chirish tartibi
        bazaning tashqi kalitlaridan olinadi (filial ustunlari ham), boshqa
        mehmonxona ma'lumotiga tegilmaydi, hammasi bitta tranzaksiyada.
        """
        from app.infrastructure.tenant.branch_scope import without_branch_scope
        from app.superadmin.hotel_purge_service import HotelPurgeService

        hotel = await self.get_hotel(hotel_id)
        hotel.status = "INACTIVE"
        await self.session.flush()
        with without_branch_scope(self.session):
            await HotelPurgeService(self.session).purge(hotel_id, actor="SUPER_ADMIN")

