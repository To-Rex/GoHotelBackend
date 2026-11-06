"""Qisman tahrirda ajralgan to'lov qatorlari: vaqt va tarix (payment_splits).

Ishga tushirish: pytest tests/test_payment_splits.py

Nozik joylar:

1. Ajralgan qator asl to'lov VAQTIGA qaytadi — smena kassasi va shaxsiy
   hisobot `created_at` oynasi bilan ishlaydi, aks holda kechagi pul
   bugungi smenaga o'tib qoladi.

2. Faqat ANIQ bog'langan qator o'zgaradi (asl to'lov tarixidagi
   `new_payment_number`); boshqa mehmonxonadagi bir xil raqam, oddiy
   to'lovlar va asl to'lovning o'zi tegilmaydi.

3. Vaqt faqat orqaga suriladi; zanjirda (bo'lingan qator yana bo'lingan)
   hammasi eng birinchi to'lov vaqtida turadi.

4. Tarixga `split_from` yozuvi BOSHIGA qo'shiladi — qatorning keyingi
   tahrirlari saqlanadi. Qayta ishga tushsa hech narsa o'zgarmaydi.
"""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.shared.payment_splits import (
    KIND_FULL,
    KIND_PARTIAL,
    KIND_SPLIT_FROM,
    PAYMENTS,
    is_split_row,
    repair_split_payments,
    split_origin_entry,
)

HOTEL = "hotel-1"
OTHER_HOTEL = "hotel-2"

PAID = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)   # pul olingan payt
EDITED = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)  # tahrir payti

TABLE = sa.Table(
    "payments", sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("hotel_id", sa.String),
    sa.Column("payment_number", sa.String),
    sa.Column("amount", sa.Float),
    sa.Column("payment_method", sa.String),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("method_changes", sa.JSON),
)


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    TABLE.metadata.create_all(engine)
    with engine.begin() as c:
        yield c


def partial(new_number, amount=50000.0, old="CASH", new="ONLINE", at=EDITED, **extra):
    return {
        "kind": KIND_PARTIAL, "old": old, "new": new, "amount": amount,
        "new_payment_number": new_number, "by": "u1", "by_name": "Aziz Karimov",
        "at": at.isoformat(), "note": "Yarmi online", **extra,
    }


def insert(conn, id_, number, created, changes=None, hotel=HOTEL, amount=50000.0, method="CASH"):
    # Tarixsiz qator bazada SQL NULL (JSON `null` emas) — ORM ham shunday yozadi
    conn.execute(TABLE.insert().values(
        id=id_, hotel_id=hotel, payment_number=number, amount=amount,
        payment_method=method, created_at=created,
        method_changes=sa.null() if changes is None else changes,
    ))


def row(conn, id_):
    r = conn.execute(sa.select(TABLE).where(TABLE.c.id == id_)).mappings().one()
    created = r["created_at"]
    return {**r, "created_at": created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created}


# ------------------------------------------------------------ 1. vaqt --

def test_split_row_returns_to_the_original_moment(conn):
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", EDITED, method="ONLINE")

    assert repair_split_payments(conn) == {"created_at": 1, "history": 1}

    assert row(conn, 2)["created_at"] == PAID
    # Asl to'lov tegilmaydi
    assert row(conn, 1)["created_at"] == PAID
    assert row(conn, 1)["method_changes"] == [partial("PAY-B")]
    # Summa va usul ham
    assert row(conn, 2)["amount"] == 50000.0 and row(conn, 2)["payment_method"] == "ONLINE"


def test_second_run_changes_nothing(conn):
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", EDITED)
    repair_split_payments(conn)
    before = row(conn, 2)

    assert repair_split_payments(conn) == {"created_at": 0, "history": 0}
    assert row(conn, 2) == before


def test_already_fixed_split_is_untouched(conn):
    """Yangi kod yozgan qator: vaqt allaqachon asl to'lovniki, tarixi bor."""
    entry = partial("PAY-B", total_before=100000.0, total_after=50000.0)
    insert(conn, 1, "PAY-A", PAID, [entry])
    insert(conn, 2, "PAY-B", PAID, [split_origin_entry(entry, "PAY-A")])

    assert repair_split_payments(conn) == {"created_at": 0, "history": 0}


def test_time_only_moves_backwards(conn):
    """Ajralgan qator vaqti asl to'lovnikidan OLDIN bo'lsa (buzuq/qo'lda
    o'zgartirilgan ma'lumot) — oldinga surilmaydi."""
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", PAID - timedelta(hours=2))

    assert repair_split_payments(conn)["created_at"] == 0
    assert row(conn, 2)["created_at"] == PAID - timedelta(hours=2)


# ------------------------------------------------- 2. faqat bog'langan --

def test_unrelated_rows_are_untouched(conn):
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", EDITED)
    # Oddiy to'lov va faqat butun tahrirli to'lov
    insert(conn, 3, "PAY-C", EDITED)
    full = {"kind": KIND_FULL, "old": "CASH", "new": "CARD", "amount": None,
            "new_payment_number": None, "at": EDITED.isoformat()}
    insert(conn, 4, "PAY-D", PAID, [full], method="CARD")

    repair_split_payments(conn)

    assert row(conn, 3)["created_at"] == EDITED and row(conn, 3)["method_changes"] is None
    assert row(conn, 4)["created_at"] == PAID and row(conn, 4)["method_changes"] == [full]


def test_same_number_in_another_hotel_is_untouched(conn):
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", EDITED)
    insert(conn, 3, "PAY-B", EDITED, hotel=OTHER_HOTEL)

    repair_split_payments(conn)

    assert row(conn, 2)["created_at"] == PAID
    assert row(conn, 3)["created_at"] == EDITED
    assert row(conn, 3)["method_changes"] is None


def test_missing_split_row_is_ignored(conn):
    """Tarixda raqam bor, qator yo'q — xato bermaydi."""
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-GONE")])
    assert repair_split_payments(conn) == {"created_at": 0, "history": 0}


def test_malformed_history_is_ignored(conn):
    insert(conn, 1, "PAY-A", PAID, {"kind": "partial"})          # ro'yxat emas
    insert(conn, 2, "PAY-B", PAID, ["x", None, {"kind": "partial"}])  # raqamsiz
    insert(conn, 3, "PAY-C", EDITED)
    assert repair_split_payments(conn) == {"created_at": 0, "history": 0}


def test_no_edits_at_all(conn):
    insert(conn, 1, "PAY-A", PAID)
    assert repair_split_payments(conn) == {"created_at": 0, "history": 0}


# -------------------------------------------------------- 3. zanjir --

def test_chain_lands_on_the_first_payment_moment(conn):
    """A (3-noyabr) → B (5-noyabrda ajralgan) → C (6-noyabrda B dan ajralgan)."""
    later = EDITED + timedelta(days=1)
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", EDITED, [partial("PAY-C", amount=20000.0, old="ONLINE", new="CARD", at=later)])
    insert(conn, 3, "PAY-C", later, method="CARD")

    assert repair_split_payments(conn) == {"created_at": 2, "history": 2}

    assert row(conn, 2)["created_at"] == PAID
    assert row(conn, 3)["created_at"] == PAID
    assert row(conn, 3)["method_changes"][0]["from_payment_number"] == "PAY-B"


def test_several_splits_of_one_payment(conn):
    later = EDITED + timedelta(hours=3)
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B"), partial("PAY-C", amount=10000.0, new="CARD", at=later)])
    insert(conn, 2, "PAY-B", EDITED)
    insert(conn, 3, "PAY-C", later)

    assert repair_split_payments(conn) == {"created_at": 2, "history": 2}
    assert row(conn, 2)["created_at"] == PAID and row(conn, 3)["created_at"] == PAID
    assert row(conn, 3)["method_changes"][0]["amount"] == 10000.0
    assert row(conn, 3)["method_changes"][0]["new"] == "CARD"


def test_cyclic_links_do_not_hang(conn):
    """Buzuq ma'lumot: ikki qator bir-birini "ajralgan" deb ko'rsatadi.
    Qaysi biri asl ekani noma'lum — tugaydi va vaqtlarga tegmaydi."""
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", EDITED, [partial("PAY-A")])
    assert repair_split_payments(conn)["created_at"] == 0
    assert row(conn, 1)["created_at"] == PAID
    assert row(conn, 2)["created_at"] == EDITED


# -------------------------------------------------------- 4. tarix --

def test_origin_entry_is_written_first_and_later_edits_survive(conn):
    """Ajralgan qator keyin butunlay boshqa turga o'tkazilgan — bu tahrir
    saqlanadi, kelib chiqishi esa boshiga yoziladi."""
    later = {"kind": KIND_FULL, "old": "ONLINE", "new": "CARD", "amount": None,
             "new_payment_number": None, "at": (EDITED + timedelta(hours=1)).isoformat()}
    insert(conn, 1, "PAY-A", PAID, [partial("PAY-B")])
    insert(conn, 2, "PAY-B", EDITED, [later], method="CARD")

    repair_split_payments(conn)

    changes = row(conn, 2)["method_changes"]
    assert [c["kind"] for c in changes] == [KIND_SPLIT_FROM, KIND_FULL]
    assert changes[1] == later
    origin = changes[0]
    assert origin["from_payment_number"] == "PAY-A"
    assert origin["old"] == "CASH" and origin["new"] == "ONLINE"
    assert origin["amount"] == 50000.0
    assert origin["by_name"] == "Aziz Karimov"
    assert origin["at"] == EDITED.isoformat()
    assert origin["note"] == "Yarmi online"
    # Eski yozuvda asl summa saqlanmagan
    assert origin["from_total"] is None
    assert origin["new_payment_number"] is None


def test_split_origin_entry_carries_the_total_when_known():
    entry = partial("PAY-B", total_before=100000.0, total_after=50000.0)
    origin = split_origin_entry(entry, "PAY-A")
    assert origin["kind"] == KIND_SPLIT_FROM
    assert origin["from_total"] == 100000.0
    assert origin["amount"] == 50000.0
    assert "total_before" not in origin and "total_after" not in origin


def test_is_split_row():
    assert is_split_row([{"kind": KIND_SPLIT_FROM}]) is True
    assert is_split_row([{"kind": KIND_PARTIAL}, {"kind": KIND_FULL}]) is False
    assert is_split_row(None) is False
    assert is_split_row({"kind": KIND_SPLIT_FROM}) is False
    assert is_split_row(["split_from", None]) is False


# ------------------------------------------- haqiqiy model bilan moslik --

def test_table_matches_the_real_model():
    """Ustun nomi xato yozilsa migratsiya ishlab ketmasligi kerak."""
    from sqlalchemy.dialects.postgresql import JSONB

    import app.infrastructure.database.models  # noqa: F401 — modellarni ro'yxatga oladi
    from app.infrastructure.database.models.base import Base

    real = Base.metadata.tables["payments"]
    for column in PAYMENTS.c:
        assert column.name in real.c, column.name
    assert isinstance(real.c.created_at.type, sa.DateTime)
    assert real.c.created_at.type.timezone is True
    assert isinstance(real.c.method_changes.type, JSONB)
    # Raqam mehmonxona ichida yagona — bog'lanish shunga tayanadi
    uniques = {
        tuple(c.name for c in constraint.columns)
        for constraint in real.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("hotel_id", "payment_number") in uniques


def test_statements_compile_for_postgresql():
    """Migratsiya PostgreSQL da yuradi: so'rovlar shu dialektda tuziladi va
    tarix ustuni JSONB sifatida bog'lanadi."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql import JSONB

    dialect = postgresql.dialect()
    select_sql = str(
        sa.select(PAYMENTS.c.id, PAYMENTS.c.method_changes)
        .where(PAYMENTS.c.method_changes.is_not(None))
        .compile(dialect=dialect)
    )
    assert "payments.method_changes IS NOT NULL" in select_sql

    update = (
        sa.update(PAYMENTS)
        .where(PAYMENTS.c.id == 1)
        .values(created_at=PAID, method_changes=[{"kind": KIND_SPLIT_FROM}])
        .compile(dialect=dialect)
    )
    assert "UPDATE payments SET created_at=" in str(update)
    assert isinstance(update.binds["method_changes"].type.dialect_impl(dialect), JSONB)
