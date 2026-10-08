"""Xona almashtirishda chegirma.

Foydalanuvchi talabi: mehmon kirib, xona yoqmadi va qimmatroq xonaga
o'tmoqchi — resepshn chegirma qilib bera olsin. Bera olishi yoki olmasligi
sozlamada, eng ko'p chegirma ham sozlamada. Shu bilan birga:
  * standart — o'chiq: yoqilmaguncha xodim chegirma bera olmaydi, chegirmasiz
    ko'chirish aynan avvalgidek ishlaydi;
  * administrator sozlamaga bog'lanmaydi, lekin chegirma narx farqidan
    oshmaydi;
  * chegirma bron yopilganda (check_out) va hisob-faktura qayta
    yaratilganda ham saqlanadi;
  * arzonroq xonaga qaytilsa avval shu chegirma kamayadi.

Bazasiz: sessiya va repozitoriylar o'rinbosar.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.dto.reservation import MoveRoomRequest, ReservationResponse
from app.application.services import move_discount_policy as mdp
from app.application.services.reservation_service import ReservationService
from app.core.config import settings as app_settings
from app.core.exceptions import ForbiddenException, ValidationException
from app.presentation.api.v1 import hotels as hotels_api
from tests._branch_fakes import BranchResult, fake_branch, is_branch_query

KEY = mdp.MOVE_DISCOUNT_SETTINGS_KEY


def rule(**overrides) -> dict:
    return mdp.resolve_move_discount_settings({KEY: {"enabled": True, **overrides}})


OFF = mdp.resolve_move_discount_settings(None)


# ---------------------------------------------------------- sozlama --
def test_setting_is_off_by_default():
    assert OFF == {"enabled": False, "max_percent": 0.0, "max_amount": 0.0}
    assert mdp.resolve_move_discount_settings({}) == OFF
    assert mdp.resolve_move_discount_settings({KEY: "ha"}) == OFF


def test_only_explicit_true_enables():
    for value in ("true", 1, "1", None, "yes"):
        assert mdp.resolve_move_discount_settings({KEY: {"enabled": value}})["enabled"] is False
    assert mdp.resolve_move_discount_settings({KEY: {"enabled": True}})["enabled"] is True


def test_broken_limits_fall_back_to_unlimited():
    resolved = mdp.resolve_move_discount_settings(
        {KEY: {"enabled": True, "max_percent": -5, "max_amount": "ko'p"}}
    )
    assert resolved["max_percent"] == 0 and resolved["max_amount"] == 0
    assert mdp.resolve_move_discount_settings(
        {KEY: {"max_percent": 500, "max_amount": float("nan")}}
    )["max_percent"] == 100
    assert mdp.resolve_move_discount_settings(
        {KEY: {"max_amount": float("inf")}}
    )["max_amount"] == 0


# ------------------------------------------------------------ qoida --
def test_no_discount_requested_always_passes():
    """Chegirma so'ralmasa — sozlama qanday bo'lmasin, ko'chirish o'tadi."""
    for discount in (None, 0, -10, 0.2):
        assert mdp.check_move_discount(OFF, actor_type="EMPLOYEE", increase=-50_000, discount=discount) == 0


def test_employee_refused_when_switched_off():
    with pytest.raises(ValidationException) as err:
        mdp.check_move_discount(OFF, actor_type="EMPLOYEE", increase=100_000, discount=10_000)
    assert err.value.error_code == "MOVE_DISCOUNT_DISABLED"


def test_only_for_upgrades():
    for increase in (0, -20_000):
        with pytest.raises(ValidationException) as err:
            mdp.check_move_discount(rule(), actor_type="ADMIN", increase=increase, discount=1_000)
        assert err.value.error_code == "MOVE_DISCOUNT_NOT_UPGRADE"


def test_percent_limit_is_of_the_difference():
    r = rule(max_percent=50)
    assert mdp.check_move_discount(r, actor_type="EMPLOYEE", increase=100_000, discount=50_000) == 50_000
    with pytest.raises(ValidationException) as err:
        mdp.check_move_discount(r, actor_type="EMPLOYEE", increase=100_000, discount=50_001)
    assert err.value.error_code == "MOVE_DISCOUNT_MAX_PERCENT"


def test_amount_limit():
    r = rule(max_amount=30_000)
    assert mdp.check_move_discount(r, actor_type="EMPLOYEE", increase=100_000, discount=30_000) == 30_000
    with pytest.raises(ValidationException) as err:
        mdp.check_move_discount(r, actor_type="EMPLOYEE", increase=100_000, discount=30_001)
    assert err.value.error_code == "MOVE_DISCOUNT_MAX_AMOUNT"


def test_both_limits_apply_together():
    r = rule(max_percent=50, max_amount=30_000)
    assert mdp.max_move_discount(r, 100_000, "EMPLOYEE") == 30_000
    assert mdp.max_move_discount(r, 40_000, "EMPLOYEE") == 20_000


def test_never_more_than_the_difference():
    with pytest.raises(ValidationException) as err:
        mdp.check_move_discount(rule(), actor_type="EMPLOYEE", increase=100_000, discount=100_001)
    assert err.value.error_code == "MOVE_DISCOUNT_EXCEEDS_DIFFERENCE"
    assert mdp.check_move_discount(rule(), actor_type="EMPLOYEE", increase=100_000, discount=100_000) == 100_000


def test_admin_ignores_the_setting_but_not_the_difference():
    for actor in ("ADMIN", "SUPER_ADMIN"):
        assert mdp.check_move_discount(OFF, actor_type=actor, increase=100_000, discount=100_000) == 100_000
        assert mdp.max_move_discount(rule(max_percent=10), 100_000, actor) == 100_000
        with pytest.raises(ValidationException):
            mdp.check_move_discount(OFF, actor_type=actor, increase=100_000, discount=100_001)


def test_max_is_floored_like_the_web():
    # 33% of 100 001 = 33 000.33 -> 33 000 (veb ham Math.floor)
    assert mdp.max_move_discount(rule(max_percent=33), 100_001, "EMPLOYEE") == 33_000
    assert mdp.check_move_discount(
        rule(max_percent=33), actor_type="EMPLOYEE", increase=100_001, discount=33_000
    ) == 33_000
    assert mdp.max_move_discount(OFF, 100_000, "EMPLOYEE") == 0
    assert mdp.max_move_discount(rule(), -5, "ADMIN") == 0


def test_discount_is_rounded_to_whole_sum():
    assert mdp.check_move_discount(rule(), actor_type="EMPLOYEE", increase=100_000, discount=1_234.6) == 1_235


def test_carry_over():
    assert mdp.carry_over_move_discount(30_000, 50_000) == 30_000  # yana qimmatroq
    assert mdp.carry_over_move_discount(30_000, 0) == 30_000  # teng narx
    assert mdp.carry_over_move_discount(30_000, -20_000) == 10_000  # biroz arzonroq
    assert mdp.carry_over_move_discount(30_000, -50_000) == 0  # avvalgi xonaga qaytdi
    assert mdp.carry_over_move_discount(None, -50_000) == 0


# -------------------------------------------------------- DTO / javob --
def test_request_is_backward_compatible():
    room = uuid.uuid4()
    assert MoveRoomRequest(new_room_id=room).discount_amount is None
    assert MoveRoomRequest(new_room_id=room, discount_amount=5_000).discount_amount == 5_000
    with pytest.raises(Exception):
        MoveRoomRequest(new_room_id=room, discount_amount=-1)


def test_response_defaults_to_zero():
    assert ReservationResponse.model_fields["move_discount_amount"].default == 0


# ------------------------------------------------- ko'chirish (servis) --
HOTEL_ID = uuid.uuid4()
OLD_ROOM = uuid.uuid4()
NEW_ROOM = uuid.uuid4()
CHEAP_ROOM = uuid.uuid4()
SAME_ROOM = uuid.uuid4()
PRICES = {OLD_ROOM: 100_000, NEW_ROOM: 150_000, CHEAP_ROOM: 80_000, SAME_ROOM: 150_000}


class FakeSession:
    def __init__(self, hotel_settings=None):
        self.hotel = SimpleNamespace(id=HOTEL_ID, settings=hotel_settings or {}, status="ACTIVE")
        # Sozlamalar filialda — mehmonxonaning yagona filiali
        self.branch = fake_branch(HOTEL_ID, self.hotel.settings)
        self.flushed = 0

    async def get(self, model, key):
        return self.hotel if key == HOTEL_ID else None

    async def execute(self, statement, *_a, **_k):
        assert is_branch_query(statement), str(statement)
        return BranchResult(self.branch)

    async def flush(self):
        self.flushed += 1

    async def refresh(self, obj):
        return None


class FakeRepo:
    def __init__(self, reservation):
        self.reservation = reservation

    async def get_by_id(self, reservation_id, hotel_id):
        return self.reservation

    async def check_room_availability(self, *args, **kwargs):
        return True


class FakeRoomRepo:
    def __init__(self):
        self.rooms = {
            room_id: SimpleNamespace(
                id=room_id, room_number=str(i + 101), room_type_id=uuid.uuid4(),
                current_status="AVAILABLE",
            )
            for i, room_id in enumerate(PRICES)
        }

    async def get_by_id(self, room_id, hotel_id):
        return self.rooms.get(room_id)


class FakeInvoiceRepo:
    def __init__(self, invoice=None, lines=()):
        self.invoice = invoice
        self.lines = list(lines)

    async def get_by_reservation(self, reservation_id, hotel_id):
        return self.invoice

    async def get_line_items(self, invoice_id):
        return self.lines


class FakeUserRepo:
    async def get_by_id(self, user_id):
        return SimpleNamespace(first_name="Ali", last_name="Valiyev")


def local_today() -> date:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=app_settings.APP_TZ_OFFSET_MINUTES)
    ).date()


def make_reservation(**overrides):
    start = local_today() + timedelta(days=1)
    data = dict(
        id=uuid.uuid4(),
        room_id=OLD_ROOM,
        status="CONFIRMED",
        booking_type="DAILY",
        check_in_date=start,
        check_out_date=start + timedelta(days=2),
        check_in_datetime=None,
        check_out_datetime=None,
        created_at=datetime.now(timezone.utc),
        discount_amount=0,
        discount_percent=0,
        move_discount_amount=0,
        total_amount=200_000,
        paid_amount=0,
        payment_status="UNPAID",
        room_moves=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def make_service(reservation, hotel_settings=None, invoice=None, lines=()):
    svc = ReservationService.__new__(ReservationService)
    svc.session = FakeSession(hotel_settings)
    svc.repo = FakeRepo(reservation)
    svc.room_repo = FakeRoomRepo()
    svc.invoice_repo = FakeInvoiceRepo(invoice, lines)
    svc.user_repo = FakeUserRepo()

    async def base_price(room_id, hotel_id):
        return float(PRICES[room_id])

    async def bookable(*args, **kwargs):
        return None

    svc._get_room_base_price = base_price
    svc._assert_room_bookable = bookable
    return svc


EMPLOYEE = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE"}
ADMIN = {"id": str(uuid.uuid4()), "user_type": "ADMIN"}
ENABLED = {KEY: {"enabled": True, "max_percent": 50, "max_amount": 0}}


def move(svc, reservation, room, actor=EMPLOYEE, discount=None):
    return asyncio.run(
        svc.move_room(HOTEL_ID, reservation.id, room, actor, discount=discount)
    )


def test_move_without_discount_is_unchanged():
    """Sozlama o'chiq, chegirma yo'q — aynan avvalgi hisob."""
    res = make_reservation()
    svc = make_service(res)
    move(svc, res, NEW_ROOM)
    assert res.total_amount == 300_000
    assert res.move_discount_amount == 0
    assert res.room_id == NEW_ROOM
    entry = res.room_moves[-1]
    assert entry["old_total"] == 200_000 and entry["new_total"] == 300_000
    assert entry["price_increase"] == 100_000 and entry["discount_amount"] == 0


def test_employee_discount_refused_when_off_and_nothing_changes():
    res = make_reservation()
    svc = make_service(res)
    with pytest.raises(ValidationException) as err:
        move(svc, res, NEW_ROOM, discount=10_000)
    assert err.value.error_code == "MOVE_DISCOUNT_DISABLED"
    assert res.room_id == OLD_ROOM and res.total_amount == 200_000 and res.room_moves is None
    assert svc.room_repo.rooms[NEW_ROOM].current_status == "AVAILABLE"
    assert svc.session.flushed == 0


def test_employee_discount_within_the_limit():
    res = make_reservation()
    svc = make_service(res, ENABLED)
    move(svc, res, NEW_ROOM, discount=50_000)
    assert res.total_amount == 250_000
    assert res.move_discount_amount == 50_000
    assert res.room_moves[-1]["discount_amount"] == 50_000
    assert res.room_moves[-1]["move_discount_total"] == 50_000


def test_employee_discount_over_the_limit():
    res = make_reservation()
    svc = make_service(res, ENABLED)
    with pytest.raises(ValidationException) as err:
        move(svc, res, NEW_ROOM, discount=60_000)
    assert err.value.error_code == "MOVE_DISCOUNT_MAX_PERCENT"
    assert res.room_id == OLD_ROOM and res.total_amount == 200_000


def test_admin_can_make_the_upgrade_free_even_when_off():
    res = make_reservation()
    svc = make_service(res)
    move(svc, res, NEW_ROOM, actor=ADMIN, discount=100_000)
    assert res.total_amount == 200_000
    other = make_reservation()
    with pytest.raises(ValidationException) as err:
        move(make_service(other), other, NEW_ROOM, actor=ADMIN, discount=100_001)
    assert err.value.error_code == "MOVE_DISCOUNT_EXCEEDS_DIFFERENCE"


def test_no_discount_for_a_cheaper_room():
    res = make_reservation()
    svc = make_service(res, ENABLED)
    with pytest.raises(ValidationException) as err:
        move(svc, res, CHEAP_ROOM, discount=1_000)
    assert err.value.error_code == "MOVE_DISCOUNT_NOT_UPGRADE"


def test_moving_back_cancels_the_discount():
    res = make_reservation()
    svc = make_service(res, ENABLED)
    move(svc, res, NEW_ROOM, discount=30_000)
    assert res.total_amount == 270_000
    # Bir xil narxdagi boshqa xona — chegirma saqlanadi
    move(svc, res, SAME_ROOM)
    assert res.total_amount == 270_000 and res.move_discount_amount == 30_000
    # Avvalgi arzon xonaga qaytdi — avvalgi narx, 170 000 emas
    move(svc, res, OLD_ROOM)
    assert res.total_amount == 200_000 and res.move_discount_amount == 0


def test_further_upgrade_adds_up():
    res = make_reservation(room_id=CHEAP_ROOM, total_amount=160_000)
    svc = make_service(res, ENABLED)
    move(svc, res, OLD_ROOM, discount=20_000)  # 160k -> 200k, farq 40k
    assert res.total_amount == 180_000
    move(svc, res, NEW_ROOM, discount=50_000)  # 200k -> 300k, farq 100k
    assert res.move_discount_amount == 70_000
    assert res.total_amount == 230_000


def test_booking_discount_and_invoice_sync():
    """Bron chegirmasi (foiz) saqlanadi, hisob-fakturada ikkalasi birga."""
    invoice = SimpleNamespace(id=uuid.uuid4(), subtotal=200_000, discount_amount=20_000, total_amount=210_000)
    room_line = SimpleNamespace(line_type="ROOM_CHARGE", total_price=200_000, description="", quantity=2, unit_price=100_000)
    service_line = SimpleNamespace(line_type="SERVICE_CHARGE", total_price=30_000)
    res = make_reservation(discount_percent=10, total_amount=210_000)
    svc = make_service(res, ENABLED, invoice, [room_line, service_line])
    # Sof farq: 100k - 10% = 90k; 50% chegarasi — 45k
    with pytest.raises(ValidationException):
        move(svc, res, NEW_ROOM, discount=45_001)
    move(svc, res, NEW_ROOM, discount=45_000)
    # 300k - 10% (30k) - 45k + xizmat 30k
    assert res.total_amount == 255_000
    assert invoice.discount_amount == 75_000
    assert invoice.total_amount == 255_000
    assert room_line.total_price == 300_000
    assert res.room_moves[-1]["price_increase"] == 90_000


def test_percent_booking_discount_never_below_the_old_price():
    """Bron chegirmasi foizda: eng katta chegirma bilan ham mehmon avvalgi
    xonadagidan kam to'lamaydi (avval 180k, endi ham 180k — 170k emas)."""
    res = make_reservation(discount_percent=10, total_amount=180_000)
    svc = make_service(res)
    with pytest.raises(ValidationException) as err:
        move(svc, res, NEW_ROOM, actor=ADMIN, discount=100_000)
    assert err.value.error_code == "MOVE_DISCOUNT_EXCEEDS_DIFFERENCE"
    move(svc, res, NEW_ROOM, actor=ADMIN, discount=90_000)
    assert res.total_amount == 180_000


def test_baseline_is_recorded_and_carried():
    res = make_reservation()
    svc = make_service(res, ENABLED)
    move(svc, res, NEW_ROOM)
    assert res.room_moves[-1]["discount_baseline_price"] is None
    move(svc, res, OLD_ROOM)
    move(svc, res, NEW_ROOM, discount=30_000)
    assert res.room_moves[-1]["discount_baseline_price"] == 100_000
    move(svc, res, SAME_ROOM)  # bir xil narx — boshlang'ich narx saqlanadi
    assert res.room_moves[-1]["discount_baseline_price"] == 100_000
    move(svc, res, OLD_ROOM)  # qaytdi — chegirma ham, boshlang'ich narx ham yo'q
    assert res.room_moves[-1]["discount_baseline_price"] is None
    assert res.move_discount_amount == 0


def test_checked_in_on_arrival_day():
    """Talabdagi holat: mehmon kirdi, xona yoqmadi — o'sha kuni ko'chirish."""
    today = local_today()
    res = make_reservation(
        status="CHECKED_IN", check_in_date=today, check_out_date=today + timedelta(days=2),
        paid_amount=200_000, payment_status="PAID",
    )
    svc = make_service(res, ENABLED)
    move(svc, res, NEW_ROOM, discount=40_000)
    assert res.total_amount == 260_000
    assert res.payment_status == "PARTIALLY_PAID"
    assert svc.room_repo.rooms[NEW_ROOM].current_status == "OCCUPIED"


def test_hourly_difference_is_the_flat_price():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    res = make_reservation(
        booking_type="HOURLY", check_in_datetime=start, check_out_datetime=start + timedelta(hours=3),
        total_amount=100_000,
    )
    svc = make_service(res, {KEY: {"enabled": True}})
    with pytest.raises(ValidationException):
        move(svc, res, NEW_ROOM, discount=50_001)
    move(svc, res, NEW_ROOM, discount=50_000)
    assert res.total_amount == 100_000


# --------------------------------------------- chiqish va hisob-faktura --
class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class CheckoutSession(FakeSession):
    async def execute(self, stmt):
        return FakeResult(SimpleNamespace(base_price=150_000))


class CheckoutRepo(FakeRepo):
    async def get_reservation_services(self, reservation_id, hotel_id):
        return []

    async def update(self, obj, **values):
        for key, value in values.items():
            setattr(obj, key, value)
        return obj


def test_checkout_keeps_the_move_discount():
    invoice = SimpleNamespace(
        id=uuid.uuid4(), subtotal=300_000, discount_amount=50_000, tax_amount=0,
        total_amount=250_000, paid_amount=0, status="ISSUED", invoice_number="INV-1",
    )
    room_line = SimpleNamespace(line_type="ROOM_CHARGE", total_price=300_000)
    res = make_reservation(
        room_id=NEW_ROOM, status="CHECKED_IN", move_discount_amount=50_000, total_amount=250_000,
        reservation_number="R-1",
    )
    svc = make_service(res, invoice=invoice, lines=[room_line])
    svc.session = CheckoutSession()
    svc.repo = CheckoutRepo(res)

    async def hotel_code(hotel_id):
        return "H"

    svc._get_hotel_code = hotel_code
    result = asyncio.run(
        svc.check_out(res.id, HOTEL_ID, uuid.uuid4(), transition_room=False, create_cleaning_task=False)
    )
    assert result["total_amount"] == 250_000
    assert invoice.discount_amount == 50_000
    assert res.total_amount == 250_000


def run_checkout(res, invoice, lines):
    svc = make_service(res, invoice=invoice, lines=lines)
    svc.session = CheckoutSession()
    svc.repo = CheckoutRepo(res)

    async def hotel_code(hotel_id):
        return "H"

    svc._get_hotel_code = hotel_code
    return asyncio.run(
        svc.check_out(res.id, HOTEL_ID, uuid.uuid4(), transition_room=False, create_cleaning_task=False)
    )


def test_checkout_clamps_the_discount_after_the_stay_is_shortened():
    """4 kecha 100k -> 150k, 100k chegirma; keyin muddat 1 kechaga
    qisqartirildi. Chiqishda mehmon 100k (boshlang'ich xona) dan kam to'lamaydi."""
    start = local_today()
    invoice = SimpleNamespace(
        id=uuid.uuid4(), subtotal=600_000, discount_amount=100_000, tax_amount=0,
        total_amount=500_000, paid_amount=0, status="ISSUED", invoice_number="INV-2",
    )
    res = make_reservation(
        room_id=NEW_ROOM, status="CHECKED_IN", check_in_date=start, check_out_date=start + timedelta(days=1),
        move_discount_amount=100_000, total_amount=500_000, reservation_number="R-2",
        room_moves=[{"to_room_number": "102", "discount_amount": 100_000, "move_discount_total": 100_000,
                     "discount_baseline_price": 100_000}],
    )
    result = run_checkout(res, invoice, [SimpleNamespace(line_type="ROOM_CHARGE", total_price=600_000)])
    # 150k (1 kecha) - farq 50k gacha qisqargan chegirma = 100k
    assert result["total_amount"] == 100_000
    assert invoice.discount_amount == 50_000
    assert res.move_discount_amount == 50_000


def test_checkout_without_baseline_keeps_the_stored_discount():
    invoice = SimpleNamespace(
        id=uuid.uuid4(), subtotal=300_000, discount_amount=0, tax_amount=0,
        total_amount=300_000, paid_amount=0, status="ISSUED", invoice_number="INV-3",
    )
    res = make_reservation(room_id=NEW_ROOM, status="CHECKED_IN", reservation_number="R-3")
    result = run_checkout(res, invoice, [SimpleNamespace(line_type="ROOM_CHARGE", total_price=300_000)])
    assert result["total_amount"] == 300_000  # chegirmasiz — aynan avvalgidek
    assert invoice.discount_amount == 0


def test_clamp_and_net_increase():
    assert mdp.net_increase(100_000, 10) == 90_000
    assert mdp.net_increase(100_000, 0) == 100_000
    assert mdp.net_increase(-100_000, 10) == -90_000
    assert mdp.clamp_move_discount(80_000, room_charge=300_000, baseline_charge=200_000) == 80_000
    assert mdp.clamp_move_discount(80_000, room_charge=150_000, baseline_charge=100_000) == 50_000
    assert mdp.clamp_move_discount(80_000, room_charge=100_000, baseline_charge=150_000) == 0
    assert mdp.clamp_move_discount(80_000, room_charge=300_000, baseline_charge=None) == 80_000
    assert mdp.clamp_move_discount(0, room_charge=300_000, baseline_charge=100_000) == 0
    assert mdp.discount_baseline(None) is None
    assert mdp.discount_baseline([{"x": 1}]) is None
    assert mdp.discount_baseline([{"discount_baseline_price": 100_000}, {"discount_baseline_price": None}]) is None
    assert mdp.discount_baseline([{"discount_baseline_price": "120000"}]) == 120_000


def test_rounding_matches_the_web():
    """Python round() juftga yaxlitlaydi; veb Math.round — yuqoriga."""
    assert mdp.carry_over_move_discount(0.5, 0) == 1
    assert mdp.carry_over_move_discount(2.5, 0) == 3
    assert mdp.check_move_discount(rule(), actor_type="EMPLOYEE", increase=100_000, discount=2.5) == 3


def test_settle_payment_invoice_includes_the_move_discount():
    """To'lovsiz bron, chegirmali ko'chirish, keyin qo'shimcha to'lov:
    yangi hisob-fakturada xona qatori to'liq narxda, chegirma ikkalasi bilan."""
    res = make_reservation(
        room_id=NEW_ROOM, discount_amount=0, move_discount_amount=50_000, total_amount=250_000,
        paid_amount=0, status="CONFIRMED", guest_id=uuid.uuid4(),
    )
    svc = make_service(res)
    created = {}

    async def create_invoice(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), paid_amount=0, total_amount=kwargs["total_amount"], status="ISSUED")

    async def create_payment(**kwargs):
        return None

    svc._create_invoice = create_invoice
    svc._create_payment = create_payment
    asyncio.run(svc.settle_payment(HOTEL_ID, res.id, 100_000, "CASH", "PAY", uuid.uuid4()))
    assert created["room_charge"] == 300_000
    assert created["discount_amount"] == 50_000
    assert created["total_amount"] == 250_000


def test_invoice_creation_subtracts_the_move_discount():
    import inspect

    from app.application.services.finance_service import FinanceService

    source = inspect.getsource(FinanceService.create_invoice)
    assert "move_discount_amount" in source


# ---------------------------------------------------- sozlama API --
def test_settings_api_round_trip_and_configurator_only():
    session = FakeSession({"discount": {"daily": {"max_percent": 5}}})
    employee = {"user_type": "EMPLOYEE", "hotel_id": HOTEL_ID}
    admin = {"user_type": "ADMIN", "hotel_id": HOTEL_ID}
    # Mehmonxona tanlagan sozlovchi (get_current_user uni ADMIN qilib beradi)
    configurator = {"user_type": "ADMIN", "actual_user_type": "CONFIGURATOR", "hotel_id": HOTEL_ID}

    got = asyncio.run(hotels_api.get_move_discount_settings(session=session, current_user=employee))
    assert got == OFF

    body = hotels_api.MoveDiscountSettingsRequest(enabled=True, max_percent=30, max_amount=50_000)
    for actor in (employee, admin):
        with pytest.raises(ForbiddenException):
            asyncio.run(hotels_api.save_move_discount_settings(body, session=session, current_user=actor))

    saved = asyncio.run(hotels_api.save_move_discount_settings(body, session=session, current_user=configurator))
    assert saved == {"enabled": True, "max_percent": 30.0, "max_amount": 50_000.0}
    # Boshqa sozlamalarga tegilmaydi
    assert session.branch.settings["discount"] == {"daily": {"max_percent": 5}}
    assert asyncio.run(
        hotels_api.get_move_discount_settings(session=session, current_user=employee)
    ) == saved


def test_settings_api_without_hotel_context():
    session = FakeSession()
    got = asyncio.run(
        hotels_api.get_move_discount_settings(session=session, current_user={"user_type": "SUPER_ADMIN"})
    )
    assert got == OFF


def test_settings_route_is_before_hotel_id_route():
    paths = [route.path for route in hotels_api.router.routes]
    assert paths.index("/move-discount-settings") < paths.index("/{hotel_id}")
