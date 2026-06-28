from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.invoice import Invoice
from app.application.services.finance_service import FinanceService
from app.application.dto.finance import (
    PaymentCreateRequest,
    InvoiceResponse,
    InvoiceDetailResponse,
    PaymentResponse,
    LedgerResponse,
    JournalEntryResponse,
    JournalEntryDetailResponse,
)
from app.presentation.middleware.auth import get_current_user, require_permission
from app.presentation.api.v1._deps import require_active_hotel

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


@router.get("/invoices", response_model=list[InvoiceResponse])
async def list_invoices(
    status: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = FinanceService(session)
    return await service.get_invoices(h_id, skip=skip, limit=limit, status=status)


@router.post("/invoices")
async def create_invoice(
    data: dict,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("finance.invoices.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = FinanceService(session)
    invoice = await service.create_invoice(
        h_id, data["reservation_id"], current_user["id"]
    )
    return {
        "id": str(invoice.id),
        "hotel_id": str(invoice.hotel_id),
        "reservation_id": str(invoice.reservation_id),
        "guest_id": str(invoice.guest_id),
        "invoice_number": invoice.invoice_number,
        "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else None,
        "due_date": str(invoice.due_date) if invoice.due_date else None,
        "subtotal": float(invoice.subtotal),
        "tax_amount": float(invoice.tax_amount),
        "discount_amount": float(invoice.discount_amount),
        "total_amount": float(invoice.total_amount),
        "paid_amount": float(invoice.paid_amount),
        "status": invoice.status,
        "notes": invoice.notes,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "updated_at": invoice.updated_at.isoformat() if invoice.updated_at else None,
    }


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailResponse)
async def get_invoice(
    invoice_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = FinanceService(session)
    result = await service.get_invoice(invoice_id, h_id)
    invoice = result["invoice"]
    line_items = result["line_items"]
    payments = result["payments"]

    return {
        "id": invoice.id,
        "hotel_id": invoice.hotel_id,
        "reservation_id": invoice.reservation_id,
        "guest_id": invoice.guest_id,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "subtotal": float(invoice.subtotal),
        "tax_amount": float(invoice.tax_amount),
        "discount_amount": float(invoice.discount_amount),
        "total_amount": float(invoice.total_amount),
        "paid_amount": float(invoice.paid_amount),
        "status": invoice.status,
        "notes": invoice.notes,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        "line_items": [
            {
                "id": li.id,
                "description": li.description,
                "line_type": li.line_type,
                "quantity": float(li.quantity),
                "unit_price": float(li.unit_price),
                "total_price": float(li.total_price),
            }
            for li in line_items
        ],
        "payments": [
            {
                "id": str(p.id),
                "payment_number": p.payment_number,
                "amount": float(p.amount),
                "payment_method": p.payment_method,
                "payment_date": str(p.payment_date) if p.payment_date else None,
                "reference": p.reference,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ],
    }


@router.post("/invoices/{invoice_id}/pay", response_model=PaymentResponse)
async def record_payment(
    invoice_id: UUID = Path(),
    data: PaymentCreateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("finance.payments.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            invoice = await session.get(Invoice, invoice_id)
            if not invoice:
                raise NotFoundException("Invoice not found", "INVOICE_NOT_FOUND")
            h_id = invoice.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = FinanceService(session)
    payload = data.model_dump()
    payload["invoice_id"] = invoice_id
    return await service.record_payment(h_id, payload, current_user["id"])


@router.get("/payments", response_model=list[PaymentResponse])
async def list_payments(
    invoice_id: UUID | None = Query(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = FinanceService(session)
    return await service.get_payments(h_id, invoice_id=invoice_id)


@router.get("/ledgers", response_model=list[LedgerResponse])
async def get_ledgers(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = FinanceService(session)
    return await service.get_ledgers(h_id)


@router.get("/journal-entries", response_model=list[JournalEntryResponse])
async def list_journal_entries(
    status: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = FinanceService(session)
    return await service.get_journal_entries(h_id, skip=skip, limit=limit, status=status)


@router.post("/journal-entries", response_model=JournalEntryResponse)
async def create_journal_entry(
    data: dict,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("finance.journal_entries.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = FinanceService(session)
    entry = await service.create_journal_entry(h_id, data)
    return entry
