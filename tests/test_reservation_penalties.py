"""Bron jarimalari: kech chiqish, shikast (buzilgan narsa), boshqa.

Foydalanuvchi talabi: mijoz kech chiqsa yoki nimanidir sindirsa jarima
qo'llansin, uni kiritish uchun joy bo'lsin va kiritilgan summalar
ko'rsatilsin. Mavjud funksiyalar 100% saqlansin — jarimasiz bron hisobi
avvalgidek.

Bazasiz: sessiya o'rinbosar.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.services import reservation_penalty_service as rps
from app.core.config import settings as app_settings
from app.core.exceptions import ForbiddenException, ValidationException
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.invoice import InvoiceLineItem
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.reservation_penalty import ReservationPenalty
from tests._branch_fakes import BranchResult, fake_branch, is_branch_query

HOTEL = uuid.uuid4()
USER = uuid.uuid4()


# --------------------------------------------------------------- qoidalar --
def test_settings_default_off_and_broken_values():
    assert rps.resolve_penalty_settings(None) == {"late_hourly_amount": 0.0, "grace_minutes": 0}
    assert rps.resolve_penalty_settings({"penalty": "x"})["late_hourly_amount"] == 0
    got = rps.resolve_penalty_settings({"penalty": {"late_hourly_amount": -5, "grace_minutes": 9999}})
    assert got == {"late_hourly_amount": 0.0, "grace_minutes": 600}
    got = rps.resolve_penalty_settings({"penalty": {"late_hourly_amount": "50000", "grace_minutes": 30}})
    assert got == {"late_hourly_amount": 50000.0, "grace_minutes": 30}


def _res(**over):
    data = dict(
        id=uuid.uuid4(), hotel_id=HOTEL, status="CHECKED_IN", booking_type="DAILY",
        check_in_date=date(2026, 10, 1), check_out_date=date(2026, 10, 3),
        check_in_datetime=None, check_out_datetime=None, checkout_requested_at=None,
        total_amount=600000, paid_amount=600000, payment_status="PAID",
        penalty_amount=0, is_deleted=False,
    )
    data.update(over)
    return SimpleNamespace(**data)


def test_planned_checkout_daily_and_hourly():
    due = rps.planned_checkout(_res())
    local = due + timedelta(minutes=app_settings.APP_TZ_OFFSET_MINUTES)
    assert local.date() == date(2026, 10, 3)
    assert local.hour == app_settings.DEFAULT_CHECKOUT_HOUR
    out = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
    assert rps.planned_checkout(_res(booking_type="HOURLY", check_out_datetime=out)) == out


def test_late_checkout_suggestion():
    rules = {"late_hourly_amount": 50000, "grace_minutes": 30}
    due = rps.planned_checkout(_res())
    # O'chiq sozlama — taklif yo'q
    assert rps.late_checkout_suggestion(_res(), {"late_hourly_amount": 0}, due + timedelta(hours=5)) is None
    # Imtiyoz ichida — yo'q
    assert rps.late_checkout_suggestion(_res(), rules, due + timedelta(minutes=25)) is None
    # 2 soat 10 daqiqa kechikdi — 3 soat yaxlitlanadi
    s = rps.late_checkout_suggestion(_res(), rules, due + timedelta(hours=2, minutes=10))
    assert s["hours"] == 3 and s["amount"] == 150000 and s["late_minutes"] == 130
    # Chiqib ketgan: "mehmon chiqmoqda" belgilangan vaqtgacha
    gone = _res(status="CHECKED_OUT", checkout_requested_at=due + timedelta(minutes=45))
    s = rps.late_checkout_suggestion(gone, rules, due + timedelta(days=3))
    assert s["hours"] == 1 and s["amount"] == 50000
    # Chiqib ketgan, vaqti noma'lum — taklif yo'q
    assert rps.late_checkout_suggestion(_res(status="CHECKED_OUT"), rules, due + timedelta(days=1)) is None
    # Hali kirmagan — yo'q
    assert rps.late_checkout_suggestion(_res(status="CONFIRMED"), rules, due + timedelta(days=1)) is None


def test_payment_status_and_void_rights():
    assert rps.payment_status_for(100, 0) == "UNPAID"
    assert rps.payment_status_for(100, 50) == "PARTIALLY_PAID"
    assert rps.payment_status_for(100, 100) == "PAID"
    assert rps.can_void({"user_type": "ADMIN"})
    assert rps.can_void({"user_type": "EMPLOYEE", "permissions": ["shift.force_close"]})
    assert not rps.can_void({"user_type": "EMPLOYEE", "permissions": ["reservation.update"]})


# ---------------------------------------------------------------- servis --
class Rows:
    def __init__(self, items=None, one=None):
        self.items = list(items or [])
        self.one_value = one

    def scalars(self):
        return self

    def all(self):
        return list(self.items)

    def first(self):
        return self.items[0] if self.items else None


class Db:
    def __init__(self, reservation, invoice=None, lines=None, hotel_settings=None):
        self.reservation = reservation
        self.invoice = invoice
        self.lines = list(lines or [])
        self.penalties = {}
        self.added = []
        self.deleted = []
        self.hotel = SimpleNamespace(id=HOTEL, settings=hotel_settings or {})
        self.branch = fake_branch(HOTEL, self.hotel.settings)

    async def get(self, model, key):
        if model is Reservation:
            return self.reservation if key == self.reservation.id else None
        if model is ReservationPenalty:
            return self.penalties.get(key)
        if model is Hotel:
            return self.hotel
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if is_branch_query(stmt):  # sozlamalar filialda
            return BranchResult(self.branch)
        if "FROM invoices" in sql:
            return Rows([self.invoice] if self.invoice else [])
        if "FROM invoice_line_items" in sql:
            if sql.startswith("SELECT invoice_line_items.reference_id"):
                return Rows([l.reference_id for l in self.lines if l.line_type == "PENALTY"])
            return Rows([l for l in self.lines if l.line_type == "PENALTY"])
        if "FROM reservation_penalties" in sql:
            return Rows([p for p in self.penalties.values() if p.voided_at is None])
        if "FROM users" in sql:
            return Rows([SimpleNamespace(id=USER, first_name="Dilnoza", last_name="R")])
        raise AssertionError(sql)

    def add(self, obj):
        if isinstance(obj, ReservationPenalty):
            obj.id = obj.id or uuid.uuid4()
            self.penalties[obj.id] = obj
        if isinstance(obj, InvoiceLineItem):
            self.lines.append(obj)
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)
        self.lines.remove(obj)

    async def flush(self):
        return None


def service(db):
    return rps.ReservationPenaltyService(db)


def invoice(total=600000, paid=600000, status="PAID"):
    return SimpleNamespace(id=uuid.uuid4(), hotel_id=HOTEL, total_amount=total, paid_amount=paid, status=status)


def test_add_penalty_updates_booking_and_invoice():
    res = _res()
    inv = invoice()
    db = Db(res, inv)
    out = asyncio.run(service(db).add(HOTEL, res.id, "damage", 150000.4, "  Stakan sindi ", USER))
    assert out["penalty"]["kind"] == "DAMAGE" and out["penalty"]["amount"] == 150000
    assert out["penalty"]["note"] == "Stakan sindi"
    assert res.penalty_amount == 150000 and res.total_amount == 750000
    assert res.payment_status == "PARTIALLY_PAID"  # endi qarz bor
    assert inv.total_amount == 750000 and inv.status == "PARTIALLY_PAID"
    line = db.lines[0]
    assert line.line_type == "PENALTY" and float(line.total_price) == 150000
    assert line.reference_id == out["penalty"]["id"] or str(line.reference_id) == out["penalty"]["id"]


def test_add_penalty_without_invoice_and_after_checkout():
    res = _res(status="CHECKED_OUT", total_amount=600000, paid_amount=0, payment_status="UNPAID")
    db = Db(res)
    asyncio.run(service(db).add(HOTEL, res.id, "LATE_CHECKOUT", 50000, None, USER))
    assert res.total_amount == 650000 and res.payment_status == "UNPAID"
    assert db.lines == []  # hisob-faktura yo'q — keyin ensure_penalty_lines qo'shadi


def test_add_penalty_validation():
    for status in ("CONFIRMED", "PENDING", "CANCELLED", "NO_SHOW"):
        res = _res(status=status)
        with pytest.raises(ValidationException) as err:
            asyncio.run(service(Db(res)).add(HOTEL, res.id, "DAMAGE", 1000, None, USER))
        assert err.value.error_code == "PENALTY_INVALID_STATUS"
    res = _res()
    with pytest.raises(ValidationException):
        asyncio.run(service(Db(res)).add(HOTEL, res.id, "FUN", 1000, None, USER))
    with pytest.raises(ValidationException):
        asyncio.run(service(Db(res)).add(HOTEL, res.id, "DAMAGE", 0.2, None, USER))
    # Boshqa mehmonxonaning broni
    with pytest.raises(Exception):
        asyncio.run(service(Db(res)).add(uuid.uuid4(), res.id, "DAMAGE", 1000, None, USER))


def test_void_reverts_totals_and_line():
    res = _res()
    inv = invoice()
    db = Db(res, inv)
    added = asyncio.run(service(db).add(HOTEL, res.id, "DAMAGE", 150000, None, USER))
    pid = uuid.UUID(added["penalty"]["id"])
    reception = {"id": USER, "user_type": "EMPLOYEE", "permissions": ["reservation.update"]}
    with pytest.raises(ForbiddenException):
        asyncio.run(service(db).void(HOTEL, res.id, pid, "xato", reception))
    manager = {"id": USER, "user_type": "EMPLOYEE", "permissions": ["shift.force_close"]}
    out = asyncio.run(service(db).void(HOTEL, res.id, pid, " xato kiritildi ", manager))
    assert out["penalty"]["voided_at"] is not None and out["penalty"]["void_reason"] == "xato kiritildi"
    assert res.penalty_amount == 0 and res.total_amount == 600000 and res.payment_status == "PAID"
    assert inv.total_amount == 600000 and inv.status == "PAID"
    assert db.lines == []
    with pytest.raises(ValidationException):
        asyncio.run(service(db).void(HOTEL, res.id, pid, None, manager))


def test_ensure_penalty_lines_adds_only_missing():
    res = _res(penalty_amount=80000)
    p1 = ReservationPenalty(id=uuid.uuid4(), hotel_id=HOTEL, reservation_id=res.id, kind="DAMAGE",
                            amount=30000, note=None, penalty_date=date.today(), created_by=USER)
    p2 = ReservationPenalty(id=uuid.uuid4(), hotel_id=HOTEL, reservation_id=res.id, kind="LATE_CHECKOUT",
                            amount=50000, note="2 soat", penalty_date=date.today(), created_by=USER)
    inv = invoice()
    existing = SimpleNamespace(line_type="PENALTY", reference_id=p1.id)
    db = Db(res, inv, lines=[existing])
    db.penalties = {p1.id: p1, p2.id: p2}
    p1.voided_at = None
    p2.voided_at = None
    asyncio.run(rps.ensure_penalty_lines(db, inv, res))
    new = [l for l in db.lines if isinstance(l, InvoiceLineItem)]
    assert len(new) == 1 and new[0].reference_id == p2.id
    assert new[0].description.startswith("Jarima: Kech chiqish")
    # Jarimasiz bron — hech qanday so'rov yo'q
    class NoQuery:
        async def execute(self, *_):
            raise AssertionError("so'rov kutilmagan")
    asyncio.run(rps.ensure_penalty_lines(NoQuery(), inv, _res(penalty_amount=0)))


def test_list_includes_suggestion_and_totals():
    res = _res(penalty_amount=50000)
    db = Db(res, hotel_settings={"penalty": {"late_hourly_amount": 40000, "grace_minutes": 0}})
    # Chiqish vaqti allaqachon o'tgan bron — taklif bo'ladi
    res.check_out_date = date.today() - timedelta(days=1)
    out = asyncio.run(service(db).list_for_reservation(HOTEL, res.id))
    assert out["total"] == 50000 and out["can_add"] is True
    assert out["late_suggestion"]["amount"] >= 40000


# ------------------------------------------ qayta hisoblarda saqlanadi --
def test_recomputations_include_penalties():
    """Chiqish, ko'chirish va hisob-faktura yaratish jarima summasini
    jamiga qo'shadi va PENALTY qatorlarini ta'minlaydi."""
    import inspect

    from app.application.services.finance_service import FinanceService
    from app.application.services.reservation_service import ReservationService

    check_out = inspect.getsource(ReservationService.check_out)
    assert "penalty_amount" in check_out and "ensure_penalty_lines" in check_out
    move = inspect.getsource(ReservationService.move_room)
    assert "penalty_total" in move
    settle = inspect.getsource(ReservationService.settle_payment)
    assert "penalty_total" in settle and "ensure_penalty_lines" in settle
    create = inspect.getsource(FinanceService.create_invoice)
    assert "penalty_amount" in create and "ensure_penalty_lines" in create


def test_move_room_keeps_penalty_in_total():
    from tests.test_move_discount_policy import NEW_ROOM, make_reservation, make_service, move

    res = make_reservation(penalty_amount=70000, total_amount=270000)
    svc = make_service(res)
    move(svc, res, NEW_ROOM)
    # 300k (yangi xona) + 70k jarima
    assert res.total_amount == 370000


def test_checkout_keeps_penalty_in_total():
    from tests.test_move_discount_policy import (
        CheckoutRepo,
        NEW_ROOM,
        make_reservation,
        make_service,
    )

    class CheckoutDb:
        def __init__(self):
            self.added = []

        async def execute(self, stmt):
            sql = str(stmt)
            if "FROM room_types" in sql:
                return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(base_price=150000))
            if "FROM reservation_penalties" in sql:
                return Rows([penalty])
            if "FROM invoice_line_items" in sql:
                return Rows([])
            raise AssertionError(sql)

        async def get(self, *_):
            return None

        def add(self, obj):
            self.added.append(obj)

        async def flush(self):
            return None

    invoice_obj = SimpleNamespace(
        id=uuid.uuid4(), hotel_id=HOTEL, subtotal=300000, discount_amount=0, tax_amount=0,
        total_amount=300000, paid_amount=300000, status="PAID", invoice_number="INV-9",
    )
    res = make_reservation(
        room_id=NEW_ROOM, status="CHECKED_IN", penalty_amount=50000, total_amount=350000,
        paid_amount=300000, reservation_number="R-9",
    )
    penalty = ReservationPenalty(id=uuid.uuid4(), hotel_id=HOTEL, reservation_id=res.id, kind="DAMAGE",
                                 amount=50000, note=None, penalty_date=date.today(), created_by=USER)
    penalty.voided_at = None
    svc = make_service(res, invoice=invoice_obj, lines=[SimpleNamespace(line_type="ROOM_CHARGE", total_price=300000)])
    db = CheckoutDb()
    svc.session = db
    svc.repo = CheckoutRepo(res)

    async def hotel_code(hotel_id):
        return "H"

    svc._get_hotel_code = hotel_code
    result = asyncio.run(
        svc.check_out(res.id, HOTEL, USER, transition_room=False, create_cleaning_task=False)
    )
    assert result["total_amount"] == 350000
    assert res.total_amount == 350000
    assert res.payment_status == "PARTIALLY_PAID"
    assert any(getattr(o, "line_type", None) == "PENALTY" for o in db.added)
