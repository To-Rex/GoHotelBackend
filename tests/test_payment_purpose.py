#!/usr/bin/env python3
"""Moliya → To'lovlar: har to'lov NIMA UCHUN olingani.

Ishga tushirish: pytest tests/test_payment_purpose.py

Tekshiriladi:
  1. Taqsimot (FIFO): xona avval, jarima keyin; chegirma xona haqidan;
     ortiqcha va qaytarim; keyin yozilgan jarimaga oldingi to'lov tegmaydi.
  2. Qarz to'lovi belgisi — chiqish kunidan KEYINGI kunlarda.
  3. Ro'yxat so'rovi PostgreSQL da tuziladi: bron va do'kon tarmoqlari
     bitta UNION ostida, filial sharti ikkala tarmoqda, qidiruv xona/mehmon/
     mahsulot bo'yicha, saralash faqat yopiq ro'yxatdan.
"""
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.application.services import payment_purpose as pp  # noqa: E402
from app.application.services.finance_service import FinanceService  # noqa: E402
from app.infrastructure.tenant.branch_scope import SCOPE_KEY, BranchScope  # noqa: E402

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)


def t(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def room(amount, at=0, label="Room charge: 12"):
    return pp.Charge(pp.KIND_ROOM, amount, t(at), label)


def penalty(amount, at, label="Jarima: Kech chiqish"):
    return pp.Charge(pp.KIND_PENALTY, amount, t(at), label)


def kinds(parts):
    return [(p["kind"], p["amount"]) for p in parts]


# --- 1. Taqsimot ------------------------------------------------------------


def test_room_then_penalty_paid_separately():
    """Prod namunasi: xona 300 000 (chegirma 50 000) → 250 000; jarima 50 000 → 50 000."""
    alloc = pp.allocate(
        [room(300_000, 0), penalty(50_000, 30)],
        [pp.Paid("a", 250_000, t(1)), pp.Paid("b", 50_000, t(31))],
        discount=50_000,
    )
    assert kinds(alloc["a"]) == [(pp.KIND_ROOM, 250_000)]
    assert kinds(alloc["b"]) == [(pp.KIND_PENALTY, 50_000)]
    assert alloc["b"][0]["label"] == "Jarima: Kech chiqish"


def test_one_payment_covers_room_balance_and_penalty():
    alloc = pp.allocate(
        [room(200_000, 0), penalty(100_000, 10)],
        [pp.Paid("a", 100_000, t(1)), pp.Paid("b", 200_000, t(20))],
    )
    assert kinds(alloc["a"]) == [(pp.KIND_ROOM, 100_000)]
    assert kinds(alloc["b"]) == [(pp.KIND_ROOM, 100_000), (pp.KIND_PENALTY, 100_000)]
    assert pp.summary_kind(alloc["b"], 200_000) == pp.KIND_MIXED
    assert pp.summary_kind(alloc["a"], 100_000) == pp.KIND_ROOM


def test_overpayment_is_extra_and_refund_is_refund():
    alloc = pp.allocate(
        [room(100_000, 0)],
        [pp.Paid("a", 130_000, t(1)), pp.Paid("r", -30_000, t(5))],
    )
    assert kinds(alloc["a"]) == [(pp.KIND_ROOM, 100_000), (pp.KIND_EXTRA, 30_000)]
    assert kinds(alloc["r"]) == [(pp.KIND_REFUND, 30_000)]
    assert pp.summary_kind(alloc["r"], -30_000) == pp.KIND_REFUND


def test_earlier_payment_does_not_touch_later_penalty():
    """To'lov o'sha paytda mavjud haqlarni yopadi: keyin yozilgan jarima
    uni "yutib" yubormaydi — ortiqcha qism ORTIQCHA bo'lib qoladi."""
    alloc = pp.allocate(
        [room(100_000, 0), penalty(50_000, 60)],
        [pp.Paid("a", 150_000, t(1))],
    )
    assert kinds(alloc["a"]) == [(pp.KIND_ROOM, 100_000), (pp.KIND_EXTRA, 50_000)]


def test_same_instant_charge_before_payment_and_unknown_time_first():
    alloc = pp.allocate(
        [pp.Charge(pp.KIND_ROOM, 80_000, None), penalty(20_000, 1)],
        [pp.Paid("a", 80_000, t(0)), pp.Paid("b", 20_000, t(1))],
    )
    assert kinds(alloc["a"]) == [(pp.KIND_ROOM, 80_000)]
    assert kinds(alloc["b"]) == [(pp.KIND_PENALTY, 20_000)]


def test_two_penalties_keep_their_labels():
    alloc = pp.allocate(
        [room(100_000, 0), penalty(30_000, 5, "Jarima: Kech chiqish"), penalty(20_000, 6, "Jarima: Shikast")],
        [pp.Paid("a", 100_000, t(1)), pp.Paid("b", 50_000, t(10))],
    )
    assert [(p["kind"], p["amount"], p["label"]) for p in alloc["b"]] == [
        (pp.KIND_PENALTY, 30_000, "Jarima: Kech chiqish"),
        (pp.KIND_PENALTY, 20_000, "Jarima: Shikast"),
    ]
    assert pp.summary_kind(alloc["b"], 50_000) == pp.KIND_PENALTY


def test_rounding_noise_is_not_extra():
    alloc = pp.allocate([room(100_000.004, 0)], [pp.Paid("a", 100_000, t(1))])
    assert kinds(alloc["a"]) == [(pp.KIND_ROOM, 100_000)]


def test_line_kind_and_shop_summary():
    assert pp.line_kind("ROOM_CHARGE") == pp.KIND_ROOM
    assert pp.line_kind("penalty") == pp.KIND_PENALTY
    assert pp.line_kind("SERVICE") == pp.KIND_SERVICE
    assert pp.line_kind("weird") == pp.KIND_OTHER
    assert pp.line_kind(None) == pp.KIND_OTHER
    assert pp.summary_kind([], 10, pp.SOURCE_SHOP) == pp.KIND_SHOP
    assert pp.summary_kind([], 10) is None


# --- 2. Qarz to'lovi --------------------------------------------------------


def test_after_checkout_only_on_later_local_days():
    checkout = date(2026, 11, 2)
    # Chiqish kunining o'zida (mahalliy 23:30 = UTC 18:30) — oddiy to'lov
    same_day = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)
    assert pp.is_after_checkout(same_day, "CHECKED_OUT", checkout) is False
    # Mahalliy 3-noyabr 00:30 (UTC 2-noyabr 19:30) — keyingi kun: qarz
    next_day = datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc)
    assert pp.is_after_checkout(next_day, "CHECKED_OUT", checkout) is True
    # Hali chiqmagan bron — qarz emas
    assert pp.is_after_checkout(next_day, "CHECKED_IN", checkout) is False
    # Soatlik bron: chiqish kuni `check_out_datetime` ning mahalliy sanasi
    hourly_end = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)  # mahalliy 3-noyabr 01:00
    assert pp.is_after_checkout(next_day, "CHECKED_OUT", checkout, hourly_end) is False
    later = datetime(2026, 11, 3, 19, 30, tzinfo=timezone.utc)  # mahalliy 4-noyabr
    assert pp.is_after_checkout(later, "CHECKED_OUT", checkout, hourly_end) is True
    assert pp.is_after_checkout(None, "CHECKED_OUT", checkout) is False
    assert pp.is_after_checkout(later, "CHECKED_OUT", None) is False


# --- 3. Ro'yxat so'rovi ------------------------------------------------------

HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()
DIALECT = postgresql.dialect()


def svc(scoped: bool = False) -> FinanceService:
    s = FinanceService.__new__(FinanceService)
    info = {SCOPE_KEY: BranchScope(HOTEL, BRANCH)} if scoped else {}
    s.session = SimpleNamespace(info=info)
    return s


def sql(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=DIALECT)).split())


def test_feed_reservation_only_compiles_and_sorts_by_created_at():
    ids, count = svc()._feed_statements(HOTEL, date(2026, 11, 1), date(2026, 11, 3), None, None, None, False)
    text = sql(ids)
    assert "FROM payments" in text
    assert "shop_sale_payments" not in text
    assert "payments.payment_date >=" in text and "payments.payment_date <=" in text
    assert "ORDER BY feed.created_at DESC, feed.created_at DESC, feed.id" in text
    assert "count(*)" in sql(count)


def test_feed_with_shop_is_a_union_with_local_day_and_bounds():
    ids, count = svc()._feed_statements(HOTEL, date(2026, 11, 2), date(2026, 11, 2), None, "amount", "asc", True)
    text = sql(ids)
    assert "UNION ALL" in text
    assert "FROM shop_sale_payments" in text
    # Do'kon kuni — UTC + mahalliy siljish, sessiya zonasiga bog'liq emas
    assert "CAST(timezone('UTC', shop_sale_payments.paid_at) + interval '" in text
    assert "minutes' AS DATE) AS pay_date" in text
    assert "shop_sale_payments.paid_at >=" in text and "shop_sale_payments.paid_at <" in text
    assert "ORDER BY feed.amount ASC, feed.created_at DESC, feed.id" in text
    assert "UNION ALL" in sql(count)


def test_feed_search_covers_room_guest_product_and_notes():
    ids, _ = svc()._feed_statements(HOTEL, None, None, "12", None, None, True)
    text = sql(ids)
    for piece in (
        "payments.payment_number ILIKE",
        "payments.notes ILIKE",
        "reservations.reservation_number ILIKE",
        "rooms.room_number ILIKE",
        "guests.first_name ILIKE",
        "guests.first_name || ",
        "shop_sale_items.product_name ILIKE",
    ):
        assert piece in text, piece


def test_feed_branch_scope_is_explicit_in_both_arms():
    ids, count = svc(scoped=True)._feed_statements(HOTEL, None, None, None, None, None, True)
    for text in (sql(ids), sql(count)):
        assert "payments.branch_id =" in text
        assert "shop_sale_payments.branch_id =" in text
    unscoped = sql(svc()._feed_statements(HOTEL, None, None, None, None, None, True)[0])
    assert "branch_id =" not in unscoped


def test_feed_unknown_sort_falls_back():
    ids, _ = svc()._feed_statements(HOTEL, None, None, None, "payments.id; DROP TABLE", "asc", False)
    assert "ORDER BY feed.created_at ASC" in sql(ids)
    ids, _ = svc()._feed_statements(HOTEL, None, None, None, "payment_number", "desc", True)
    assert "ORDER BY feed.number DESC" in sql(ids)
