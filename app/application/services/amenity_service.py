from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ConflictException
from app.infrastructure.database.models.amenity import Amenity, HotelAmenity, RoomAmenity
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.hotel import Hotel


class AmenityService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- Global Amenity CRUD ---

    async def get_amenities(self) -> list[Amenity]:
        stmt = select(Amenity).order_by(Amenity.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_amenity(self, amenity_id: UUID) -> Amenity:
        amenity = await self.session.get(Amenity, amenity_id)
        if not amenity:
            raise NotFoundException("Amenity not found", "AMENITY_NOT_FOUND")
        return amenity

    async def create_amenity(self, data: dict) -> Amenity:
        stmt = select(Amenity).where(Amenity.name == data["name"])
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictException(
                f"Amenity '{data['name']}' already exists", "AMENITY_EXISTS"
            )
        amenity = Amenity(
            name=data["name"],
            icon=data.get("icon"),
        )
        self.session.add(amenity)
        await self.session.flush()
        await self.session.refresh(amenity)
        return amenity

    async def update_amenity(self, amenity_id: UUID, data: dict) -> Amenity:
        amenity = await self.get_amenity(amenity_id)
        for key in ("name", "icon", "is_active"):
            if key in data and data[key] is not None:
                setattr(amenity, key, data[key])
        await self.session.flush()
        return amenity

    async def delete_amenity(self, amenity_id: UUID) -> None:
        amenity = await self.get_amenity(amenity_id)
        await self.session.delete(amenity)
        await self.session.flush()

    # --- Hotel-Amenity ---

    async def add_hotel_amenity(self, hotel_id: UUID, amenity_id: UUID) -> dict:
        hotel = await self.session.get(Hotel, hotel_id)
        if not hotel:
            raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
        await self.get_amenity(amenity_id)

        stmt = select(HotelAmenity).where(
            HotelAmenity.hotel_id == hotel_id, HotelAmenity.amenity_id == amenity_id
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictException("Amenity already assigned to hotel", "HOTEL_AMENITY_EXISTS")

        ha = HotelAmenity(hotel_id=hotel_id, amenity_id=amenity_id)
        self.session.add(ha)
        await self.session.flush()
        return {"hotel_id": str(hotel_id), "amenity_id": str(amenity_id)}

    async def remove_hotel_amenity(self, hotel_id: UUID, amenity_id: UUID) -> None:
        stmt = select(HotelAmenity).where(
            HotelAmenity.hotel_id == hotel_id, HotelAmenity.amenity_id == amenity_id
        )
        result = await self.session.execute(stmt)
        ha = result.scalar_one_or_none()
        if not ha:
            raise NotFoundException("Hotel amenity not found", "HOTEL_AMENITY_NOT_FOUND")
        await self.session.delete(ha)
        await self.session.flush()

    async def get_hotel_amenities(self, hotel_id: UUID) -> list[dict]:
        stmt = (
            select(Amenity)
            .join(HotelAmenity, HotelAmenity.amenity_id == Amenity.id)
            .where(HotelAmenity.hotel_id == hotel_id)
            .order_by(Amenity.name)
        )
        result = await self.session.execute(stmt)
        amenities = result.scalars().all()
        return [
            {"id": str(a.id), "name": a.name, "icon": a.icon, "is_active": a.is_active}
            for a in amenities
        ]

    # --- Room-Amenity ---

    async def add_room_amenity(self, room_id: UUID, amenity_id: UUID, hotel_id: UUID) -> dict:
        room = await self.session.get(Room, room_id)
        if not room or room.hotel_id != hotel_id:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
        await self.get_amenity(amenity_id)

        stmt = select(RoomAmenity).where(
            RoomAmenity.room_id == room_id, RoomAmenity.amenity_id == amenity_id
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictException("Amenity already assigned to room", "ROOM_AMENITY_EXISTS")

        ra = RoomAmenity(room_id=room_id, amenity_id=amenity_id)
        self.session.add(ra)
        await self.session.flush()
        return {"room_id": str(room_id), "amenity_id": str(amenity_id)}

    async def remove_room_amenity(self, room_id: UUID, amenity_id: UUID, hotel_id: UUID) -> None:
        room = await self.session.get(Room, room_id)
        if not room or room.hotel_id != hotel_id:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        stmt = select(RoomAmenity).where(
            RoomAmenity.room_id == room_id, RoomAmenity.amenity_id == amenity_id
        )
        result = await self.session.execute(stmt)
        ra = result.scalar_one_or_none()
        if not ra:
            raise NotFoundException("Room amenity not found", "ROOM_AMENITY_NOT_FOUND")
        await self.session.delete(ra)
        await self.session.flush()

    async def get_room_amenities(self, room_id: UUID, hotel_id: UUID) -> list[dict]:
        room = await self.session.get(Room, room_id)
        if not room or room.hotel_id != hotel_id:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

        stmt = (
            select(Amenity)
            .join(RoomAmenity, RoomAmenity.amenity_id == Amenity.id)
            .where(RoomAmenity.room_id == room_id)
            .order_by(Amenity.name)
        )
        result = await self.session.execute(stmt)
        amenities = result.scalars().all()
        return [
            {"id": str(a.id), "name": a.name, "icon": a.icon, "is_active": a.is_active}
            for a in amenities
        ]
