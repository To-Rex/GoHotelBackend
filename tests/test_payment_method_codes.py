"""To'lov usuli kodlari — BARCHA to'lov DTO'lari bir xil to'plamni qabul qilishi shart.

Ilova KANONIK to'rtlikni yuboradi: `CASH`, `CARD`, `ONLINE`,
`BANK_TRANSFER`. Bazada va eski klientlarda ESKI kodlar ham bor:
`CREDIT_CARD`, `DEBIT_CARD`, `MOBILE_PAYMENT`, `TRANSFER`.

Ro'yxatlar ajralib ketsa (masalan bittasida `CARD` unutilsa) foydalanuvchi
"payment_method: Input should be ..." xatosini oladi — 2026-10 da shu hol
settle-payment'da sodir bo'lgan edi. Shu sinov to'plamlarni qulflaydi:
har bir to'lov DTO'si kanonik to'rtlikni va eski kodlarni qabul qiladi,
begona kodni rad etadi.
"""
import uuid
from datetime import date

import pydantic
import pytest

CANONICAL = ("CASH", "CARD", "ONLINE", "BANK_TRANSFER")
LEGACY = ("CREDIT_CARD", "DEBIT_CARD", "MOBILE_PAYMENT", "TRANSFER")
ALL_METHODS = CANONICAL + LEGACY
JUNK = "CRYPTO"


def test_reservation_payment_item_accepts_all_codes():
    from app.application.dto.reservation import ReservationPaymentItem

    for method in ALL_METHODS:
        item = ReservationPaymentItem(amount=1000, payment_method=method)
        assert item.payment_method == method
    with pytest.raises(pydantic.ValidationError):
        ReservationPaymentItem(amount=1000, payment_method=JUNK)


def test_reservation_create_accepts_all_codes():
    from app.application.dto.reservation import ReservationCreateRequest

    base = dict(
        guest_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        check_in_date=date(2026, 10, 20),
        check_out_date=date(2026, 10, 21),
    )
    for method in ALL_METHODS:
        data = ReservationCreateRequest(**base, payment_method=method)
        assert data.payment_method == method
    # Maydon ixtiyoriy — eski klientlar yubormasligi mumkin
    assert ReservationCreateRequest(**base).payment_method is None
    with pytest.raises(pydantic.ValidationError):
        ReservationCreateRequest(**base, payment_method=JUNK)


def test_settle_payment_accepts_all_codes():
    from app.application.dto.reservation import SettlePaymentRequest

    for method in ALL_METHODS:
        data = SettlePaymentRequest(
            amount=100000, payment_method=method, direction="PAY"
        )
        assert data.payment_method == method
    with pytest.raises(pydantic.ValidationError):
        SettlePaymentRequest(amount=100000, payment_method=JUNK, direction="PAY")


def test_settle_parts_accept_all_codes():
    """Bo'lib to'lash bo'laklari — aynan shu yerda CARD unutilgan edi."""
    from app.application.dto.reservation import SettlePaymentRequest

    for method in ALL_METHODS:
        data = SettlePaymentRequest(
            direction="PAY",
            payments=[{"amount": 50000, "payment_method": method}],
        )
        assert data.payments[0].payment_method == method
    with pytest.raises(pydantic.ValidationError):
        SettlePaymentRequest(
            direction="PAY", payments=[{"amount": 50000, "payment_method": JUNK}]
        )


def test_expense_accepts_all_codes():
    from app.presentation.api.v1.expenses import ExpenseCreateRequest

    for method in ALL_METHODS:
        data = ExpenseCreateRequest(title="Xarajat", amount=10000, payment_method=method)
        assert data.payment_method == method
    with pytest.raises(pydantic.ValidationError):
        ExpenseCreateRequest(title="Xarajat", amount=10000, payment_method=JUNK)


def test_invoice_payment_accepts_all_codes():
    from app.application.dto.finance import PaymentCreateRequest

    for method in ALL_METHODS:
        data = PaymentCreateRequest(
            invoice_id=uuid.uuid4(), amount=10000, payment_method=method
        )
        assert data.payment_method == method
    with pytest.raises(pydantic.ValidationError):
        PaymentCreateRequest(
            invoice_id=uuid.uuid4(), amount=10000, payment_method=JUNK
        )


def test_shop_payments_accept_canonical_codes():
    """Do'kon — yangi modul: kanonik to'rtlik shart (TRANSFER ham qabul qilinadi)."""
    from app.presentation.api.v1.shop import SalePayRequest, SalePaymentPart

    for method in CANONICAL + ("TRANSFER",):
        assert SalePayRequest(payment_method=method).payment_method == method
        assert SalePaymentPart(amount=1000, payment_method=method).payment_method == method
    with pytest.raises(pydantic.ValidationError):
        SalePayRequest(payment_method=JUNK)


def test_payment_method_enum_contains_both_sets():
    from app.domain.enums import PaymentMethod

    for method in ALL_METHODS:
        assert PaymentMethod(method).value == method
