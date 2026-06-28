import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem
from app.infrastructure.database.models.journal_entry import (
    JournalEntry,
    JournalEntryLine,
)
from app.infrastructure.database.models.ledger import Ledger
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.repositories.base import TenantBaseRepository


class LedgerRepository(TenantBaseRepository[Ledger]):
    model = Ledger

    async def get_by_code(self, hotel_id: UUID, code: str) -> Ledger | None:
        stmt = select(Ledger).where(Ledger.hotel_id == hotel_id, Ledger.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_type(
        self, hotel_id: UUID, ledger_type: str
    ) -> list[Ledger]:
        stmt = select(Ledger).where(
            Ledger.hotel_id == hotel_id,
            Ledger.type == ledger_type,
            Ledger.is_active == True,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_subledgers(self, parent_id: UUID) -> list[Ledger]:
        stmt = select(Ledger).where(Ledger.parent_id == parent_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class JournalEntryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_hotel(
        self,
        hotel_id: UUID | None,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
    ) -> list[JournalEntry]:
        stmt = select(JournalEntry)
        if hotel_id is not None:
            stmt = stmt.where(JournalEntry.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(JournalEntry.status == status)
        stmt = stmt.order_by(JournalEntry.entry_date.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(
        self, entry_id: UUID, hotel_id: UUID
    ) -> JournalEntry | None:
        stmt = select(JournalEntry).where(
            JournalEntry.id == entry_id, JournalEntry.hotel_id == hotel_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_lines(self, entry_id: UUID) -> list[JournalEntryLine]:
        stmt = select(JournalEntryLine).where(
            JournalEntryLine.journal_entry_id == entry_id
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_entry(self, entry: JournalEntry) -> JournalEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def add_line(self, line: JournalEntryLine) -> JournalEntryLine:
        self.session.add(line)
        await self.session.flush()
        return line

    async def post_entry(
        self, entry: JournalEntry, user_id: UUID
    ) -> JournalEntry:
        entry.status = "POSTED"
        entry.posted_by = user_id
        entry.posted_at = datetime.datetime.now(datetime.UTC)
        await self.session.flush()
        return entry

    async def void_entry(self, entry: JournalEntry) -> JournalEntry:
        entry.status = "VOIDED"
        await self.session.flush()
        return entry


class InvoiceRepository(TenantBaseRepository[Invoice]):
    model = Invoice

    async def get_by_reservation(
        self, reservation_id: UUID, hotel_id: UUID
    ) -> Invoice | None:
        stmt = select(Invoice).where(
            Invoice.reservation_id == reservation_id, Invoice.hotel_id == hotel_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_number(
        self, hotel_id: UUID, invoice_number: str
    ) -> Invoice | None:
        stmt = select(Invoice).where(
            Invoice.hotel_id == hotel_id, Invoice.invoice_number == invoice_number
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending_invoices(
        self, hotel_id: UUID, skip: int = 0, limit: int = 100
    ) -> list[Invoice]:
        stmt = (
            select(Invoice)
            .where(
                Invoice.hotel_id == hotel_id,
                Invoice.status.in_(["ISSUED", "PARTIALLY_PAID"]),
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_line_items(self, invoice_id: UUID) -> list[InvoiceLineItem]:
        stmt = select(InvoiceLineItem).where(
            InvoiceLineItem.invoice_id == invoice_id
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add_line_item(
        self, line: InvoiceLineItem
    ) -> InvoiceLineItem:
        self.session.add(line)
        await self.session.flush()
        return line

    async def get_payments(self, invoice_id: UUID) -> list[Payment]:
        stmt = select(Payment).where(Payment.invoice_id == invoice_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class PaymentRepository(TenantBaseRepository[Payment]):
    model = Payment

    async def get_by_invoice(
        self, invoice_id: UUID, hotel_id: UUID | None
    ) -> list[Payment]:
        stmt = select(Payment).where(Payment.invoice_id == invoice_id)
        if hotel_id is not None:
            stmt = stmt.where(Payment.hotel_id == hotel_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
