"""UTC bo'yicha yozilib qolgan hujjat sanalarini tuzatish (date_repair).

Ishga tushirish: pytest tests/test_date_repair.py

Nozik joylar:

1. Faqat IMZOga tushgan yozuv o'zgaradi: sana = created_at ning UTC kuni
   va ≠ mahalliy kuni. Qo'lda kiritilgan (boshqa) sana tegilmaydi,
   allaqachon to'g'ri yozuv ham.

2. Hosilaviy ustun (`due_date` = sana + 7) faqat aynan eski sana + 7
   bo'lsa suriladi — qo'lda o'zgartirilgan muddat saqlanadi.

3. Ikkinchi marta ishga tushsa hech narsa qilmaydi (idempotent) — deploy
   takrorlansa ham xavfsiz.

4. REPAIRS ro'yxati haqiqiy modellarga mos: jadval va ustunlar bor, turlari
   Date / DateTime. Ustun nomi xato yozilsa migratsiya ishlab ketmasligi
   kerak.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.shared.date_repair import (
    REPAIRS,
    DateRepair,
    repair_all_utc_dates,
    repair_utc_dates,
)

TASHKENT = 300  # daqiqa


def utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


DOCS = sa.Table(
    "docs", sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("doc_date", sa.Date),
    sa.Column("due_date", sa.Date),
)


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    DOCS.metadata.create_all(engine)
    with engine.begin() as c:
        yield c


REPAIR = DateRepair("docs", "doc_date", derived=(("due_date", timedelta(days=7)),))


def insert(conn, id_, created, doc_date, due=None):
    # SQLite vaqt belgisini zonasiz saqlaydi — haqiqiy bazadagi kabi UTC
    conn.execute(
        DOCS.insert().values(
            id=id_, created_at=created.replace(tzinfo=None), doc_date=doc_date, due_date=due
        )
    )


def rows(conn):
    return {
        r.id: (r.doc_date, r.due_date)
        for r in conn.execute(sa.select(DOCS.c.id, DOCS.c.doc_date, DOCS.c.due_date).order_by(DOCS.c.id))
    }


def test_night_row_moves_to_local_day(conn):
    # 2-noyabr 00:06 Toshkent = 1-noyabr 19:06 UTC; sana 1-noyabr yozilgan
    insert(conn, 1, utc(2026, 11, 1, 19, 6), date(2026, 11, 1), date(2026, 11, 8))
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 1
    assert rows(conn)[1] == (date(2026, 11, 2), date(2026, 11, 9))


def test_daytime_row_is_untouched(conn):
    # 12:00 Toshkent = 07:00 UTC — UTC va mahalliy kun bir xil
    insert(conn, 1, utc(2026, 11, 2, 7, 0), date(2026, 11, 2), date(2026, 11, 9))
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 0
    assert rows(conn)[1] == (date(2026, 11, 2), date(2026, 11, 9))


def test_manually_backdated_row_is_untouched(conn):
    # Tunda yaratilgan, lekin sana ataylab 3 kun oldin — UTC kuniga teng emas
    insert(conn, 1, utc(2026, 11, 1, 19, 6), date(2026, 10, 29), date(2026, 11, 5))
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 0
    assert rows(conn)[1] == (date(2026, 10, 29), date(2026, 11, 5))


def test_already_correct_night_row_is_untouched(conn):
    # Yangilanishdan keyingi tungi yozuv: sana allaqachon mahalliy kun
    insert(conn, 1, utc(2026, 11, 2, 19, 6), date(2026, 11, 3), date(2026, 11, 10))
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 0
    assert rows(conn)[1] == (date(2026, 11, 3), date(2026, 11, 10))


def test_derived_column_moves_only_when_it_still_equals_old_date_plus_shift(conn):
    insert(conn, 1, utc(2026, 11, 1, 19, 6), date(2026, 11, 1), date(2026, 11, 8))  # +7
    insert(conn, 2, utc(2026, 11, 1, 20, 0), date(2026, 11, 1), date(2026, 11, 20))  # qo'lda
    insert(conn, 3, utc(2026, 11, 1, 21, 0), date(2026, 11, 1), None)
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 3
    got = rows(conn)
    assert got[1] == (date(2026, 11, 2), date(2026, 11, 9))
    assert got[2] == (date(2026, 11, 2), date(2026, 11, 20))
    assert got[3] == (date(2026, 11, 2), None)


def test_before_bound_skips_later_rows(conn):
    insert(conn, 1, utc(2026, 10, 31, 19, 6), date(2026, 10, 31))
    insert(conn, 2, utc(2026, 11, 5, 19, 6), date(2026, 11, 5))  # chegaradan keyin
    fixed = repair_utc_dates(
        conn, REPAIR, TASHKENT, before=datetime(2026, 11, 2, 19, 0)
    )
    assert fixed == 1
    got = rows(conn)
    assert got[1][0] == date(2026, 11, 1)
    assert got[2][0] == date(2026, 11, 5)


def test_null_dates_are_skipped(conn):
    insert(conn, 1, utc(2026, 11, 1, 19, 6), None)
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 0


def test_second_run_changes_nothing(conn):
    insert(conn, 1, utc(2026, 11, 1, 19, 6), date(2026, 11, 1), date(2026, 11, 8))
    insert(conn, 2, utc(2026, 11, 1, 23, 59), date(2026, 11, 1), date(2026, 11, 8))
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 2
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 0
    assert rows(conn)[2] == (date(2026, 11, 2), date(2026, 11, 9))


def test_edge_of_the_night_window(conn):
    # 04:59 Toshkent (23:59 UTC) — hali xato oynasi; 05:00 (00:00 UTC) — emas
    insert(conn, 1, utc(2026, 11, 1, 23, 59), date(2026, 11, 1))
    insert(conn, 2, utc(2026, 11, 2, 0, 0), date(2026, 11, 2))
    assert repair_utc_dates(conn, REPAIR, TASHKENT) == 1
    got = rows(conn)
    assert got[1][0] == date(2026, 11, 2)
    assert got[2][0] == date(2026, 11, 2)


def test_other_offset_is_respected(conn):
    # +03:00 da 02:30 mahalliy = 23:30 UTC; +05:00 bilan ham xuddi shu qator
    insert(conn, 1, utc(2026, 11, 1, 23, 30), date(2026, 11, 1))
    assert repair_utc_dates(conn, REPAIR, 180) == 1
    assert rows(conn)[1][0] == date(2026, 11, 2)


def test_repair_all_reports_per_table(conn):
    insert(conn, 1, utc(2026, 11, 1, 19, 6), date(2026, 11, 1))
    counts = repair_all_utc_dates(conn, TASHKENT, repairs=(REPAIR,))
    assert counts == {"docs.doc_date": 1}


def test_repairs_match_real_models():
    """Jadval va ustun nomlari haqiqiy modellarda bor va turlari to'g'ri."""
    import app.infrastructure.database.models  # noqa: F401 — modellarni ro'yxatga oladi
    from app.infrastructure.database.models.base import Base

    tables = Base.metadata.tables
    for repair in REPAIRS:
        assert repair.table in tables, repair.table
        t = tables[repair.table]
        assert "id" in t.c, repair.table
        assert isinstance(t.c[repair.column].type, sa.Date), f"{repair.table}.{repair.column}"
        assert isinstance(t.c[repair.created_column].type, sa.DateTime), (
            f"{repair.table}.{repair.created_column}"
        )
        for name, _ in repair.derived:
            assert isinstance(t.c[name].type, sa.Date), f"{repair.table}.{name}"


def test_repairs_cover_every_business_date_column():
    """Modellardagi har bir hujjat sanasi ro'yxatda — yangi ustun qo'shilsa
    test eslatadi (odam sanalari: tug'ilgan kun, ishga olingan kun, bron
    kunlari — mijoz kiritadi, serverning "bugun"i emas)."""
    import app.infrastructure.database.models  # noqa: F401
    from app.infrastructure.database.models.base import Base

    not_stamped_by_server = {
        "guests.birth_date",
        "users.hire_date",
        "users.termination_date",
        "reservations.check_in_date",
        "reservations.check_out_date",
    }
    covered = {f"{r.table}.{r.column}" for r in REPAIRS}
    covered |= {f"{r.table}.{name}" for r in REPAIRS for name, _ in r.derived}
    date_columns = {
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.c
        if isinstance(c.type, sa.Date)
    }
    assert date_columns - covered - not_stamped_by_server == set()
