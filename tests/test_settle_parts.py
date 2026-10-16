"""Bo'lib to'lash (settle-payment): bir necha bo'lak, har xil usul.

Xona almashtirishdan keyingi qo'shimcha to'lov (qarz) endi BO'LIB ham
to'lanadi: masalan yarmi naqd, yarmi online. Har bo'lak alohida Payment
bo'lib yoziladi; jami suma endpointda tekshiriladi. Mavjud yakka usulli
yo'l o'zgarmagan.

Bazasiz: sessiya/repo o'rinbosarlari bilan.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pydantic
import pytest

from app.application.services.reservation_service import ReservationService
from app.core.exceptions import ValidationException
from app.presentation.api.v1 import reservations as reservations_api

HOTEL = uuid.uuid4()
USER = {"id": str(uuid.uuid4()), "user_type": "ADMIN", "hotel_id": str(HOTEL), "permissions": []}


class FakeInvoice:
    def __init__(self, total=300000.0, paid=200000.0):
        self.id = uuid.uuid4()
        self.total_amount = total
        self.paid_amount = paid
        self.status = "PARTIALLY_PAID"


class FakeRepo:
    def __init__(self, reservation):
        self.reservation = reservation

    async def get_by_id(self, reservation_id, hotel_id):
        if reservation_id != self.reservation.id:
            return None
        return self.reservation


class FakeInvoiceRepo:
    def __init__(self, invoice):
        self.invoice = invoice

    async def get_by_reservation(self, reservation_id, hotel_id):
        return self.invoice


class FakeSession:
    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass


def make_reservation(**overrides):
    base = dict(
        id=uuid.uuid4(),
        status="CONFIRMED",
        total_amount=300000.0,
        paid_amount=200000.0,
        payment_status="PARTIALLY_PAID",
        booking_type="DAILY",
        room_id=uuid.uuid4(),
        check_in_date=None,
        check_out_date=None,
        discount_amount=0,
        move_discount_amount=0,
        penalty_amount=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def build_service(monkeypatch, reservation=None, invoice=None):
    reservation = reservation or make_reservation()
    invoice = invoice or FakeInvoice()
    svc = ReservationService.__new__(ReservationService)
    svc.session = FakeSession()
    svc.repo = FakeRepo(reservation)
    svc.invoice_repo = FakeInvoiceRepo(invoice)

    async def fake_hotel_code(self, hotel_id):
        return "TST"

    monkeypatch.setattr(ReservationService, "_get_hotel_code", fake_hotel_code)
    return svc, reservation, invoice


# ------------------------------------------------ xizmat: bo'lib to'lash --

def test_parts_create_one_payment_per_part(monkeypatch):
    svc, reservation, invoice = build_service(monkeypatch)
    parts = [
        {"amount": 50000.0, "payment_method": "CASH"},
        {"amount": 50000.0, "payment_method": "ONLINE"},
    ]

    out = asyncio.run(
        svc.settle_payment(
            HOTEL, reservation.id, 100000.0, "CASH", "PAY", uuid.uuid4(), parts
        )
    )

    # Ikki qator, har biri o'z usulida; izohda bo'lak raqami
    assert len(svc.session.added) == 2
    first, second = svc.session.added
    assert float(first.amount) == 50000.0 and first.payment_method == "CASH"
    assert float(second.amount) == 50000.0 and second.payment_method == "ONLINE"
    assert "bo'lib to'lash 1/2" in first.notes
    assert "bo'lib to'lash 2/2" in second.notes

    # Hisoblar: yig'indi bo'yicha (100 000), ikki marta qo'shilmagan
    assert float(reservation.paid_amount) == 300000.0
    assert reservation.payment_status == "PAID"
    assert float(invoice.paid_amount) == 300000.0
    assert out is reservation


def test_single_payment_path_is_unchanged(monkeypatch):
    svc, reservation, _ = build_service(monkeypatch)

    asyncio.run(
        svc.settle_payment(HOTEL, reservation.id, 100000.0, "CASH", "PAY", uuid.uuid4())
    )

    assert len(svc.session.added) == 1
    assert float(svc.session.added[0].amount) == 100000.0
    assert "bo'lib to'lash" not in svc.session.added[0].notes
    assert float(reservation.paid_amount) == 300000.0


def test_parts_over_remaining_is_rejected(monkeypatch):
    """Jami qarzdan oshsa — eski tekshiruv hamon ishlaydi."""
    svc, reservation, _ = build_service(monkeypatch)
    parts = [{"amount": 150000.0, "payment_method": "CASH"}]

    with pytest.raises(ValidationException) as exc:
        asyncio.run(
            svc.settle_payment(
                HOTEL, reservation.id, 150000.0, "CASH", "PAY", uuid.uuid4(), parts
            )
        )

    assert exc.value.error_code == "AMOUNT_EXCEEDS_BALANCE"
    assert svc.session.added == []


# --------------------------------------------- endpoint: so'rov tekshiruvi --

class NeverSession:
    """Xato shoxobchalarida bazaga umuman tegmasligi kerak."""

    def __getattr__(self, name):
        raise AssertionError(f"session.{name} chaqirilmasligi kerak edi")


def _request(**overrides):
    from app.application.dto.reservation import SettlePaymentRequest
    from app.presentation.api.v1.reservations import (
        settle_payment as endpoint,
    )

    payload = dict(direction="PAY")
    payload.update(overrides)
    data = SettlePaymentRequest(**payload)
    return endpoint, data


def test_endpoint_rejects_parts_on_refund():
    endpoint, data = _request(
        direction="REFUND",
        payments=[{"amount": 10000, "payment_method": "CASH"}],
    )
    with pytest.raises(ValidationException) as exc:
        asyncio.run(
            endpoint(
                reservation_id=uuid.uuid4(),
                data=data,
                session=NeverSession(),
                current_user=USER,
            )
        )
    assert exc.value.error_code == "PARTS_NOT_ALLOWED"


def test_endpoint_rejects_parts_sum_mismatch():
    endpoint, data = _request(
        amount=90000,
        payments=[
            {"amount": 50000, "payment_method": "CASH"},
            {"amount": 30000, "payment_method": "ONLINE"},
        ],
    )
    with pytest.raises(ValidationException) as exc:
        asyncio.run(
            endpoint(
                reservation_id=uuid.uuid4(),
                data=data,
                session=NeverSession(),
                current_user=USER,
            )
        )
    assert exc.value.error_code == "PARTS_SUM_MISMATCH"


def test_endpoint_requires_amount_or_payments():
    endpoint, data = _request()
    with pytest.raises(ValidationException) as exc:
        asyncio.run(
            endpoint(
                reservation_id=uuid.uuid4(),
                data=data,
                session=NeverSession(),
                current_user=USER,
            )
        )
    assert exc.value.error_code == "SETTLE_AMOUNT_REQUIRED"


def test_request_model_shape():
    from app.application.dto.reservation import SettlePaymentRequest

    # Yakka usul (eski mijozlar) hamon ishlaydi
    old = SettlePaymentRequest(amount=50000, payment_method="CASH", direction="PAY")
    assert old.amount == 50000 and old.payments is None

    # Bo'laklar bilan: amount/payment_method shart emas
    new = SettlePaymentRequest(
        direction="PAY",
        payments=[
            {"amount": 50000, "payment_method": "CASH"},
            {"amount": 25000, "payment_method": "ONLINE"},
        ],
    )
    assert new.amount is None and len(new.payments) == 2

    with pytest.raises(pydantic.ValidationError):
        SettlePaymentRequest(direction="PAY", payments=[{"amount": 0, "payment_method": "CASH"}])
    with pytest.raises(pydantic.ValidationError):
        SettlePaymentRequest(direction="PAY", payments=[{"amount": 1, "payment_method": "CRYPTO"}])
