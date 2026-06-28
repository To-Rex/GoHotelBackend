"""Hotel service — creation includes auto-creating the main branch."""
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.amenity import HotelAmenity, RoomAmenity
from app.infrastructure.database.models.room_type import HotelRoomType
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.user import User
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
        hotel = await self.get_hotel(hotel_id)

        # Step 1: Direct hotel-scoped children (leaf nodes first)
        from app.infrastructure.database.models.audit_log import AuditLog
        from app.infrastructure.database.models.file_attachment import FileAttachment
        from app.infrastructure.database.models.notification import Notification
        from app.infrastructure.database.models.report import Report
        from app.infrastructure.database.models.ledger import Ledger
        from app.infrastructure.database.models.journal_entry import JournalEntryLine, JournalEntry as JE

        await self.session.execute(delete(AuditLog).where(AuditLog.hotel_id == hotel_id))
        await self.session.execute(delete(FileAttachment).where(FileAttachment.hotel_id == hotel_id))
        await self.session.execute(delete(Notification).where(Notification.hotel_id == hotel_id))
        await self.session.execute(delete(Report).where(Report.hotel_id == hotel_id))
        await self.session.execute(delete(HotelAmenity).where(HotelAmenity.hotel_id == hotel_id))
        await self.session.execute(delete(HotelRoomType).where(HotelRoomType.hotel_id == hotel_id))

        je_stmt = select(JE.id).where(JE.hotel_id == hotel_id)
        je_result = await self.session.execute(je_stmt)
        je_ids = [row[0] for row in je_result.all()]
        if je_ids:
            await self.session.execute(delete(JournalEntryLine).where(JournalEntryLine.journal_entry_id.in_(je_ids)))
            await self.session.execute(delete(JE).where(JE.id.in_(je_ids)))
        await self.session.execute(delete(Ledger).where(Ledger.hotel_id == hotel_id))

        from app.infrastructure.database.models.permission import UserPermission
        await self.session.execute(delete(UserPermission).where(UserPermission.hotel_id == hotel_id))

        from app.infrastructure.database.models.service import HotelService as HSvc, ReservationService
        await self.session.execute(delete(ReservationService).where(ReservationService.hotel_id == hotel_id))
        await self.session.execute(delete(HSvc).where(HSvc.hotel_id == hotel_id))

        # Step 2: Branches → Rooms → everything under rooms
        branches_stmt = select(Branch.id).where(Branch.hotel_id == hotel_id)
        result = await self.session.execute(branches_stmt)
        branch_ids = [row[0] for row in result.all()]

        if branch_ids:
            from app.infrastructure.database.models.room import Room
            from app.infrastructure.database.models.floor import Floor
            from app.infrastructure.database.models.housekeeping import HousekeepingTask
            from app.infrastructure.database.models.room_status_history import RoomStatusHistory
            from app.infrastructure.database.models.invoice import Invoice as Inv, InvoiceLineItem
            from app.infrastructure.database.models.payment import Payment

            room_stmt = select(Room.id).where(Room.branch_id.in_(branch_ids))
            room_result = await self.session.execute(room_stmt)
            room_ids = [row[0] for row in room_result.all()]

            if room_ids:
                await self.session.execute(delete(RoomAmenity).where(RoomAmenity.room_id.in_(room_ids)))
                await self.session.execute(delete(RoomStatusHistory).where(RoomStatusHistory.room_id.in_(room_ids)))
                await self.session.execute(delete(HousekeepingTask).where(HousekeepingTask.room_id.in_(room_ids)))

                res_stmt = select(Reservation.id).where(Reservation.room_id.in_(room_ids))
                res_result = await self.session.execute(res_stmt)
                res_ids = [row[0] for row in res_result.all()]
                if res_ids:
                    inv_stmt = select(Inv.id).where(Inv.reservation_id.in_(res_ids))
                    inv_result = await self.session.execute(inv_stmt)
                    inv_ids = [row[0] for row in inv_result.all()]
                    if inv_ids:
                        await self.session.execute(delete(InvoiceLineItem).where(InvoiceLineItem.invoice_id.in_(inv_ids)))
                        await self.session.execute(delete(Payment).where(Payment.invoice_id.in_(inv_ids)))
                        await self.session.execute(delete(Inv).where(Inv.id.in_(inv_ids)))
                    await self.session.execute(delete(ReservationService).where(ReservationService.reservation_id.in_(res_ids)))
                    await self.session.execute(delete(Reservation).where(Reservation.id.in_(res_ids)))

                await self.session.execute(delete(Room).where(Room.id.in_(room_ids)))

            floor_stmt = select(Floor.id).where(Floor.branch_id.in_(branch_ids))
            floor_result = await self.session.execute(floor_stmt)
            floor_ids = [row[0] for row in floor_result.all()]
            if floor_ids:
                await self.session.execute(delete(Floor).where(Floor.id.in_(floor_ids)))

            # Null users' branch_id before deleting branches
            await self.session.execute(
                update(User).where(User.branch_id.in_(branch_ids)).values(branch_id=None)
            )
            await self.session.execute(delete(Branch).where(Branch.id.in_(branch_ids)))

        # Step 3: Guests and Users
        await self.session.execute(delete(Guest).where(Guest.hotel_id == hotel_id))
        await self.session.execute(
            update(User).where(User.hotel_id == hotel_id).values(branch_id=None)
        )
        await self.session.execute(delete(User).where(User.hotel_id == hotel_id))

        # Step 4: Hotel itself
        await self.session.delete(hotel)
        await self.session.flush()
