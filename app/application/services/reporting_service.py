from uuid import UUID
from datetime import date, datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.invoice import Invoice
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.repositories.report_repo import ReportRepository


class ReportingService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ReportRepository(session)

    async def get_occupancy_report(
        self,
        hotel_id: UUID | None,
        start_date: date,
        end_date: date,
        branch_id: UUID | None = None,
    ) -> dict:
        total_rooms_stmt = select(func.count()).select_from(Room).where(
            Room.is_deleted.is_(False),
        )
        if hotel_id is not None:
            total_rooms_stmt = total_rooms_stmt.where(Room.hotel_id == hotel_id)
        if branch_id:
            total_rooms_stmt = total_rooms_stmt.where(Room.branch_id == branch_id)
        total_result = await self.session.execute(total_rooms_stmt)
        total_rooms = total_result.scalar() or 0

        occupied_stmt = select(func.count(func.distinct(Reservation.room_id))).where(
            Reservation.status.in_(["CONFIRMED", "CHECKED_IN"]),
            Reservation.check_in_date < end_date,
            Reservation.check_out_date > start_date,
            Reservation.is_deleted.is_(False),
        )
        if hotel_id is not None:
            occupied_stmt = occupied_stmt.where(Reservation.hotel_id == hotel_id)
        if branch_id:
            occupied_stmt = occupied_stmt.where(Reservation.branch_id == branch_id)
        occupied_result = await self.session.execute(occupied_stmt)
        occupied_rooms = occupied_result.scalar() or 0

        occupancy_pct = (occupied_rooms / total_rooms * 100) if total_rooms > 0 else 0

        reservations_stmt = select(Reservation).where(
            Reservation.check_in_date < end_date,
            Reservation.check_out_date > start_date,
            Reservation.is_deleted.is_(False),
        )
        if hotel_id is not None:
            reservations_stmt = reservations_stmt.where(Reservation.hotel_id == hotel_id)
        if branch_id:
            reservations_stmt = reservations_stmt.where(Reservation.branch_id == branch_id)
        res_result = await self.session.execute(reservations_stmt)
        reservations = list(res_result.scalars().all())

        return {
            "total_rooms": total_rooms,
            "occupied_rooms": occupied_rooms,
            "occupancy_pct": round(occupancy_pct, 2),
            "total_reservations": len(reservations),
            "period": {"start_date": str(start_date), "end_date": str(end_date)},
        }

    async def get_revenue_report(
        self,
        hotel_id: UUID | None,
        start_date: date,
        end_date: date,
        branch_id: UUID | None = None,
    ) -> dict:
        stmt = select(
            func.coalesce(func.sum(Invoice.total_amount), 0)
        ).where(
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date <= end_date,
            Invoice.status.in_(["ISSUED", "PARTIALLY_PAID", "PAID"]),
        )
        if hotel_id is not None:
            stmt = stmt.where(Invoice.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        total_revenue = float(result.scalar() or 0)

        paid_stmt = select(
            func.coalesce(func.sum(Invoice.paid_amount), 0)
        ).where(
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date <= end_date,
            Invoice.status.in_(["ISSUED", "PARTIALLY_PAID", "PAID"]),
        )
        if hotel_id is not None:
            paid_stmt = paid_stmt.where(Invoice.hotel_id == hotel_id)
        paid_result = await self.session.execute(paid_stmt)
        total_paid = float(paid_result.scalar() or 0)

        return {
            "total_revenue": total_revenue,
            "total_paid": total_paid,
            "outstanding": total_revenue - total_paid,
            "period": {"start_date": str(start_date), "end_date": str(end_date)},
        }

    async def save_report(
        self,
        hotel_id: UUID,
        name: str,
        report_type: str,
        parameters: dict,
        user_id: UUID | None = None,
        result_data: dict | None = None,
    ):
        report = await self.repo.create(hotel_id, name, report_type, parameters, user_id)
        if result_data:
            await self.repo.save_result(report.id, result_data)
        return report

    async def get_saved_reports(
        self, hotel_id: UUID | None, report_type: str | None = None
    ):
        return await self.repo.get_by_hotel(hotel_id, report_type)
