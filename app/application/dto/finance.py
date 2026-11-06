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


class PaymentPurposePart(BaseModel):
    """To'lovning bir qismi NIMA UCHUN: ROOM / PENALTY / SERVICE / EXTRA /
    REFUND / SHOP (`payment_purpose`)."""

    kind: str
    amount: float
    label: str | None = None


class PaymentResponse(BaseModel):
    id: UUID
    hotel_id: UUID
    #: Do'kon to'lovida (`source=SHOP`) bo'sh
    invoice_id: UUID | None = None
    payment_number: str | None = None
    amount: float
    payment_method: str
    payment_date: date | None = None
    reference: str | None
    notes: str | None
    created_at: datetime
    #: To'lov turi tahriri tarixi: [{kind, old, new, amount, total_before,
    #: total_after, by, by_name, at, note, new_payment_number}, ...].
    #: `kind`: full — butun summa; partial — qismi yangi qatorga o'tdi;
    #: split_from — shu qator boshqa to'lovdan ajralgan
    #: (`from_payment_number`, `from_total`). `app/shared/payment_splits.py`
    method_changes: list | None = None

    # --- To'lov nima uchun (GET /finance/payments ro'yxatida) ---------
    #: RESERVATION — bron to'lovi (`payments`), SHOP — do'kon to'lovi
    source: str | None = None
    #: Asosiy tur: ROOM / PENALTY / SERVICE / MIXED / REFUND / SHOP / EXTRA
    kind: str | None = None
    parts: list[PaymentPurposePart] | None = None
    #: Qarz to'lovi — mehmon chiqib ketgandan keyingi kunlarda olingan
    after_checkout: bool | None = None
    #: Pulni qabul qilgan xodim
    created_by_name: str | None = None
    reservation_id: UUID | None = None
    reservation_number: str | None = None
    reservation_status: str | None = None
    booking_type: str | None = None
    check_in_date: date | None = None
    check_out_date: date | None = None
    #: Soatlik bronda aniq soatlar
    check_in_datetime: datetime | None = None
    check_out_datetime: datetime | None = None
    room_number: str | None = None
    guest_name: str | None = None
    sale_id: UUID | None = None
    #: Do'kon to'lovida — chekdagi mahsulotlar
    items: list[dict] | None = None

    model_config = {"from_attributes": True}


class PaymentCreateRequest(BaseModel):
    invoice_id: UUID
    amount: float = Field(..., gt=0)
    # Kanonik to'rtlik (CASH/CARD/ONLINE/BANK_TRANSFER) + eski kodlar —
    # eski klientlar va bazadagi tarix uchun qabul qilinadi
    payment_method: str = Field(
        ...,
        pattern=(
            r"^(CASH|CARD|ONLINE|BANK_TRANSFER"
            r"|CREDIT_CARD|DEBIT_CARD|MOBILE_PAYMENT|TRANSFER)$"
        ),
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
