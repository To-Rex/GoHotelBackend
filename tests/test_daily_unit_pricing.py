"""Kunlik bron hisobi 24 soatlik bo'lsa — bir kun = xona narxi × 2.

Foydalanuvchi talabi: Sozlamalarda "Kunlik bron hisobi — 24 soatlik"
tanlangan bo'lsa, kunlik (kalendar) bronda 1 kun uchun ikkita 12 soatlik
narx olinsin: 250 000 so'mlik xona — kuniga 500 000 so'm. Mavjud
funksiyalar 100% saqlansin: 12 soatlik rejim, soatlik bronlar va
sozlamadan oldin yaratilgan bronlar narxi o'zgarmaydi.
"""
import asyncio
import inspect
import uuid
from datetime import timedelta
from types import SimpleNamespace

from app.application.services import daily_unit as du
from app.application.services.reservation_service import ReservationService
from tests.test_move_discount_policy import (
    ADMIN,
    HOTEL_ID,
    NEW_ROOM,
    OLD_ROOM,
    PRICES,
    local_today,
    make_reservation,
    make_service,
    move,
    run_checkout,
)

FULL_DAY = {"booking": {"default_type": "DAILY", "daily_unit": "24h"}}


# --------------------------------------------------------- qoidalar --
def test_unit_rules():
    assert du.resolve_daily_unit(None) == "12h"
    assert du.resolve_daily_unit({"booking": "x"}) == "12h"
    assert du.resolve_daily_unit({"booking": {"daily_unit": "48h"}}) == "12h"
    assert du.resolve_daily_unit(FULL_DAY) == "24h"
    assert du.daily_multiplier("DAILY", "24h") == 2
    assert du.daily_multiplier(None, "24h") == 2
    assert du.daily_multiplier("DAILY", "12h") == 1
    # Soatlik bronga tegishli emas
    assert du.daily_multiplier("HOURLY", "24h") == 1
    assert du.unit_price(250_000, "DAILY", "24h") == 500_000
    assert du.unit_price(250_000, "DAILY", None) == 250_000
    # Ustuni yo'q eski yozuv — 12 soatlik
    assert du.reservation_daily_unit(SimpleNamespace()) == "12h"
    assert du.reservation_daily_unit(SimpleNamespace(daily_unit="24h")) == "24h"


# --------------------------------------------------------- yaratish --
class CreateSession:
    def __init__(self, hotel_settings):
        self.hotel = SimpleNamespace(id=HOTEL_ID, settings=hotel_settings, status="ACTIVE")
        self.added = []

    async def get(self, model, key):
        return self.hotel if key == HOTEL_ID else None

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            try:
                obj.id = uuid.uuid4()
            except AttributeError:
                pass
        self.added.append(obj)

    async def flush(self):
        return None

    async def execute(self, *_):
        raise AssertionError("kutilmagan so'rov")


class CreateRepo:
    def __init__(self):
        self.created = None

    async def check_room_availability(self, *args, **kwargs):
        return True

    async def create(self, reservation):
        reservation.id = uuid.uuid4()
        self.created = reservation
        return reservation


class CreateRoomRepo:
    def __init__(self):
        self.room = SimpleNamespace(
            id=OLD_ROOM, room_number="101", room_type_id=uuid.uuid4(), current_status="AVAILABLE",
            branch_id=uuid.uuid4(),
        )

    async def get_by_id(self, room_id, hotel_id):
        return self.room

    async def update(self, room, **values):
        for key, value in values.items():
            setattr(room, key, value)
        return room


def create(hotel_settings, monkeypatch, **data):
    from app.application.services import blacklist_service

    async def allowed(self, guest_ids, hotel_id):
        return None

    monkeypatch.setattr(blacklist_service.BlacklistService, "assert_bookable", allowed)

    svc = ReservationService.__new__(ReservationService)
    svc.session = CreateSession(hotel_settings)
    svc.repo = CreateRepo()
    svc.room_repo = CreateRoomRepo()
    guest = SimpleNamespace(id=uuid.uuid4(), first_name="Ali", last_name="Valiyev")

    async def get_guest(guest_id):
        return guest

    svc.guest_repo = SimpleNamespace(get_by_id_unscoped=get_guest)

    async def base_price(room_id, hotel_id):
        return 250_000.0

    async def bookable(*args, **kwargs):
        return None

    async def hotel_code(hotel_id):
        return "H"

    svc._get_room_base_price = base_price
    svc._assert_room_bookable = bookable
    svc._get_hotel_code = hotel_code

    start = local_today() + timedelta(days=1)
    payload = {
        "guest_id": guest.id,
        "room_id": OLD_ROOM,
        "booking_type": "DAILY",
        "check_in_date": start,
        "check_out_date": start + timedelta(days=1),
        "adults": 1,
        **data,
    }
    reservation = asyncio.run(svc.create_reservation(HOTEL_ID, uuid.uuid4(), payload, uuid.uuid4()))
    return reservation, svc


def lines_of(svc):
    return [o for o in svc.session.added if getattr(o, "line_type", None) == "ROOM_CHARGE"]


def test_full_day_charges_two_half_day_prices(monkeypatch):
    res, svc = create(FULL_DAY, monkeypatch, payment_amount=500_000, payment_method="CASH")
    # 1 kun: 250 000 × 2
    assert float(res.total_amount) == 500_000
    assert res.daily_unit == "24h"
    assert res.payment_status == "PAID"
    line = lines_of(svc)[0]
    assert float(line.unit_price) == 500_000 and float(line.total_price) == 500_000
    assert line.quantity == 1


def test_two_full_days(monkeypatch):
    start = local_today() + timedelta(days=1)
    res, _ = create(FULL_DAY, monkeypatch, check_in_date=start, check_out_date=start + timedelta(days=2))
    assert float(res.total_amount) == 1_000_000


def test_half_day_mode_is_unchanged(monkeypatch):
    res, _ = create({}, monkeypatch)
    assert float(res.total_amount) == 250_000
    assert res.daily_unit == "12h"
    res, _ = create({"booking": {"daily_unit": "12h"}}, monkeypatch)
    assert float(res.total_amount) == 250_000


def test_hourly_booking_is_not_doubled(monkeypatch):
    from datetime import datetime

    start = local_today() + timedelta(days=1)
    res, _ = create(
        FULL_DAY,
        monkeypatch,
        booking_type="HOURLY",
        check_in_date=start,
        check_out_date=start + timedelta(days=1),
        check_in_datetime=datetime(start.year, start.month, start.day, 14),
        check_out_datetime=datetime(start.year, start.month, start.day, 16),
    )
    assert float(res.total_amount) == 250_000
    assert res.daily_unit == "12h"


def test_percent_discount_uses_the_full_day_price(monkeypatch):
    res, _ = create(FULL_DAY, monkeypatch, discount_percent=10)
    assert float(res.total_amount) == 450_000


# ------------------------------------------ ko'chirish va chiqish --
def test_move_room_keeps_the_reservation_unit():
    """2 kun, 100k -> 150k (12 soatlik narx): 24 soatlik bronda 150k × 2 × 2."""
    res = make_reservation(daily_unit="24h", total_amount=400_000)
    svc = make_service(res, hotel_settings=FULL_DAY)
    move(svc, res, NEW_ROOM, actor=ADMIN)
    assert res.total_amount == PRICES[NEW_ROOM] * 2 * 2
    entry = res.room_moves[-1]
    assert entry["price_increase"] == (PRICES[NEW_ROOM] - PRICES[OLD_ROOM]) * 2 * 2


def test_setting_change_does_not_reprice_existing_bookings():
    # Sozlamadan oldin yaratilgan bron (12 soatlik) — sozlama 24h bo'lsa ham
    res = make_reservation(total_amount=200_000)
    svc = make_service(res, hotel_settings=FULL_DAY)
    move(svc, res, NEW_ROOM, actor=ADMIN)
    assert res.total_amount == PRICES[NEW_ROOM] * 2
    # Ustuni umuman yo'q yozuv ham
    legacy = make_reservation(total_amount=200_000)
    assert not hasattr(legacy, "daily_unit")


def test_checkout_keeps_the_full_day_price():
    start = local_today()
    invoice = SimpleNamespace(
        id=uuid.uuid4(), subtotal=600_000, discount_amount=0, tax_amount=0,
        total_amount=600_000, paid_amount=600_000, status="PAID", invoice_number="INV-7",
    )
    res = make_reservation(
        room_id=NEW_ROOM, status="CHECKED_IN", daily_unit="24h",
        check_in_date=start, check_out_date=start + timedelta(days=2),
        total_amount=600_000, paid_amount=600_000, reservation_number="R-7",
    )
    result = run_checkout(res, invoice, [SimpleNamespace(line_type="ROOM_CHARGE", total_price=600_000)])
    assert result["total_amount"] == 600_000
    assert res.total_amount == 600_000


def test_checkout_of_half_day_booking_is_unchanged():
    start = local_today()
    invoice = SimpleNamespace(
        id=uuid.uuid4(), subtotal=300_000, discount_amount=0, tax_amount=0,
        total_amount=300_000, paid_amount=0, status="ISSUED", invoice_number="INV-8",
    )
    res = make_reservation(
        room_id=NEW_ROOM, status="CHECKED_IN",
        check_in_date=start, check_out_date=start + timedelta(days=2),
        total_amount=300_000, reservation_number="R-8",
    )
    result = run_checkout(res, invoice, [SimpleNamespace(line_type="ROOM_CHARGE", total_price=300_000)])
    assert result["total_amount"] == 300_000


def test_every_recalculation_uses_the_reservation_unit():
    from app.application.services.finance_service import FinanceService

    for fn in (
        ReservationService.move_room,
        ReservationService.check_out,
        ReservationService.settle_payment,
        FinanceService.create_invoice,
    ):
        source = inspect.getsource(fn)
        assert "reservation_daily_unit(reservation)" in source or "unit = reservation_daily_unit" in source, fn
    assert "daily_unit=daily_unit" in inspect.getsource(ReservationService.create_reservation)


def test_response_field_defaults():
    from app.application.dto.reservation import ReservationResponse

    assert ReservationResponse.model_fields["daily_unit"].default == "12h"
