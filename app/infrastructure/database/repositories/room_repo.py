from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.floor import Floor
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.models.room_type import RoomType
from app.infrastructure.database.repositories.base import BaseRepository, TenantBaseRepository


class RoomRepository(TenantBaseRepository[Room]):
    model = Room

    async def get_available_rooms(
        self,
        hotel_id: UUID | None,
        branch_id: UUID | None = None,
        room_type_id: UUID | None = None,
        check_in: date | None = None,
        check_out: date | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Room]:
        stmt = select(Room).where(Room.is_deleted.is_(False))
        if hotel_id is not None:
            stmt = stmt.where(Room.hotel_id == hotel_id)
        if branch_id:
            stmt = stmt.where(Room.branch_id == branch_id)
        if room_type_id:
            stmt = stmt.where(Room.room_type_id == room_type_id)

        if check_in and check_out:
            conflict_base = select(Reservation.room_id).where(
                Reservation.status.in_(["CONFIRMED", "CHECKED_IN"]),
                Reservation.check_in_date < check_out,
                Reservation.check_out_date > check_in,
            )
            if hotel_id is not None:
                conflict_base = conflict_base.where(Reservation.hotel_id == hotel_id)
            conflict_subq = conflict_base.subquery()
            stmt = stmt.where(Room.id.not_in(select(conflict_subq)))

        stmt = stmt.offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_room_number(
        self, hotel_id: UUID, branch_id: UUID, room_number: str
    ) -> Room | None:
        stmt = select(Room).where(
            Room.hotel_id == hotel_id,
            Room.branch_id == branch_id,
            Room.room_number == room_number,
            Room.is_deleted.is_(False),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_status_history(
        self, room_id: UUID, hotel_id: UUID, limit: int = 50
    ) -> list[RoomStatusHistory]:
        stmt = (
            select(RoomStatusHistory)
            .where(
                RoomStatusHistory.room_id == room_id,
                RoomStatusHistory.hotel_id == hotel_id,
            )
            .order_by(RoomStatusHistory.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_rooms_by_status(
        self, hotel_id: UUID, status: str, skip: int = 0, limit: int = 100
    ) -> list[Room]:
        stmt = (
            select(Room)
            .where(
                Room.hotel_id == hotel_id,
                Room.current_status == status,
                Room.is_deleted.is_(False),
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class RoomTypeRepository(BaseRepository[RoomType]):
    model = RoomType

    async def get_active_types(self) -> list[RoomType]:
        stmt = select(RoomType).where(RoomType.is_active.is_(True)).order_by(RoomType.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class FloorRepository(TenantBaseRepository[Floor]):
    model = Floor

    async def get_by_branch(
        self, hotel_id: UUID, branch_id: UUID
    ) -> list[Floor]:
        stmt = (
            select(Floor)
            .where(
                Floor.hotel_id == hotel_id,
                Floor.branch_id == branch_id,
            )
            .order_by(Floor.floor_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
