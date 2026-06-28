from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InvoiceLineItemResponse(BaseModel):
    id: UUID
    description: str
    line_type: str
    quantity: float
    unit_price: float
    total_price: float

    model_config = {"from_attributes": True}


class InvoiceResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    reservation_id: UUID
    guest_id: UUID
    invoice_number: str
    invoice_date: date
    due_date: date | None
    subtotal: float
    tax_amount: float
    discount_amount: float
    total_amount: float
    paid_amount: float
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InvoiceDetailResponse(InvoiceResponse):
    line_items: list[InvoiceLineItemResponse] = []
    payments: list[dict] = []


class PaymentResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    invoice_id: UUID
    payment_number: str
    amount: float
    payment_method: str
    payment_date: date
    reference: str | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PaymentCreateRequest(BaseModel):
    invoice_id: UUID
    amount: float = Field(..., gt=0)
    payment_method: str = Field(
        ..., pattern=r"^(CASH|CREDIT_CARD|DEBIT_CARD|BANK_TRANSFER|MOBILE_PAYMENT|ONLINE)$"
    )
    payment_date: date | None = None
    reference: str | None = None
    notes: str | None = None


class LedgerResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    name: str
    code: str
    type: str
    parent_id: UUID | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class JournalEntryResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    entry_number: str
    entry_date: date
    reference_type: str | None
    reference_id: UUID | None
    description: str | None
    total_debit: float
    total_credit: float
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class JournalEntryDetailResponse(JournalEntryResponse):
    lines: list[dict] = []
