from uuid import UUID
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.exceptions import NotFoundException, ValidationException, BadRequestException
from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.ledger import Ledger
from app.infrastructure.database.models.journal_entry import JournalEntry, JournalEntryLine
from app.infrastructure.database.repositories.finance_repo import (
    LedgerRepository,
    JournalEntryRepository,
    InvoiceRepository,
    PaymentRepository,
)
from app.shared.utils import generate_code


class FinanceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.invoice_repo = InvoiceRepository(session)
        self.payment_repo = PaymentRepository(session)
        self.ledger_repo = LedgerRepository(session)
        self.journal_entry_repo = JournalEntryRepository(session)

    async def _get_hotel_code(self, hotel_id: UUID) -> str:
        from app.infrastructure.database.models.hotel import Hotel
        stmt = select(Hotel).where(Hotel.id == hotel_id)
        result = await self.session.execute(stmt)
        hotel = result.scalar_one_or_none()
        if not hotel:
            raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
        return hotel.code

    async def create_invoice(
        self,
        hotel_id: UUID,
        reservation_id: UUID,
        created_by: UUID,
    ) -> Invoice:
        from app.infrastructure.database.models.reservation import Reservation
        from app.infrastructure.database.models.room_type import RoomType

        reservation_stmt = select(Reservation).where(
            Reservation.id == reservation_id, Reservation.hotel_id == hotel_id
        )
        r_result = await self.session.execute(reservation_stmt)
        reservation = r_result.scalar_one_or_none()
        if not reservation:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")

        guest_id = reservation.guest_id

        from app.infrastructure.database.models.room import Room
        room_stmt = select(Room).where(Room.id == reservation.room_id)
        room_result = await self.session.execute(room_stmt)
        room = room_result.scalar_one_or_none()

        rt_stmt = select(RoomType).where(RoomType.id == room.room_type_id) if room else None
        room_type = None
        base_price = 0
        if rt_stmt is not None:
            rt_result = await self.session.execute(rt_stmt)
            room_type = rt_result.scalar_one_or_none()
            base_price = float(room_type.base_price) if room_type else 0

        booking_type = getattr(reservation, 'booking_type', None) or "DAILY"
        nights = (reservation.check_out_date - reservation.check_in_date).days
        if nights < 1:
            nights = 1

        room_charge = base_price * nights
        duration_label = "night(s)"

        if booking_type == "HOURLY" and reservation.check_in_datetime and reservation.check_out_datetime:
            delta = reservation.check_out_datetime - reservation.check_in_datetime
            hours = delta.total_seconds() / 3600
            if hours < 1:
                hours = 1
            hourly_rate = base_price / 24
            room_charge = round(hourly_rate * hours, 2)
            nights = hours
            duration_label = "hour(s)"

        hotel_code = await self._get_hotel_code(hotel_id)
        invoice_number = generate_code("INV", hotel_code)

        invoice = Invoice(
            hotel_id=hotel_id,
            reservation_id=reservation_id,
            guest_id=guest_id,
            invoice_number=invoice_number,
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal=room_charge,
            tax_amount=0,
            discount_amount=reservation.discount_amount or 0,
            total_amount=max(room_charge - (reservation.discount_amount or 0), 0),
            paid_amount=0,
            status="ISSUED",
            created_by=created_by,
        )
        self.session.add(invoice)
        await self.session.flush()

        room_line = InvoiceLineItem(
            invoice_id=invoice.id,
            hotel_id=hotel_id,
            description=f"Room charge: {room.room_number if room else ''} ({nights} {duration_label})",
            line_type="ROOM_CHARGE",
            quantity=nights,
            unit_price=base_price,
            total_price=room_charge,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(room_line)

        from app.infrastructure.database.repositories.reservation_repo import ReservationRepository
        res_repo = ReservationRepository(self.session)
        services = await res_repo.get_reservation_services(reservation_id, hotel_id)

        total = invoice.total_amount
        for svc in services:
            service_line = InvoiceLineItem(
                invoice_id=invoice.id,
                hotel_id=hotel_id,
                description=f"Service: {svc['service_name']} (x{svc['quantity']})",
                line_type="SERVICE_CHARGE",
                reference_type="reservation_service",
                reference_id=UUID(svc["id"]),
                quantity=svc["quantity"],
                unit_price=svc["unit_price"],
                total_price=svc["total_price"],
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(service_line)
            total += svc["total_price"]

        invoice.total_amount = max(total, 0)
        await self.session.flush()
        return invoice

    async def get_invoices(
        self,
        hotel_id: UUID | None,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
    ) -> list[Invoice]:
        stmt = select(Invoice)
        if hotel_id is not None:
            stmt = stmt.where(Invoice.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(Invoice.status == status)
        stmt = stmt.order_by(Invoice.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_invoice(self, invoice_id: UUID, hotel_id: UUID | None) -> dict:
        if hotel_id is None:
            invoice = await self.invoice_repo.get_by_id_unscoped(invoice_id)
        else:
            invoice = await self.invoice_repo.get_by_id(invoice_id, hotel_id)
        if not invoice:
            raise NotFoundException("Invoice not found", "INVOICE_NOT_FOUND")

        line_items = await self.invoice_repo.get_line_items(invoice_id)
        payments = await self.invoice_repo.get_payments(invoice_id)

        return {
            "invoice": invoice,
            "line_items": line_items,
            "payments": payments,
        }

    async def record_payment(
        self, hotel_id: UUID, data: dict, created_by: UUID
    ) -> Payment:
        invoice = await self.invoice_repo.get_by_id(data["invoice_id"], hotel_id)
        if not invoice:
            raise NotFoundException("Invoice not found", "INVOICE_NOT_FOUND")

        if invoice.status == "CANCELLED":
            raise ValidationException("Cannot pay a cancelled invoice", "INVOICE_CANCELLED")

        amount = float(data["amount"])
        if amount <= 0:
            raise ValidationException("Payment amount must be positive", "INVALID_AMOUNT")

        remaining = float(invoice.total_amount) - float(invoice.paid_amount)
        if amount > remaining:
            raise ValidationException(
                f"Payment exceeds remaining balance ({remaining})", "AMOUNT_EXCEEDS_BALANCE"
            )

        hotel_code = await self._get_hotel_code(hotel_id)
        payment_number = generate_code("PAY", hotel_code)

        payment = Payment(
            hotel_id=hotel_id,
            invoice_id=data["invoice_id"],
            payment_number=payment_number,
            amount=amount,
            payment_method=data["payment_method"],
            payment_date=data.get("payment_date") or date.today(),
            reference=data.get("reference"),
            notes=data.get("notes"),
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(payment)

        new_paid = float(invoice.paid_amount) + amount
        new_total = float(invoice.total_amount)
        if new_paid >= new_total:
            invoice.status = "PAID"
        else:
            invoice.status = "PARTIALLY_PAID"
        invoice.paid_amount = new_paid

        from app.infrastructure.database.models.reservation import Reservation
        res_stmt = select(Reservation).where(
            Reservation.id == invoice.reservation_id,
            Reservation.hotel_id == hotel_id,
        )
        res_result = await self.session.execute(res_stmt)
        reservation = res_result.scalar_one_or_none()
        if reservation:
            reservation.paid_amount = new_paid
            if new_paid >= new_total:
                reservation.payment_status = "PAID"
            elif new_paid > 0:
                reservation.payment_status = "PARTIALLY_PAID"

        await self.session.flush()
        return payment

    async def get_payments(
        self, hotel_id: UUID | None, invoice_id: UUID | None = None
    ) -> list[Payment]:
        if invoice_id:
            return await self.payment_repo.get_by_invoice(invoice_id, hotel_id)
        stmt = select(Payment)
        if hotel_id is not None:
            stmt = stmt.where(Payment.hotel_id == hotel_id)
        stmt = stmt.order_by(Payment.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_ledgers(self, hotel_id: UUID | None) -> list[Ledger]:
        stmt = select(Ledger).where(Ledger.is_active.is_(True))
        if hotel_id is not None:
            stmt = stmt.where(Ledger.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_journal_entry(self, hotel_id: UUID, data: dict) -> JournalEntry:
        hotel_code = await self._get_hotel_code(hotel_id)
        entry_number = generate_code("JE", hotel_code)

        lines = data.get("lines", [])
        if not lines:
            raise ValidationException("Journal entry must have at least one line", "NO_LINES")

        total_debit = sum(line.get("debit", 0) for line in lines)
        total_credit = sum(line.get("credit", 0) for line in lines)

        entry = JournalEntry(
            hotel_id=hotel_id,
            entry_number=entry_number,
            entry_date=data.get("entry_date", date.today()),
            reference_type=data.get("reference_type"),
            reference_id=data.get("reference_id"),
            description=data.get("description"),
            total_debit=total_debit,
            total_credit=total_credit,
            status="DRAFT",
        )
        self.session.add(entry)
        await self.session.flush()

        for line_data in lines:
            jline = JournalEntryLine(
                journal_entry_id=entry.id,
                hotel_id=hotel_id,
                ledger_id=line_data["ledger_id"],
                debit=line_data.get("debit", 0),
                credit=line_data.get("credit", 0),
                description=line_data.get("description"),
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(jline)

        await self.session.flush()
        return entry

    async def get_journal_entries(
        self,
        hotel_id: UUID | None,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
    ) -> list[JournalEntry]:
        return await self.journal_entry_repo.get_by_hotel(hotel_id, skip, limit, status)
