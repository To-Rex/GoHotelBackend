from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.infrastructure.database.models.invoice import Invoice
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.service import (
    HotelService,
    ReservationService,
    Service,
)
from app.infrastructure.database.repositories.base import TenantBaseRepository


class ReservationRepository(TenantBaseRepository[Reservation]):
    model = Reservation

    async def get_by_number(
        self, hotel_id: UUID, reservation_number: str
    ) -> Reservation | None:
        stmt = select(Reservation).where(
            Reservation.hotel_id == hotel_id,
            Reservation.reservation_number == reservation_number,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_details(
        self, reservation_id: UUID, hotel_id: UUID | None
    ) -> Reservation | None:
        stmt = (
            select(Reservation)
            .options(
                joinedload(Reservation.guest),
                joinedload(Reservation.room),
                joinedload(Reservation.invoice),
            )
            .where(Reservation.id == reservation_id)
        )
        if hotel_id is not None:
            stmt = stmt.where(Reservation.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get_calendar_reservations(
        self,
        hotel_id: UUID | None,
        start_date: date,
        end_date: date,
        branch_id: UUID | None = None,
        room_type_id: UUID | None = None,
        skip: int = 0,
        limit: int = 200,
    ) -> list[Reservation]:
        stmt = select(Reservation).where(
            Reservation.status.in_(["CONFIRMED", "CHECKED_IN"]),
            Reservation.check_in_date < end_date,
            Reservation.check_out_date > start_date,
            Reservation.is_deleted.is_(False),
        )
        if hotel_id is not None:
            stmt = stmt.where(Reservation.hotel_id == hotel_id)
        if branch_id:
            stmt = stmt.where(Reservation.branch_id == branch_id)
        if room_type_id:
            stmt = stmt.join(Room).where(Room.room_type_id == room_type_id)
        stmt = stmt.offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def check_room_availability(
        self,
        room_id: UUID,
        check_in: date,
        check_out: date,
        exclude_reservation_id: UUID | None = None,
        booking_type: str = "DAILY",
        check_in_datetime: datetime | None = None,
        check_out_datetime: datetime | None = None,
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.room_id == room_id,
                Reservation.status.in_(["CONFIRMED", "CHECKED_IN"]),
                Reservation.is_deleted.is_(False),
            )
        )
        if booking_type == "HOURLY" and check_in_datetime and check_out_datetime:
            stmt = stmt.where(
                Reservation.check_in_date <= check_in,
                Reservation.check_out_date >= check_out,
            )
            stmt = stmt.where(
                Reservation.check_in_datetime < check_out_datetime,
                Reservation.check_out_datetime > check_in_datetime,
            )
        else:
            stmt = stmt.where(
                Reservation.check_in_date < check_out,
                Reservation.check_out_date > check_in,
            )
        if exclude_reservation_id:
            stmt = stmt.where(Reservation.id != exclude_reservation_id)
        result = await self.session.execute(stmt)
        count = result.scalar() or 0
        return count == 0

    async def get_reservation_services(
        self, reservation_id: UUID, hotel_id: UUID | None
    ) -> list[dict]:
        stmt = (
            select(ReservationService, HotelService, Service)
            .join(HotelService, HotelService.id == ReservationService.hotel_service_id)
            .join(Service, Service.id == HotelService.service_id)
            .where(ReservationService.reservation_id == reservation_id)
        )
        if hotel_id is not None:
            stmt = stmt.where(ReservationService.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            {
                "id": str(rs.id),
                "service_id": str(svc.id),
                "service_name": svc.name,
                "service_code": svc.code,
                "quantity": rs.quantity,
                "unit_price": float(rs.unit_price),
                "total_price": float(rs.total_price),
                "service_date": str(rs.service_date),
                "notes": rs.notes,
            }
            for rs, hs, svc in rows
        ]

    async def get_guest_reservations(
        self, guest_id: UUID, hotel_id: UUID | None
    ) -> list[Reservation]:
        stmt = (
            select(Reservation)
            .where(
                Reservation.guest_id == guest_id,
                Reservation.is_deleted.is_(False),
            )
            .order_by(Reservation.created_at.desc())
        )
        if hotel_id is not None:
            stmt = stmt.where(Reservation.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def cancel_reservation(
        self, reservation: Reservation, reason: str, cancelled_by: UUID
    ) -> Reservation:
        reservation.status = "CANCELLED"
        reservation.cancelled_reason = reason
        reservation.cancelled_at = datetime.now(timezone.utc)
        reservation.cancelled_by = cancelled_by
        await self.session.flush()
        await self.session.refresh(reservation)
        return reservation

    async def add_service(
        self,
        reservation_id: UUID,
        hotel_id: UUID,
        hotel_service_id: UUID,
        quantity: int,
        unit_price: float,
        service_date: date,
        notes: str | None = None,
    ) -> ReservationService:
        total_price = unit_price * quantity
        rs = ReservationService(
            hotel_id=hotel_id,
            reservation_id=reservation_id,
            hotel_service_id=hotel_service_id,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_price,
            service_date=service_date,
            notes=notes,
        )
        self.session.add(rs)
        await self.session.flush()
        return rs
