"""Mehmon qarzi: ko'rinsin, sababi aniq bo'lsin, unutilmasin.

Foydalanuvchi talabi: imkon qadar mijozlar bilan qarz qolib ketmasin —
qarzlar ko'rinib tursin, tez-tez eslatilsin (hamma qismida) va NIMA UCHUN
qarzligi aniq ko'rsatilsin. Mavjud funksiyalar 100% saqlansin.
"""
import asyncio
import inspect
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.services import debt_reminder_service as reminders
from app.application.services import debt_service as ds
from app.application.services.debtor_service import DebtorService
from app.application.services.reservation_service import ReservationService
from app.core.exceptions import ConflictException, ValidationException
from app.presentation.api.v1 import reservations as reservations_api

HOTEL = uuid.uuid4()
CASHIER = uuid.uuid4()
TODAY = date(2026, 10, 8)


def res(status="CHECKED_OUT", total=0, paid=0, penalty=0, **kw):
    base = dict(
        id=uuid.uuid4(), hotel_id=HOTEL, room_id=uuid.uuid4(), guest_id=uuid.uuid4(),
        status=status, total_amount=Decimal(str(total)), paid_amount=Decimal(str(paid)),
        penalty_amount=Decimal(str(penalty)), booking_type="DAILY",
        check_in_date=TODAY - timedelta(days=2), check_out_date=TODAY,
        check_in_datetime=None, check_out_datetime=None, discount_amount=0, discount_percent=0,
        move_discount_amount=0, room_moves=None, daily_unit="12h",
        debt_ack_at=None, debt_ack_by=None, debt_ack_note=None, debt_ack_amount=None,
        reservation_number="RES-1", created_by=CASHIER,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def penalty(amount, kind="LATE_CHECKOUT", note="1 soat"):
    return SimpleNamespace(amount=Decimal(str(amount)), kind=kind, note=note, penalty_date=TODAY,
                           created_at=datetime(2026, 10, 8, tzinfo=timezone.utc))


def build(r, *, penalties=(), services=(), shop=(), projected=None):
    service = ds.DebtService(session=None)

    async def fake_projection(_res, _price):
        return projected

    service._projected_room = fake_projection  # type: ignore[method-assign]
    return asyncio.run(service._build(r, ("101", 250000.0), list(penalties), list(services),
                                      list(shop), TODAY))


# ------------------------------------------------------------- sabablar --
def test_payments_cover_the_room_first_so_the_penalty_is_the_reason():
    r = res(total=550000, paid=500000, penalty=50000)
    debt = build(r, penalties=[penalty(50000)])
    assert debt["reservation_debt"] == 50000
    assert [(i["kind"], i["amount"]) for i in debt["items"]] == [("penalty", 50000)]
    assert debt["items"][0]["penalty_kind"] == "LATE_CHECKOUT"
    assert ds.reasons_text(debt["items"]) == "Jarima (kech chiqish, 1 soat) 50 000"


def test_a_partly_paid_room_shows_what_is_left_of_it():
    r = res(total=500000, paid=300000)
    debt = build(r)
    assert [(i["kind"], i["amount"], i["charged"]) for i in debt["items"]] == [("room", 200000, 500000)]
    assert debt["items"][0]["room_number"] == "101"


def test_an_extended_stay_is_a_debt_before_the_guest_leaves():
    # 1 kecha to'langan, 2 kechaga uzaytirilgan — farq chiqishda qo'shiladi
    r = res(status="CHECKED_IN", total=250000, paid=250000)
    debt = build(r, projected={"net": 500000.0, "duration": 2, "unit_price": 250000.0, "discount": 0})
    assert debt["projected"] is True and debt["expected_total"] == 500000
    assert [(i["kind"], i["amount"]) for i in debt["items"]] == [("extension", 250000)]
    assert debt["reservation_debt"] == 250000


def test_a_shortened_stay_lowers_the_expected_total():
    r = res(status="CHECKED_IN", total=500000, paid=500000)
    debt = build(r, projected={"net": 250000.0, "duration": 1, "unit_price": 250000.0, "discount": 0})
    assert debt["expected_total"] == 250000 and debt["total_debt"] == 0 and debt["items"] == []


def test_shop_sales_on_the_booking_are_part_of_the_guest_debt():
    sale = {"sale_id": uuid.uuid4(), "total": 30000, "paid": 10000, "remaining": 20000,
            "products": "Cola ×2", "created_at": None, "created_by_name": "Dilnoza"}
    r = res(total=500000, paid=500000)
    debt = build(r, shop=[sale])
    assert debt["reservation_debt"] == 0 and debt["shop_debt"] == 20000 and debt["total_debt"] == 20000
    assert debt["items"][0]["kind"] == "shop" and debt["items"][0]["products"] == "Cola ×2"
    # Kelmagan (tasdiqlangan) bronda faqat do'kon qarzi
    confirmed = build(res(status="CONFIRMED", total=900000, paid=0), shop=[sale])
    assert confirmed["reservation_debt"] == 0 and confirmed["total_debt"] == 20000


def test_unexplained_penalty_total_still_shows_up_as_a_reason():
    r = res(total=560000, paid=500000, penalty=60000)
    debt = build(r, penalties=[penalty(50000)])
    kinds = [(i["kind"], i["amount"], i.get("penalty_kind")) for i in debt["items"]]
    assert kinds == [("penalty", 50000, "LATE_CHECKOUT"), ("penalty", 10000, "OTHER")]


def test_unbilled_services_are_charged_as_checkout_will():
    svc = {"id": uuid.uuid4(), "name": "Kir yuvish", "quantity": 1, "total": 40000,
           "date": TODAY, "billed": False}
    r = res(status="CHECKED_IN", total=250000, paid=250000)
    debt = build(r, services=[svc], projected={"net": 250000.0, "duration": 1,
                                                "unit_price": 250000.0, "discount": 0})
    assert [(i["kind"], i["amount"], i["name"]) for i in debt["items"]] == [("service", 40000, "Kir yuvish")]


def test_overdue_days_and_acknowledgement_are_reported():
    r = res(total=300000, paid=100000, check_out_date=TODAY - timedelta(days=5),
            debt_ack_at=datetime(2026, 10, 3, tzinfo=timezone.utc), debt_ack_by=CASHIER,
            debt_ack_note="ertaga olib keladi", debt_ack_amount=Decimal("200000"))
    debt = build(r)
    assert debt["overdue_days"] == 5
    assert debt["acknowledged"]["note"] == "ertaga olib keladi"
    assert debt["acknowledged"]["amount"] == 200000


def test_allocate_is_chronological_and_ignores_negatives():
    charges = [ds.Charge("room", 100), ds.Charge("penalty", -5), ds.Charge("penalty", 50)]
    ds.allocate(charges, 120)
    assert [c.unpaid for c in charges] == [0, 0, 30]
    assert ds.fmt_sum(1234567.4) == "1 234 567"


# ---------------------------------------------------- chiqishdagi to'siq --
def gate(r, debt, **kw):
    service = ReservationService(session=None)

    class FakeDebts:
        def __init__(self, _session):
            pass

        async def breakdown(self, _res, today=None):
            return debt

    original = ds.DebtService
    ds.DebtService = FakeDebts  # type: ignore[misc]
    try:
        return asyncio.run(service._checkout_debt_gate(
            r, CASHIER, kw.get("acknowledge", False), kw.get("note"), kw.get("enforce", True),
        ))
    finally:
        ds.DebtService = original  # type: ignore[misc]


DEBT = {"total_debt": 50000.0, "items": [{"kind": "penalty", "amount": 50000, "penalty_kind": "DAMAGE", "note": "stakan"}]}


def test_reception_cannot_check_out_a_debtor_silently():
    with pytest.raises(ConflictException) as err:
        gate(res(status="CHECKED_IN"), DEBT)
    assert err.value.error_code == "CHECKOUT_DEBT"
    assert "50 000" in err.value.detail and "Jarima (shikast, stakan)" in err.value.detail


def test_checking_out_with_debt_needs_a_reason_and_is_recorded():
    r = res(status="CHECKED_IN")
    with pytest.raises(ValidationException) as err:
        gate(r, DEBT, acknowledge=True, note=" ")
    assert err.value.error_code == "DEBT_NOTE_REQUIRED"
    event, _ = gate(r, DEBT, acknowledge=True, note="Ertaga to'laydi")
    assert event == "acknowledged"
    assert r.debt_ack_by == CASHIER and r.debt_ack_note == "Ertaga to'laydi"
    assert float(r.debt_ack_amount) == 50000 and r.debt_ack_at is not None
    # Qayta bosilsa (qarz o'sha) — yana so'ralmaydi
    assert gate(r, DEBT)[0] is None


def test_the_cleaner_is_never_blocked_but_cashiers_are_told():
    assert gate(res(status="CHECKED_IN"), DEBT, enforce=False)[0] == "cleaner"
    assert gate(res(status="CHECKED_IN"), {"total_debt": 0.0, "items": []})[0] is None


def test_only_the_cleaner_button_skips_the_check():
    src = inspect.getsource(reservations_api.request_checkout)
    assert "enforce_debt=not self_assign" in src
    assert "acknowledge_debt=body.acknowledge_debt" in src


# --------------------------------------------------------- qarzdorlar --
class DebtorsDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return SimpleNamespace(all=lambda: self.rows)


class _Row(tuple):
    def __new__(cls, r, first, last, room):
        obj = super().__new__(cls, (r,))
        obj.first_name, obj.last_name, obj.phone = first, last, "+998"
        obj.room_number, obj.branch_name = room, "Markaz"
        obj.creator_first, obj.creator_last = "Dilnoza", "R"
        return obj


def test_debtors_list_explains_every_debt_and_skips_settled_guests(monkeypatch):
    paid_in = res(status="CHECKED_IN", total=250000, paid=250000)
    owes = res(status="CHECKED_OUT", total=550000, paid=500000, penalty=50000)
    breakdowns = {
        paid_in.id: {"total_debt": 0.0, "reservation_debt": 0.0, "shop_debt": 0.0, "items": [],
                     "expected_total": 250000, "projected": False, "overdue_days": None, "acknowledged": None},
        owes.id: {"total_debt": 70000.0, "reservation_debt": 50000.0, "shop_debt": 20000.0,
                  "items": [{"kind": "penalty", "amount": 50000, "penalty_kind": "LATE_CHECKOUT", "note": None},
                            {"kind": "shop", "amount": 20000, "products": "Cola ×2"}],
                  "expected_total": 550000, "projected": False, "overdue_days": 2, "acknowledged": None},
    }

    async def fake_breakdowns(self, reservations, today=None):
        return {r.id: breakdowns[r.id] for r in reservations}

    async def fake_shop_ids(self, hotel_id):
        return set()

    monkeypatch.setattr(ds.DebtService, "breakdowns", fake_breakdowns)
    monkeypatch.setattr(ds.DebtService, "shop_reservation_ids", fake_shop_ids)
    out = asyncio.run(DebtorService(DebtorsDb([_Row(paid_in, "A", "B", "102"), _Row(owes, "Ali", "Valiyev", "101")])).list_debtors(HOTEL))
    assert out["summary"] == {"count": 1, "guests": 1, "total_debt": 70000, "reservation_debt": 50000, "shop_debt": 20000}
    item = out["items"][0]
    assert item["debt_amount"] == 70000 and item["shop_debt"] == 20000
    assert item["reason_text"] == "Jarima (kech chiqish) 50 000 · Do'kon: Cola ×2 20 000"
    assert item["overdue_days"] == 2 and item["reasons"][1]["kind"] == "shop"
    # Eski maydonlar joyida
    assert item["total_amount"] == 550000 and item["paid_amount"] == 500000
    assert out["guests"][0]["debt_amount"] == 70000


# -------------------------------------------------------- eslatmalar --
def test_settings_defaults_and_validation():
    assert reminders.resolve_debt_settings(None) == {"enabled": True, "interval_minutes": 120}
    assert reminders.resolve_debt_settings({"debt_reminders": {"enabled": False, "interval_minutes": 30}}) == {
        "enabled": False, "interval_minutes": 30}
    assert reminders.resolve_debt_settings({"debt_reminders": {"interval_minutes": 7}})["interval_minutes"] == 120


class Sent(list):
    async def __call__(self, _session, hotel_id, targets, title, body, entity_type, entity_id):
        self.append((title, body, entity_type, entity_id, set(targets)))
        return len(targets)


def reminder_service(monkeypatch, rows, debts, already=()):
    service = reminders.DebtReminderService(session=SimpleNamespace(
        execute=lambda _s: _result(rows)))
    sent = Sent()
    monkeypatch.setattr(reminders, "_send", sent)

    async def fake_already(_session, _hotel, entity_type, entity_id=None, since=None):
        return (entity_type, entity_id) in already

    async def fake_names(_session, r):
        return "Ali Valiyev", "101"

    async def fake_breakdowns(reservations, today=None):
        return {r.id: debts[r.id] for r in reservations}

    async def fake_breakdown(r, today=None):
        return debts[r.id]

    async def fake_shop_ids(_hotel):
        return set()

    monkeypatch.setattr(reminders, "_already_sent", fake_already)
    monkeypatch.setattr(reminders, "_guest_and_room", fake_names)
    service.debts.breakdowns = fake_breakdowns  # type: ignore[method-assign]
    service.debts.breakdown = fake_breakdown  # type: ignore[method-assign]
    service.debts.shop_reservation_ids = fake_shop_ids  # type: ignore[method-assign]
    return service, sent


async def _async(value):
    return value


def _result(rows):
    return _async(SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows)))


OWES = {"total_debt": 50000.0, "items": [{"kind": "penalty", "amount": 50000, "penalty_kind": "DAMAGE", "note": None}]}


def test_departing_debtors_are_announced_once(monkeypatch):
    now = datetime(2026, 10, 8, 10, 0)
    daily = res(status="CHECKED_IN")
    hourly_later = res(status="CHECKED_IN", booking_type="HOURLY", check_out_datetime=now + timedelta(hours=3))
    hourly_soon = res(status="CHECKED_IN", booking_type="HOURLY", check_out_datetime=now + timedelta(minutes=40))
    debts = {r.id: OWES for r in (daily, hourly_later, hourly_soon)}
    service, sent = reminder_service(monkeypatch, [daily, hourly_later, hourly_soon], debts)
    assert asyncio.run(service._soon(HOTEL, {CASHIER}, now)) == 2
    assert {e[3] for e in sent} == {daily.id, hourly_soon.id}
    assert sent[0][0] == "Bugun chiqadi — qarzi bor" and "Jarima (shikast) 50 000" in sent[0][1]
    # Allaqachon aytilgan — takrorlanmaydi
    service2, sent2 = reminder_service(monkeypatch, [daily], debts, already={(reminders.TYPE_SOON, daily.id)})
    assert asyncio.run(service2._soon(HOTEL, {CASHIER}, now)) == 0 and sent2 == []
    # Tunda bezovta qilinmaydi
    assert asyncio.run(service._soon(HOTEL, {CASHIER}, datetime(2026, 10, 8, 6, 0))) == 0


def test_a_guest_who_left_with_debt_is_reported(monkeypatch):
    left = res(status="CHECKED_OUT", total=300000, paid=250000)
    service, sent = reminder_service(monkeypatch, [left], {left.id: OWES})
    assert asyncio.run(service._left(HOTEL, {CASHIER}, datetime(2026, 10, 8, 12, 0))) == 1
    assert sent[0][0] == "Mehmon qarz bilan chiqib ketdi" and sent[0][2] == reminders.TYPE_LEFT


def test_the_digest_lists_the_biggest_debts_with_reasons(monkeypatch):
    service, sent = reminder_service(monkeypatch, [], {})
    report = {
        "summary": {"count": 4, "total_debt": 400000},
        "items": [
            {"guest_name": "A", "room_number": "1", "debt_amount": 50000, "reasons": []},
            {"guest_name": "Ali", "room_number": "101", "debt_amount": 200000,
             "reasons": [{"kind": "penalty", "penalty_kind": "LATE_CHECKOUT", "note": None, "amount": 1}]},
            {"guest_name": "B", "room_number": "2", "debt_amount": 100000,
             "reasons": [{"kind": "shop", "products": "Cola ×1", "amount": 1}]},
            {"guest_name": "C", "room_number": None, "debt_amount": 50000, "reasons": []},
        ],
    }

    async def fake_list(self, hotel_id, today=None, **_kw):
        return report

    monkeypatch.setattr(DebtorService, "list_debtors", fake_list)
    assert asyncio.run(service._digest(HOTEL, {CASHIER}, datetime(2026, 10, 8, 15, 0), 120)) == 1
    title, body = sent[0][0], sent[0][1]
    assert title == "Qarzdorlar: 4 ta · 400 000 so'm"
    assert body.startswith("Ali (101-xona): 200 000 — Jarima (kech chiqish); B (2-xona): 100 000 — Do'kon: Cola ×1")
    assert body.endswith("yana 1 ta")
    # Kechasi — yo'q
    assert asyncio.run(service._digest(HOTEL, {CASHIER}, datetime(2026, 10, 8, 23, 0), 120)) == 0


def test_checkout_with_debt_tells_everyone_but_the_person_who_did_it(monkeypatch):
    r = res(status="CHECKED_IN", debt_ack_note="ertaga keladi")
    sent = Sent()
    monkeypatch.setattr(reminders, "_send", sent)

    async def fake_recipients(_session, _hotel):
        return {CASHIER, uuid.uuid4()}

    async def fake_names(_session, _r):
        return "Ali Valiyev", "101"

    class Session:
        async def get(self, _model, _key):
            return SimpleNamespace(first_name="Dilnoza", last_name="R")

    monkeypatch.setattr(reminders, "recipients", fake_recipients)
    monkeypatch.setattr(reminders, "_guest_and_room", fake_names)
    asyncio.run(reminders.notify_checkout_with_debt(Session(), r, OWES, "101", event="acknowledged", actor_id=CASHIER))
    title, body, etype, eid, targets = sent[0]
    assert title == "Mehmon qarz bilan chiqarildi" and etype == reminders.TYPE_ACK
    assert "Sabab: ertaga keladi" in body and "Chiqargan: Dilnoza R" in body
    assert CASHIER not in targets and len(targets) == 1


def test_the_reminder_runs_inside_the_automation_tick():
    from app.application.services.automation_service import AutomationService

    assert "DebtReminderService(self.session).run(now)" in inspect.getsource(AutomationService.run_tick)


def test_debt_settings_writer_checks_the_role():
    from app.presentation.api.v1 import hotels

    assert "assert_can_manage_settings(current_user)" in inspect.getsource(hotels.save_debt_settings)


def test_debt_notifications_have_their_own_type():
    from app.presentation.api.v1.notifications import _map_notification_type

    assert _map_notification_type(SimpleNamespace(entity_type="debt_digest")) == "debt"
    assert _map_notification_type(SimpleNamespace(entity_type="task")) == "newTask"
    assert _map_notification_type(SimpleNamespace(entity_type=None)) == "system"


# ------------------------------------- uzaytirish pulini oldindan olish --
def settle(r, invoice, expected, amount):
    service = ReservationService(session=SimpleNamespace(flush=_noop, refresh=_noop_arg))
    payments = []

    async def get_by_id(_id, _hotel):
        return r

    async def get_invoice(_id, _hotel):
        return invoice

    async def create_payment(**kw):
        payments.append(kw["amount"])
        kw["invoice"].paid_amount = float(kw["invoice"].paid_amount) + kw["amount"]

    class FakeDebts:
        def __init__(self, _session):
            pass

        async def breakdown(self, _res, today=None):
            return {"expected_total": expected}

    service.repo = SimpleNamespace(get_by_id=get_by_id)
    service.invoice_repo = SimpleNamespace(get_by_reservation=get_invoice)
    service._create_payment = create_payment  # type: ignore[method-assign]
    original = ds.DebtService
    ds.DebtService = FakeDebts  # type: ignore[misc]
    try:
        asyncio.run(service.settle_payment(HOTEL, r.id, amount, "CASH", "PAY", CASHIER))
    finally:
        ds.DebtService = original  # type: ignore[misc]
    return payments


async def _noop():
    return None


async def _noop_arg(_obj):
    return None


def test_the_extension_can_be_paid_before_the_guest_leaves():
    r = res(status="CHECKED_IN", total=250000, paid=250000)
    invoice = SimpleNamespace(total_amount=250000, paid_amount=250000)
    assert settle(r, invoice, 500000, 250000) == [250000]
    assert float(r.total_amount) == 500000 and float(invoice.total_amount) == 500000
    assert float(r.paid_amount) == 500000 and r.payment_status == "PAID"


def test_checked_out_totals_are_never_raised_by_a_payment():
    r = res(status="CHECKED_OUT", total=300000, paid=100000)
    invoice = SimpleNamespace(total_amount=300000, paid_amount=100000)
    assert settle(r, invoice, 999999, 200000) == [200000]
    assert float(r.total_amount) == 300000
