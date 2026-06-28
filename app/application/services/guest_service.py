from uuid import UUID
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.repositories.guest_repo import GuestRepository


class GuestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = GuestRepository(session)

    async def create_guest(self, hotel_id: UUID, data: dict) -> Guest:
        guest = Guest(
            hotel_id=hotel_id,
            first_name=data["first_name"],
            last_name=data["last_name"],
            phone=data.get("phone"),
            email=data.get("email"),
            passport_number=data.get("passport_number"),
            nationality=data.get("nationality"),
            birth_date=data.get("birth_date"),
            id_document_type=data.get("id_document_type"),
            id_document_number=data.get("id_document_number"),
            address=data.get("address"),
            notes=data.get("notes"),
        )
        return await self.repo.create(guest)

    async def get_guests(
        self, hotel_id: UUID | None, skip: int = 0, limit: int = 100, query: str | None = None
    ) -> list[Guest]:
        if query:
            return await self.repo.search(hotel_id, query, skip, limit)
        if hotel_id is None:
            return await self.repo.get_all_unscoped(skip, limit)
        return await self.repo.get_all(hotel_id, skip, limit)

    async def get_guest(self, guest_id: UUID, hotel_id: UUID | None) -> Guest:
        if hotel_id is None:
            guest = await self.repo.get_by_id_unscoped(guest_id)
        else:
            guest = await self.repo.get_by_id(guest_id, hotel_id)
        if not guest:
            raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")
        return guest

    async def update_guest(self, guest_id: UUID, hotel_id: UUID, data: dict) -> Guest:
        guest = await self.get_guest(guest_id, hotel_id)
        updatable = [
            "first_name",
            "last_name",
            "phone",
            "email",
            "passport_number",
            "nationality",
            "birth_date",
            "id_document_type",
            "id_document_number",
            "address",
            "notes",
        ]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        return await self.repo.update(guest, **update_data)

    async def soft_delete_guest(self, guest_id: UUID, hotel_id: UUID) -> Guest:
        guest = await self.repo.soft_delete(guest_id, hotel_id)
        if not guest:
            raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")
        return guest
