"""Moliya → To'lovlar (va hisob-fakturalar) filtrlari va saralashi.

Ishga tushirish: pytest tests/test_finance_filters.py

Nozik joylar:

1. "Turi" filtri ekrandagi belgilar bilan BIR XIL qoida: "Jarima"
   tanlansa aynan "Jarima" belgili qatorlar qoladi (taqsimot FIFO), qaytarim
   faqat "Qaytarim", qarz to'lovi — chiqib ketgandan keyingi kunlarda.
2. Haq turlari bazada yozilmaydi — ular bo'yicha sahifa Pythonda kesiladi;
   jami soni va summasi ham filtrdan o'tganlardan.
3. To'lov turi eski kodlarni ham oladi (CREDIT_CARD → "Bank kartasi").
4. Summa oralig'i modul bo'yicha — qaytarim ham o'z kattaligi bilan.
5. So'rovlar PostgreSQL da tuziladi: kerak bo'lmagan tarmoq (do'kon /
   bron) UNION ga qo'shilmaydi, xodim va xona saralashi bo'sh qiymatni
   oxiriga qo'yadi.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app.application.services import payment_purpose as pp  # noqa: E402
from app.application.services.finance_service import (  # noqa: E402
    FinanceService,
    InvoiceFilters,
    PaymentFeedFilters,
    method_codes,
)
from app.infrastructure.database.models.base import Base  # noqa: E402
from app.infrastructure.database.models.branch import Branch  # noqa: E402
from app.infrastructure.database.models.guest import Guest  # noqa: E402
from app.infrastructure.database.models.hotel import Hotel  # noqa: E402
from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem  # noqa: E402
from app.infrastructure.database.models.payment import Payment  # noqa: E402
from app.infrastructure.database.models.reservation import Reservation  # noqa: E402
from app.infrastructure.database.models.room import Room  # noqa: E402
from app.infrastructure.database.models.user import User  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # faqat testda: SQLite da JSONB yo'q
    return "JSON"


DIALECT = postgresql.dialect()
HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()


def sql(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=DIALECT)).split())


def svc() -> FinanceService:
    s = FinanceService.__new__(FinanceService)
    s.session = SimpleNamespace(info={})
    return s


def feed_sql(*, include_shop=True, sort_by=None, search=None, **filters):
    ids, count = svc()._feed_statements(
        HOTEL, date(2026, 11, 1), date(2026, 11, 3), search, sort_by, "asc",
        include_shop, PaymentFeedFilters(**filters),
    )
    return sql(ids), sql(count)


# --- 1. Qoidalar ------------------------------------------------------------

def test_parse_kinds_and_method_codes():
    assert pp.parse_filter_kinds(" shop, penalty ,nima,, ") == {"SHOP", "PENALTY"}
    assert pp.parse_filter_kinds(None) == frozenset()
    assert method_codes("CARD") == ("CARD", "CREDIT_CARD", "DEBIT_CARD")
    assert method_codes("cash, online") == ("CASH", "ONLINE", "MOBILE_PAYMENT")
    assert method_codes("BANK_TRANSFER,nima") == ("BANK_TRANSFER", "TRANSFER")
    assert method_codes("") == ()


def test_matches_kinds_follows_the_badges():
    room = [{"kind": pp.KIND_ROOM, "amount": 100.0, "label": None}]
    mixed = [*room, {"kind": pp.KIND_PENALTY, "amount": 50.0, "label": None}]

    def m(kinds, **row):
        row.setdefault("source", pp.SOURCE_RESERVATION)
        row.setdefault("amount", 150.0)
        row.setdefault("parts", mixed)
        row.setdefault("after_checkout", False)
        return pp.matches_kinds(frozenset(kinds), **row)

    assert m(set()) is True                                   # filtrsiz — hammasi
    assert m({"PENALTY"}) and m({"ROOM"}) and not m({"SERVICE"})
    assert m({"RESERVATION"}) and not m({"SHOP"})
    # Do'kon to'lovi faqat "Do'kon"
    assert m({"SHOP"}, source=pp.SOURCE_SHOP, parts=[{"kind": "SHOP", "amount": 9.0}])
    assert not m({"RESERVATION", "ROOM"}, source=pp.SOURCE_SHOP)
    # Qaytarim faqat "Qaytarim" — haqlari bo'lsa ham
    assert m({"REFUND"}, amount=-50.0) and not m({"ROOM", "DEBT"}, amount=-50.0, after_checkout=True)
    # Qarz to'lovi: o'zi ham, yopgan haqi bo'yicha ham
    assert m({"DEBT"}, after_checkout=True) and not m({"DEBT"})
    assert m({"ROOM"}, after_checkout=True, parts=room)
    # Nol summali qism belgi bermaydi
    assert not m({"PENALTY"}, parts=[*room, {"kind": "PENALTY", "amount": 0}])


def test_needs_purpose_only_for_badge_kinds():
    assert PaymentFeedFilters(kinds=frozenset({"PENALTY"})).needs_purpose
    assert PaymentFeedFilters(kinds=frozenset({"DEBT", "SHOP"})).needs_purpose
    # Manba va qaytarim — SQL da
    assert not PaymentFeedFilters(kinds=frozenset({"SHOP", "REFUND"})).needs_purpose
    # "Bron to'lovlari" tanlangan bo'lsa haq turi hech narsani toraytirmaydi
    assert not PaymentFeedFilters(kinds=frozenset({"RESERVATION", "PENALTY"})).needs_purpose
    assert not PaymentFeedFilters().needs_purpose


# --- 2. So'rovlar (PostgreSQL) ---------------------------------------------

def test_no_filters_keeps_the_old_query_shape():
    ids, count = feed_sql()
    assert "UNION ALL" in ids and "FROM shop_sale_payments" in ids
    assert "upper(" not in ids and "abs(" not in ids
    # Jami — soni va summasi bitta so'rovda
    assert "count(*)" in count and "sum(feed.amount)" in count


def test_shop_only_and_reservation_only_drop_the_other_arm():
    ids, _ = feed_sql(kinds=frozenset({"SHOP"}))
    assert "UNION" not in ids and "FROM shop_sale_payments" in ids and "FROM payments" not in ids
    ids, _ = feed_sql(kinds=frozenset({"RESERVATION"}))
    assert "UNION" not in ids and "shop_sale_payments" not in ids
    # Do'kon so'ralmagan bo'lsa "faqat do'kon" — bo'sh natija (bron tarmog'i false)
    ids, _ = feed_sql(include_shop=False, kinds=frozenset({"SHOP"}))
    assert "shop_sale_payments" not in ids and "false" in ids.lower()


def test_refund_is_sql_and_badge_kinds_exclude_refunds():
    ids, _ = feed_sql(kinds=frozenset({"REFUND"}))
    assert "payments.amount < " in ids and "shop_sale_payments" not in ids
    ids, _ = feed_sql(kinds=frozenset({"PENALTY"}))
    assert "payments.amount >= " in ids
    ids, _ = feed_sql(kinds=frozenset({"PENALTY", "REFUND"}))
    assert "payments.amount >= " not in ids and "payments.amount < " not in ids


def test_method_staff_amount_and_edited_conditions():
    ids, count = feed_sql(
        methods=("CARD", "CREDIT_CARD"), created_by=uuid.uuid4(), min_amount=1000, max_amount=5000
    )
    for text in (ids, count):
        assert "upper(payments.payment_method) IN" in text
        assert "upper(shop_sale_payments.payment_method) IN" in text
        assert "payments.created_by = " in text and "shop_sale_payments.created_by = " in text
        assert "abs(payments.amount) >= " in text and "abs(shop_sale_payments.amount) <= " in text
    ids, _ = feed_sql(edited=True)
    # Tahrir faqat bron to'lovida — do'kon tarmog'i yo'q
    assert "payments.method_changes IS NOT NULL" in ids and "shop_sale_payments" not in ids
    assert "CAST(payments.method_changes AS VARCHAR) NOT IN" in ids


def test_staff_and_room_sorts_join_and_put_blanks_last():
    ids, _ = feed_sql(sort_by="created_by_name")
    assert "LEFT OUTER JOIN users ON users.id = payments.created_by" in ids
    assert "LEFT OUTER JOIN users ON users.id = shop_sale_payments.created_by" in ids
    assert "ORDER BY feed.staff ASC NULLS LAST" in ids
    ids, _ = feed_sql(sort_by="room_number")
    assert "LEFT OUTER JOIN rooms" in ids and "ORDER BY feed.room ASC NULLS LAST" in ids
    # Odatdagi saralashda qo'shimcha JOIN yo'q
    ids, _ = feed_sql(sort_by="amount")
    assert "JOIN users" not in ids and "JOIN rooms" not in ids


def test_invoice_filters_and_people_search():
    conditions = svc()._invoice_conditions(
        HOTEL, None, None, None, "Ali",
        InvoiceFilters(debt_only=True, discount_only=True, min_amount=10, max_amount=20),
    )
    text = " ".join(sql(c) for c in conditions)
    assert "invoices.invoice_number ILIKE" in text
    assert "invoices.reservation_id IN (SELECT reservations.id" in text
    assert "guests.first_name ILIKE" in text and "rooms.room_number ILIKE" in text
    assert "invoices.total_amount - invoices.paid_amount >" in text
    assert "invoices.status NOT IN" in text and "invoices.discount_amount >" in text
    assert "invoices.total_amount >=" in text and "invoices.total_amount <=" in text
    # Filtrsiz — avvalgi shartlar soni
    assert len(svc()._invoice_conditions(HOTEL, "PAID", None, None, "INV-1")) == 3


# --- 3. Haqiqiy so'rovlar (SQLite) -----------------------------------------

def at(day: int, hour: int) -> datetime:
    """Mahalliy vaqt (UTC+5) -> UTC."""
    return datetime(2026, 11, day, hour - 5, 0, tzinfo=timezone.utc)


def run(body):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [
            Hotel.__table__, Branch.__table__, User.__table__, Guest.__table__,
            Room.__table__, Reservation.__table__, Invoice.__table__,
            InvoiceLineItem.__table__, Payment.__table__,
        ]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as session:
                await body(session, await seed(session))
        finally:
            await engine.dispose()

    asyncio.run(main())


async def seed(session) -> dict:
    """Bitta bron: xona 300 000, keyin jarima 50 000; chiqib ketgan (2-noyabr).

    P1 xona 300 000 naqd (Aziz) · P2 jarima 50 000 eski karta kodi (Bobur)
    · P3 4-noyabr 100 000 online, turi tahrirlangan (Aziz) va P4 4-noyabr
    20 000 naqd (Bobur) — ikkalasi qarz to'lovi va ortiqcha. (Bazada
    `amount > 0` — qaytarim qoidasi yuqorida, SQL va qoida darajasida.)
    """
    aziz = User(user_type="RECEPTION", username="aziz", password_hash="x", first_name="Aziz", last_name="Karimov")
    bobur = User(user_type="MANAGER", username="bobur", password_hash="x", first_name="Bobur", last_name="Aliyev")
    guest = Guest(hotel_id=HOTEL, first_name="Ali", last_name="Valiyev")
    session.add_all([aziz, bobur, guest])
    await session.flush()
    room = Room(hotel_id=HOTEL, branch_id=BRANCH, floor_id=uuid.uuid4(), room_type_id=uuid.uuid4(), room_number="12")
    session.add(room)
    await session.flush()
    reservation = Reservation(
        hotel_id=HOTEL, branch_id=BRANCH, reservation_number="RES-1", guest_id=guest.id,
        room_id=room.id, check_in_date=date(2026, 11, 1), check_out_date=date(2026, 11, 2),
        status="CHECKED_OUT", created_by=aziz.id,
    )
    session.add(reservation)
    await session.flush()
    invoice = Invoice(
        hotel_id=HOTEL, branch_id=BRANCH, reservation_id=reservation.id, guest_id=guest.id,
        invoice_number="INV-1", invoice_date=date(2026, 11, 1), total_amount=350_000,
        paid_amount=350_000, status="PAID", created_by=aziz.id,
    )
    session.add(invoice)
    await session.flush()
    session.add_all([
        InvoiceLineItem(invoice_id=invoice.id, hotel_id=HOTEL, branch_id=BRANCH, description="Room charge: 12",
                        line_type="ROOM_CHARGE", unit_price=300_000, total_price=300_000, created_at=at(1, 12)),
        InvoiceLineItem(invoice_id=invoice.id, hotel_id=HOTEL, branch_id=BRANCH, description="Jarima: Kech chiqish",
                        line_type="PENALTY", unit_price=50_000, total_price=50_000, created_at=at(2, 13)),
    ])

    def pay(number, amount, method, when, who, changes=None):
        return Payment(
            hotel_id=HOTEL, branch_id=BRANCH, invoice_id=invoice.id, payment_number=number,
            amount=amount, payment_method=method, payment_date=when.date(), created_by=who.id,
            created_at=when, method_changes=changes,
        )

    edit = [{"kind": "full", "old": "CASH", "new": "ONLINE", "at": at(4, 10).isoformat()}]
    rows = {
        "P1": pay("PAY-1", 300_000, "CASH", at(1, 12), aziz),
        "P2": pay("PAY-2", 50_000, "CREDIT_CARD", at(2, 14), bobur),
        "P3": pay("PAY-3", 100_000, "ONLINE", at(4, 9), aziz, changes=edit),
        "P4": pay("PAY-4", 20_000, "CASH", at(4, 11), bobur),
    }
    session.add_all(rows.values())
    await session.commit()
    return {"aziz": aziz.id, "bobur": bobur.id, **{k: v.id for k, v in rows.items()}}


async def feed(session, ids, *, sort_by="created_at", sort_dir="asc", skip=0, limit=50, **filters):
    rows, total, amount = await FinanceService(session).payments_feed(
        HOTEL, skip=skip, limit=limit, sort_by=sort_by, sort_dir=sort_dir,
        filters=PaymentFeedFilters(**filters),
    )
    names = {v: k for k, v in ids.items()}
    return [names[r["id"]] for r in rows], total, amount


def test_feed_filters_on_real_rows():
    async def body(session, ids):
        # Filtrsiz — hammasi; qatorlarda xodim id ham
        names, total, amount = await feed(session, ids)
        assert names == ["P1", "P2", "P3", "P4"] and total == 4 and amount == 470_000
        rows, *_ = await FinanceService(session).payments_feed(HOTEL, limit=50)
        assert {r["created_by"] for r in rows} == {ids["aziz"], ids["bobur"]}

        k = lambda *v: frozenset(v)  # noqa: E731
        assert (await feed(session, ids, kinds=k("PENALTY")))[:2] == (["P2"], 1)
        assert (await feed(session, ids, kinds=k("ROOM")))[0] == ["P1"]
        # 4-noyabr to'lovlari — chiqib ketgandan keyin va haqlardan ortiqcha
        assert (await feed(session, ids, kinds=k("DEBT")))[:3] == (["P3", "P4"], 2, 120_000)
        assert (await feed(session, ids, kinds=k("EXTRA")))[0] == ["P3", "P4"]
        assert (await feed(session, ids, kinds=k("REFUND")))[:3] == ([], 0, 0)
        names, total, amount = await feed(session, ids, kinds=k("ROOM", "PENALTY", "REFUND"))
        assert names == ["P1", "P2"] and total == 2 and amount == 350_000
        assert (await feed(session, ids, kinds=k("SHOP")))[:2] == ([], 0)
        assert (await feed(session, ids, kinds=k("RESERVATION", "SHOP")))[1] == 4

        # Haq turi bo'yicha sahifa Pythonda: jami soni sahifadan qat'i nazar
        assert (await feed(session, ids, kinds=k("ROOM", "PENALTY"), skip=1, limit=1))[:2] == (["P2"], 2)

        # Eski karta kodi "Bank kartasi" ga tushadi
        assert (await feed(session, ids, methods=method_codes("CARD")))[0] == ["P2"]
        assert (await feed(session, ids, methods=method_codes("CASH")))[0] == ["P1", "P4"]
        assert (await feed(session, ids, created_by=ids["bobur"]))[0] == ["P2", "P4"]
        assert (await feed(session, ids, min_amount=100_000))[0] == ["P1", "P3"]
        assert (await feed(session, ids, min_amount=10_000, max_amount=60_000))[0] == ["P2", "P4"]
        assert (await feed(session, ids, edited=True))[0] == ["P3"]
        # Filtrlar birga ("va"): xodim + qarz to'lovi
        assert (await feed(session, ids, created_by=ids["aziz"], kinds=k("DEBT", "PENALTY")))[0] == ["P3"]
        assert (await feed(session, ids, created_by=ids["bobur"], kinds=k("DEBT")))[0] == ["P4"]

        # Xodim bo'yicha saralash: Aziz Karimov < Bobur Aliyev
        names, *_ = await feed(session, ids, sort_by="created_by_name")
        assert set(names[:2]) == {"P1", "P3"} and set(names[2:]) == {"P2", "P4"}
        names, *_ = await feed(session, ids, sort_by="created_by_name", sort_dir="desc")
        assert set(names[:2]) == {"P2", "P4"}

        staff = await FinanceService(session).payment_staff(HOTEL)
        assert [(s["name"], s["count"], s["total"]) for s in staff] == [
            ("Aziz Karimov", 2, 400_000.0),
            ("Bobur Aliyev", 2, 70_000.0),
        ]
        dated = await FinanceService(session).payment_staff(
            HOTEL, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2)
        )
        assert [s["name"] for s in dated] == ["Bobur Aliyev"]

    run(body)


def test_invoice_search_people_and_totals_on_real_rows():
    async def body(session, ids):
        service = FinanceService(session)
        for text in ("Valiyev", "RES-1", "12", "INV-1"):
            assert await service.count_invoices(HOTEL, search=text) == 1, text
        assert await service.count_invoices(HOTEL, search="Boshqa") == 0
        # To'langan hujjat qarz emas
        assert await service.count_invoices(HOTEL, filters=InvoiceFilters(debt_only=True)) == 0
        totals = await service.invoice_totals(HOTEL)
        assert totals == {"count": 1, "total": 350_000.0, "remaining": 0.0}
        invoices = await service.get_invoices(HOTEL)
        people = await service.invoice_people(invoices)
        assert people[invoices[0].reservation_id] == {
            "reservation_number": "RES-1", "guest_name": "Ali Valiyev", "room_number": "12",
        }

    run(body)
