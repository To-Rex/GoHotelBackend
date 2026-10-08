"""Do'kon savdosini QISMAN va ARALASH to'lash.

Foydalanuvchi talabi: do'konda qisman to'lash va aralash (masalan naqd +
bank kartasi) to'lash imkoniyati bo'lsin, istalgancha davom etsin. Mavjud
funksiyalar 100% saqlansin: oddiy sotuv, to'liq to'lash va bo'lib to'lash
avvalgidek ishlaydi, eski ma'lumot va hisob-kitoblar o'zgarmaydi.
"""
import asyncio
import inspect
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.services import shop_payments as sp
from app.core.exceptions import ConflictException, ValidationException
from app.infrastructure.database.models.shop import ShopSale, ShopSalePayment
from app.presentation.api.v1 import shop as shop_api

HOTEL = uuid.uuid4()
DILNOZA = uuid.uuid4()
TEMUR = uuid.uuid4()
T1 = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)


def pending_sale(total=100_000):
    return SimpleNamespace(
        id=uuid.uuid4(), hotel_id=HOTEL, reservation_id=uuid.uuid4(), total_amount=Decimal(str(total)),
        paid_amount=Decimal("0"), payment_method=None, payments=None, status="PENDING", paid_at=None,
        created_by=DILNOZA, created_at=T1, items=[],
    )


class Db:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


# ------------------------------------------------------------ qoidalar --
def test_partial_then_mixed_then_rest():
    sale, db = pending_sale(), Db()
    # 1) 30 000 naqd — qisman
    sp.apply_payment(db, sale, [{"amount": 30000, "payment_method": "CASH"}],
                     user_id=DILNOZA, user_name="Dilnoza R", now=T1)
    assert sale.status == "PENDING" and float(sale.paid_amount) == 30000
    assert sp.remaining_of(sale) == 70000 and sale.paid_at is None
    assert sale.payment_method == "CASH"
    # 2) ertasiga aralash: 20 000 naqd + 30 000 karta — boshqa xodim
    sp.apply_payment(db, sale, [{"amount": 20000, "payment_method": "CASH"},
                                {"amount": 30000, "payment_method": "CARD"}],
                     user_id=TEMUR, user_name="Temur K", now=T2)
    assert sale.status == "PENDING" and sp.remaining_of(sale) == 20000
    assert sale.payment_method == "MIXED"
    # 3) qoldiq karta bilan — to'liq to'landi
    sp.apply_payment(db, sale, [{"amount": 20000, "payment_method": "CARD"}],
                     user_id=TEMUR, user_name="Temur K", now=T3)
    assert sale.status == "PAID" and sale.paid_at == T3 and sp.remaining_of(sale) == 0
    # Tarix: har bo'lak o'z vaqti va qabul qilgan xodimi bilan
    assert [p["amount"] for p in sale.payments] == [30000, 20000, 30000, 20000]
    assert sale.payments[1]["created_by_name"] == "Temur K"
    assert sale.payments[0]["paid_at"] == T1.isoformat()
    # Har bo'lak — alohida to'lov qatori (tushum va kassa shulardan)
    rows = [o for o in db.added if isinstance(o, ShopSalePayment)]
    assert [(float(r.amount), r.payment_method, r.created_by) for r in rows] == [
        (30000, "CASH", DILNOZA), (20000, "CASH", TEMUR), (30000, "CARD", TEMUR), (20000, "CARD", TEMUR),
    ]
    assert rows[0].paid_at == T1 and rows[3].paid_at == T3


def test_single_full_payment_keeps_the_old_shape():
    sale, db = pending_sale(45000), Db()
    sp.apply_payment(db, sale, [{"amount": 45000, "payment_method": "CARD"}],
                     user_id=DILNOZA, user_name=None, now=T1)
    assert sale.status == "PAID" and sale.payment_method == "CARD"
    # Avvalgidek: bitta usulda to'liq — payments bo'sh
    assert sale.payments is None


def test_cannot_overpay_or_pay_zero():
    sale = pending_sale(50000)
    with pytest.raises(ValidationException) as err:
        sp.apply_payment(Db(), sale, [{"amount": 30000, "payment_method": "CASH"},
                                      {"amount": 30000, "payment_method": "CARD"}],
                         user_id=DILNOZA, user_name=None, now=T1)
    assert err.value.error_code == "SHOP_PAYMENT_EXCEEDS_REMAINING"
    with pytest.raises(ValidationException):
        sp.apply_payment(Db(), sale, [{"amount": 0, "payment_method": "CASH"}],
                         user_id=DILNOZA, user_name=None, now=T1)
    with pytest.raises(ValidationException):
        sp.apply_payment(Db(), sale, [], user_id=DILNOZA, user_name=None, now=T1)
    # Hech narsa o'zgarmagan
    assert float(sale.paid_amount) == 0 and sale.status == "PENDING"


# ---------------------------------------------------------- endpointlar --
class PayDb:
    def __init__(self, sale):
        self.sale = sale
        self.added = []

    async def get(self, model, key, options=None, **kwargs):
        if model is ShopSale:
            # To'lashda savdo qatori qulflanadi
            assert kwargs.get("with_for_update") is True
            return self.sale
        if model.__name__ == "User":
            return SimpleNamespace(first_name="Temur", last_name="K")
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


USER = {"id": str(TEMUR), "user_type": "ADMIN", "hotel_id": HOTEL}


def pay(sale, **body):
    db = PayDb(sale)
    out = asyncio.run(shop_api.pay_sale(
        sale_id=sale.id, data=shop_api.SalePayRequest(**body), hotel_id=None, session=db,
        current_user=USER, _shift=None,
    ))
    return out, db


def test_pay_endpoint_partial_single_method():
    sale = pending_sale(100_000)
    out, db = pay(sale, payment_method="CASH", amount=40000)
    assert out["status"] == "PENDING"
    assert out["paid_amount"] == 40000 and out["remaining_amount"] == 60000
    out, _ = pay(sale, payments=[{"amount": 50000, "payment_method": "CARD"},
                                 {"amount": 10000, "payment_method": "CASH"}])
    assert out["status"] == "PAID" and out["remaining_amount"] == 0
    assert out["payment_method"] == "MIXED"


def test_pay_endpoint_without_amount_pays_the_rest_as_before():
    sale = pending_sale(100_000)
    pay(sale, payment_method="CASH", amount=25000)
    out, _ = pay(sale, payment_method="CARD")
    assert out["status"] == "PAID" and out["paid_amount"] == 100_000
    with pytest.raises(ConflictException):
        pay(sale, payment_method="CASH")


def test_pay_endpoint_rejects_overpayment():
    sale = pending_sale(30000)
    with pytest.raises(ValidationException):
        pay(sale, payment_method="CASH", amount=30001)


class CreateDb:
    """Sotuv yaratish: bron, mahsulot, partiya, foydalanuvchi."""

    def __init__(self, price=15000, stock=10):
        self.reservation = SimpleNamespace(
            id=uuid.uuid4(), hotel_id=HOTEL, status="CHECKED_IN", is_deleted=False,
            guest_id=uuid.uuid4(), room_id=uuid.uuid4(), reservation_number="RES-1",
        )
        self.product = SimpleNamespace(id=uuid.uuid4(), hotel_id=HOTEL, is_active=True, name="Cola")
        self.batch = SimpleNamespace(id=uuid.uuid4(), remaining=stock, sale_price=Decimal(str(price)))
        self.added = []

    async def get(self, model, key, options=None):
        name = model.__name__
        if name == "Reservation":
            return self.reservation
        if name == "ShopProduct":
            return self.product
        if name == "User":
            return SimpleNamespace(first_name="Dilnoza", last_name="R")
        if name == "Guest":
            return SimpleNamespace(first_name="Ali", last_name="V")
        if name == "Room":
            return SimpleNamespace(room_number="12")
        return None

    async def execute(self, stmt):
        batch = self.batch
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [batch]))

    def add(self, obj):
        if getattr(obj, "id", None) is None and isinstance(obj, ShopSale):
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if isinstance(obj, ShopSale) and obj.id is None:
                obj.id = uuid.uuid4()


def create(db, **body):
    data = shop_api.SaleCreateRequest(items=[{"product_id": db.product.id, "quantity": 4}], **body)
    return asyncio.run(shop_api.create_sale(
        data=data, hotel_id=None, session=db,
        current_user={"id": str(DILNOZA), "user_type": "ADMIN", "hotel_id": HOTEL}, _shift=None,
    ))


def rows_of(db):
    return [o for o in db.added if isinstance(o, ShopSalePayment)]


def test_booking_sale_with_partial_payment_now():
    db = CreateDb()
    out = create(db, reservation_id=db.reservation.id,
                 payments=[{"amount": 20000, "payment_method": "CASH"},
                           {"amount": 10000, "payment_method": "CARD"}])
    # 4 × 15 000 = 60 000; hozir 30 000, qolgani bron hisobida
    assert out["status"] == "PENDING" and out["paid_amount"] == 30000
    assert out["remaining_amount"] == 30000
    assert [(float(r.amount), r.payment_method) for r in rows_of(db)] == [(20000, "CASH"), (10000, "CARD")]


def test_booking_sale_without_payment_is_unchanged():
    db = CreateDb()
    out = create(db, reservation_id=db.reservation.id)
    assert out["status"] == "PENDING" and out["paid_amount"] == 0 and out["remaining_amount"] == 60000
    assert rows_of(db) == []


def test_direct_sale_records_payment_rows():
    db = CreateDb()
    out = create(db, payment_method="CASH")
    assert out["status"] == "PAID" and out["paid_amount"] == 60000
    assert [(float(r.amount), r.payment_method) for r in rows_of(db)] == [(60000, "CASH")]
    db = CreateDb()
    out = create(db, payments=[{"amount": 40000, "payment_method": "CASH"},
                               {"amount": 20000, "payment_method": "CARD"}])
    assert out["payment_method"] == "MIXED" and out["payments"][0]["amount"] == 40000
    assert len(rows_of(db)) == 2
    # Oddiy sotuvda bo'laklar jami summaga teng bo'lishi shart — avvalgidek
    with pytest.raises(ValidationException):
        create(CreateDb(), payments=[{"amount": 10000, "payment_method": "CASH"}])


# ------------------------------------------- hisob-kitob manbalari --
def test_money_is_counted_from_payment_rows():
    from app.application.services.finance_summary_service import FinanceSummaryService
    from app.application.services.personal_report_service import PersonalReportService
    from app.application.services.shift_service import ShiftService

    for fn in (
        FinanceSummaryService._shop,
        FinanceSummaryService.daily,
        FinanceSummaryService.by_staff,
        ShiftService.cash_breakdown,
        PersonalReportService._shop,
    ):
        assert "ShopSalePayment" in inspect.getsource(fn), fn
    # Do'kon qarzi — qoldiq (qisman to'langanining qolgani)
    assert "ShopSale.total_amount - ShopSale.paid_amount" in inspect.getsource(FinanceSummaryService._shop)


def test_reset_data_clears_payment_rows_before_sales():
    from app.application.services.maintenance_service import HOTEL_SCOPED_TABLES

    assert HOTEL_SCOPED_TABLES.index("shop_sale_payments") < HOTEL_SCOPED_TABLES.index("shop_sales")
    assert HOTEL_SCOPED_TABLES.index("reservation_penalties") < HOTEL_SCOPED_TABLES.index("reservations")
