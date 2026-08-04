from uuid import UUID
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.infrastructure.database.models.floor import Floor
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.models.room_type import RoomType, HotelRoomType
from app.infrastructure.database.repositories.room_repo import (
    FloorRepository,
    RoomRepository,
)
from app.shared.utils import generate_code


class RoomService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.room_repo = RoomRepository(session)
        self.floor_repo = FloorRepository(session)

    # --- Room Types ---

    async def create_room_type(self, data: dict, hotel_id: UUID | None = None) -> RoomType:
        # Nom unikaliligi mehmonxona doirasida tekshiriladi
        stmt = select(RoomType).where(
            RoomType.name == data["name"], RoomType.hotel_id == hotel_id
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictException(
                f"Room type '{data['name']}' already exists", "ROOM_TYPE_EXISTS"
            )
        rt = RoomType(
            hotel_id=hotel_id,
            name=data["name"],
            description=data.get("description"),
            capacity=data.get("capacity", 1),
            base_price=data["base_price"],
            amenities=data.get("amenities", []),
        )
        self.session.add(rt)
        await self.session.flush()
        await self.session.refresh(rt)
        return rt

    async def get_room_types(
        self, active_only: bool = False, hotel_id: UUID | None = None
    ) -> list[RoomType]:
        stmt = select(RoomType).order_by(RoomType.name)
        # Har bir mehmonxona faqat O'Z turlarini ko'radi; hotel_id berilmasa
        # (SUPER_ADMIN kontekstsiz) — hammasi qaytadi
        if hotel_id is not None:
            stmt = stmt.where(RoomType.hotel_id == hotel_id)
        if active_only:
            stmt = stmt.where(RoomType.is_active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_room_type(self, type_id: UUID) -> RoomType:
        rt = await self.session.get(RoomType, type_id)
        if not rt:
            raise NotFoundException("Room type not found", "ROOM_TYPE_NOT_FOUND")
        return rt

    async def update_room_type(self, type_id: UUID, data: dict) -> RoomType:
        rt = await self.get_room_type(type_id)
        # Nom o'zgartirilsa — o'sha mehmonxona doirasida dublikatni tekshiramiz
        new_name = data.get("name")
        if new_name and new_name != rt.name:
            dup = await self.session.execute(
                select(RoomType.id)
                .where(
                    RoomType.name == new_name,
                    RoomType.hotel_id == rt.hotel_id,
                    RoomType.id != rt.id,
                )
                .limit(1)
            )
            if dup.first():
                raise ConflictException(
                    f"Room type '{new_name}' already exists", "ROOM_TYPE_EXISTS"
                )
        updatable = ["name", "description", "capacity", "base_price", "amenities", "is_active"]
        for key in updatable:
            if key in data and data[key] is not None:
                setattr(rt, key, data[key])
        await self.session.flush()
        return rt

    async def delete_room_type(self, type_id: UUID) -> None:
        rt = await self.get_room_type(type_id)

        # rooms.room_type_id FK RESTRICT — turga biriktirilgan xona (arxivdagi
        # ham) bo'lsa DB o'chirishga yo'l qo'ymaydi va 500 xato chiqardi.
        # Buning o'rniga tushunarli 409 qaytaramiz. hotel_room_types
        # bog'lanishlari esa CASCADE bilan avtomatik o'chadi.
        count_stmt = (
            select(func.count()).select_from(Room).where(Room.room_type_id == type_id)
        )
        result = await self.session.execute(count_stmt)
        room_count = result.scalar() or 0
        if room_count > 0:
            raise ConflictException(
                f"Bu xona turiga {room_count} ta xona biriktirilgan. "
                "Avval o'sha xonalarni boshqa turga o'tkazing yoki o'chiring.",
                "ROOM_TYPE_IN_USE",
            )

        await self.session.delete(rt)
        await self.session.flush()

    # --- Hotel-RoomType ---

    async def add_hotel_room_type(self, hotel_id: UUID, room_type_id: UUID) -> dict:
        hotel = await self.session.get(Hotel, hotel_id)
        if not hotel:
            raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
        await self.get_room_type(room_type_id)

        stmt = select(HotelRoomType).where(
            HotelRoomType.hotel_id == hotel_id,
            HotelRoomType.room_type_id == room_type_id,
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictException(
                "Room type already assigned to hotel", "HOTEL_ROOM_TYPE_EXISTS"
            )

        hrt = HotelRoomType(hotel_id=hotel_id, room_type_id=room_type_id)
        self.session.add(hrt)
        await self.session.flush()
        return {"hotel_id": str(hotel_id), "room_type_id": str(room_type_id)}

    async def remove_hotel_room_type(self, hotel_id: UUID, room_type_id: UUID) -> None:
        stmt = select(HotelRoomType).where(
            HotelRoomType.hotel_id == hotel_id,
            HotelRoomType.room_type_id == room_type_id,
        )
        result = await self.session.execute(stmt)
        hrt = result.scalar_one_or_none()
        if not hrt:
            raise NotFoundException("Hotel room type not found", "HOTEL_ROOM_TYPE_NOT_FOUND")
        await self.session.delete(hrt)
        await self.session.flush()

    async def get_hotel_room_types(self, hotel_id: UUID) -> list[RoomType]:
        stmt = (
            select(RoomType)
            .join(HotelRoomType, HotelRoomType.room_type_id == RoomType.id)
            .where(HotelRoomType.hotel_id == hotel_id)
            .order_by(RoomType.name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Floors ---

    async def create_floor(self, hotel_id: UUID, branch_id: UUID, data: dict) -> Floor:
        # (branch, floor_number) unikal — DB xatosi (500) o'rniga aniq 409
        dup = await self.session.execute(
            select(Floor.id)
            .where(
                Floor.branch_id == branch_id,
                Floor.floor_number == data["floor_number"],
                Floor.is_deleted.is_(False),
            )
            .limit(1)
        )
        if dup.first():
            raise ConflictException(
                f"Bu filialda {data['floor_number']}-qavat allaqachon mavjud",
                "FLOOR_NUMBER_EXISTS",
            )
        floor = Floor(
            hotel_id=hotel_id,
            branch_id=branch_id,
            floor_number=data["floor_number"],
            name=data.get("name"),
        )
        return await self.floor_repo.create(floor)

    async def get_floors(
        self, hotel_id: UUID | None, branch_id: UUID | None = None
    ) -> list[Floor]:
        if hotel_id is None:
            return await self.floor_repo.get_all_unscoped(limit=500, is_deleted=False)
        if branch_id:
            return await self.floor_repo.get_by_branch(hotel_id, branch_id)
        return await self.floor_repo.get_all(hotel_id, limit=500, is_deleted=False)

    async def get_floor(self, floor_id: UUID, hotel_id: UUID | None) -> Floor:
        if hotel_id is None:
            floor = await self.floor_repo.get_by_id_unscoped(floor_id)
        else:
            floor = await self.floor_repo.get_by_id(floor_id, hotel_id)
        if not floor:
            raise NotFoundException("Floor not found", "FLOOR_NOT_FOUND")
        return floor

    async def update_floor(self, floor: Floor, **values) -> Floor:
        # Qavat raqami o'zgartirilsa — dublikatni oldindan tekshiramiz
        # (aks holda DB unikal cheklovi 500 xato berardi)
        new_number = values.get("floor_number")
        if new_number is not None and new_number != floor.floor_number:
            dup = await self.session.execute(
                select(Floor.id)
                .where(
                    Floor.branch_id == floor.branch_id,
                    Floor.floor_number == new_number,
                    Floor.id != floor.id,
                    Floor.is_deleted.is_(False),
                )
                .limit(1)
            )
            if dup.first():
                raise ConflictException(
                    f"Bu filialda {new_number}-qavat allaqachon mavjud",
                    "FLOOR_NUMBER_EXISTS",
                )
        return await self.floor_repo.update(floor, **values)

    async def _room_has_references(self, room_id: UUID) -> bool:
        """Xonaga bog'liq (FK RESTRICT) yozuvlar bormi — bron, xo'jalik
        vazifasi yoki holat tarixi. Bo'lsa, xona qatorini butunlay o'chirib
        bo'lmaydi (arxivda qolishi kerak)."""
        for model in (Reservation, HousekeepingTask, RoomStatusHistory):
            stmt = select(model.id).where(model.room_id == room_id).limit(1)
            if (await self.session.execute(stmt)).first():
                return True
        return False

    async def delete_floor(self, floor_id: UUID, hotel_id: UUID) -> None:
        """Qavatni unga biriktirilgan xonalari bilan BIRGA o'chiradi.

        Xonalar: tarixi (bron/vazifa/holat yozuvi) yo'qlari butunlay
        o'chiriladi; tarixi borlari arxivga (soft delete) o'tadi — hisobotlar
        va bron tarixi buzilmaydi. Agar tarixli xonalar qolsa, ularning qatori
        FK orqali qavatni ushlab turadi — bunday holatda qavat ham arxivga
        o'tadi (ro'yxatlarda ko'rinmaydi), aks holda butunlay o'chiriladi.
        """
        floor = await self.get_floor(floor_id, hotel_id)

        rooms_stmt = select(Room).where(Room.floor_id == floor_id)
        rooms = list((await self.session.execute(rooms_stmt)).scalars().all())

        now = datetime.now(timezone.utc)
        history_rooms = 0
        for room in rooms:
            if await self._room_has_references(room.id):
                if not room.is_deleted:
                    room.is_deleted = True
                    room.deleted_at = now
                history_rooms += 1
            else:
                # Qulayliklar bog'lanishi (room_amenities) CASCADE bilan o'chadi
                await self.session.delete(room)
        await self.session.flush()

        if history_rooms:
            floor.is_deleted = True
            floor.deleted_at = now
            await self.session.flush()
        else:
            await self.floor_repo.delete(floor)

    # --- Rooms ---

    async def create_room(self, hotel_id: UUID, branch_id: UUID, data: dict) -> Room:
        existing = await self.room_repo.get_by_room_number(hotel_id, branch_id, data["room_number"])
        if existing:
            raise ConflictException(
                f"Room number '{data['room_number']}' already exists in this branch",
                "ROOM_NUMBER_EXISTS",
            )

        room = Room(
            hotel_id=hotel_id,
            branch_id=branch_id,
            floor_id=data["floor_id"],
            room_type_id=data["room_type_id"],
            room_number=data["room_number"],
            base_price=data.get("base_price", 0),
            capacity=data.get("capacity"),
            notes=data.get("notes"),
            current_status="AVAILABLE",
        )
        return await self.room_repo.create(room)

    async def get_rooms(
        self,
        hotel_id: UUID | None,
        branch_id: UUID | None = None,
        floor_id: UUID | None = None,
        room_type_id: UUID | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Room]:
        if hotel_id is None:
            filters: dict = {}
            if branch_id:
                filters["branch_id"] = branch_id
            if floor_id:
                filters["floor_id"] = floor_id
            if room_type_id:
                filters["room_type_id"] = room_type_id
            if status:
                filters["current_status"] = status
            # O'chirilgan (soft-delete) xonalar ro'yxatga chiqmasligi kerak —
            # aks holda o'chirish UI'da "ishlamagandek" ko'rinadi
            filters["is_deleted"] = False
            return await self.room_repo.get_all_unscoped(skip, limit, **filters)
        if status:
            return await self.room_repo.get_rooms_by_status(hotel_id, status, skip, limit)

        filters: dict = {}
        if branch_id:
            filters["branch_id"] = branch_id
        if floor_id:
            filters["floor_id"] = floor_id
        if room_type_id:
            filters["room_type_id"] = room_type_id
        # O'chirilgan xonalar ro'yxatdan yashiriladi
        filters["is_deleted"] = False
        return await self.room_repo.get_all(hotel_id, skip, limit, **filters)

    async def get_room(self, room_id: UUID, hotel_id: UUID | None) -> Room:
        if hotel_id is None:
            room = await self.room_repo.get_by_id_unscoped(room_id)
        else:
            room = await self.room_repo.get_by_id(room_id, hotel_id)
        if not room:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
        return room

    async def update_room(self, room_id: UUID, hotel_id: UUID, data: dict) -> Room:
        room = await self.get_room(room_id, hotel_id)
        updatable = ["floor_id", "room_type_id", "base_price", "capacity", "notes"]
        update_data = {k: v for k, v in data.items() if k in updatable and v is not None}
        return await self.room_repo.update(room, **update_data)

    async def update_room_status(
        self,
        room_id: UUID,
        hotel_id: UUID,
        status: str,
        changed_by: UUID,
        notes: str | None = None,
    ) -> Room:
        room = await self.get_room(room_id, hotel_id)

        valid_statuses = [
            "AVAILABLE",
            "RESERVED",
            "OCCUPIED",
            "CLEANING",
            "MAINTENANCE",
            "INSPECTION",
            "OUT_OF_SERVICE",
        ]
        if status not in valid_statuses:
            raise ValidationException(f"Invalid room status: {status}", "INVALID_STATUS")

        room.current_status = status
        await self.room_repo.update(room, current_status=status)

        history = RoomStatusHistory(
            hotel_id=hotel_id,
            room_id=room_id,
            status=status,
            changed_by=changed_by,
            notes=notes,
        )
        self.session.add(history)
        await self.session.flush()

        return room

    async def get_status_history(
        self, room_id: UUID, hotel_id: UUID | None, limit: int = 50
    ) -> list[RoomStatusHistory]:
        if hotel_id is None:
            from sqlalchemy import select as sa_select
            stmt = (
                sa_select(RoomStatusHistory)
                .where(RoomStatusHistory.room_id == room_id)
                .order_by(RoomStatusHistory.created_at.desc())
                .limit(limit)
            )
            result = await self.session.execute(stmt)
            return list(result.scalars().all())
        return await self.room_repo.get_status_history(room_id, hotel_id, limit)

    async def soft_delete_room(self, room_id: UUID, hotel_id: UUID) -> Room:
        room = await self.get_room(room_id, hotel_id)
        # Tarixi (bron/vazifa/holat yozuvi) BO'LMAGAN xona butunlay o'chiriladi —
        # qatori qolib qavat/xona turi o'chirishlarini bloklab yurmasligi uchun
        # (qulayliklar bog'lanishi CASCADE bilan o'zi o'chadi). Tarixi borlar
        # arxivga (soft delete) o'tadi — hisobotlar va tarix buzilmaydi.
        if not await self._room_has_references(room.id):
            await self.session.delete(room)
            await self.session.flush()
            return room
        room.is_deleted = True
        room.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return room

    async def get_available_rooms(
        self,
        hotel_id: UUID | None,
        check_in: date | None = None,
        check_out: date | None = None,
        branch_id: UUID | None = None,
        room_type_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Room]:
        return await self.room_repo.get_available_rooms(
            hotel_id, branch_id, room_type_id, check_in, check_out, skip, limit
        )
