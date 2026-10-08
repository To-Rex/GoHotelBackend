"""Filiallarni ajratish migratsiyasi (a6b7c8d9e0f1) — haqiqiy PostgreSQL'da.

Alohida vaqtinchalik bazada: eski sxema (f5c6d7e8f9a0) → ko'p filialli
mehmonxona ma'lumoti (ikki filialda bron qilgan mehmon, hamrohlar, boshqa
filialdagi do'kon savdosi, xizmatlar, hisob rejasi...) → migratsiya →
tekshiruv:

* hech bir yozuv yo'qolmadi, filial ustuni bo'sh qolmadi;
* har yozuv to'g'ri filialga tushdi;
* filialli yozuvlar bir-biriga faqat O'Z filiali ichida bog'langan
  (aks holda filial ichidagi ko'rinishda bog'lanish uzilib qolardi);
* sozlamalar har filialga nusxalandi, filiali yo'q mehmonxonaga asosiy
  filial ochildi; unikal kalitlar endi filial bo'yicha.

Faqat lokal test serverida: GOHOTEL_TEST_PG_URL (masalan
postgresql+asyncpg://postgres@127.0.0.1:55432/gohotel_test).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.test_hotel_purge import Seeder

URL = os.environ.get("GOHOTEL_TEST_PG_URL", "")
pytestmark = pytest.mark.skipif(
    not URL or "test" not in URL.rsplit("/", 1)[-1] or not any(h in URL for h in ("127.0.0.1", "localhost")),
    reason="GOHOTEL_TEST_PG_URL (lokal test bazasi) berilmagan",
)

BACKEND = Path(__file__).resolve().parents[1]
OLD_REVISION = "f5c6d7e8f9a0"
NEW_REVISION = "a6b7c8d9e0f1"


def _alembic(db_url: str, *args: str) -> None:
    env = dict(os.environ, DATABASE_URL=db_url, PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]


async def _admin(sql: str) -> None:
    import asyncpg

    base = URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0]
    conn = await asyncpg.connect(base + "/postgres")
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def migrated():
    """Vaqtinchalik baza: eski sxema → ma'lumot → yangi sxema."""
    name = f"gohotel_test_mig_{uuid.uuid4().hex[:8]}"
    db_url = URL.rsplit("/", 1)[0] + "/" + name
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        _alembic(db_url, "upgrade", OLD_REVISION)
        ids = asyncio.run(_seed(db_url))
        _alembic(db_url, "upgrade", NEW_REVISION)
        yield db_url, ids
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def run(db_url, fn):
    async def main():
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        engine = create_async_engine(db_url)
        try:
            async with AsyncSession(engine) as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(main())


async def _seed(db_url: str) -> dict:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(db_url)
    ids: dict = {}
    try:
        async with AsyncSession(engine) as s:
            sd = Seeder(s)
            await sd.load()

            async def add(key, table, owner, **values):
                ids[key] = await sd.insert(table, owner, values)
                return ids[key]

            await add("A", "hotels", "A", status="ACTIVE",
                      settings=json.dumps({"booking": {"daily_unit": "24h"}, "debt": {"enabled": True}}))
            await add("B", "hotels", "B", status="ACTIVE", settings=json.dumps({"shift": {"mode": "cash"}}))
            await add("C", "hotels", "C", status="ACTIVE")  # filialsiz mehmonxona
            A1 = await add("A1", "branches", "A", is_main_branch=True, name="Markaz")
            A2 = await add("A2", "branches", "A", is_main_branch=False, name="Chilonzor")
            B1 = await add("B1", "branches", "B", is_main_branch=True)

            admin = await add("admin", "users", "A", user_type="ADMIN", branch_id=None)
            emp1 = await add("emp1", "users", "A", user_type="EMPLOYEE", branch_id=A1)
            emp2 = await add("emp2", "users", "A", user_type="EMPLOYEE", branch_id=A2)
            await add("empB", "users", "B", user_type="EMPLOYEE", branch_id=B1)
            await add("empNull", "users", "A", user_type="EMPLOYEE", branch_id=None)

            rt = await add("rt", "room_types", "A", name="Lux")
            rt_global = await add("rt_global", "room_types", "G", name="Standart", hotel_id=None)
            await s.execute(text("INSERT INTO hotel_room_types (hotel_id, room_type_id) VALUES (:h, :r), (:h, :g)"),
                            {"h": ids["A"], "r": rt, "g": rt_global})
            am = await add("am", "amenities", "G", name="Wi-Fi")
            await s.execute(text("INSERT INTO hotel_amenities (hotel_id, amenity_id) VALUES (:h, :a)"),
                            {"h": ids["A"], "a": am})

            f1 = await add("f1", "floors", "A", branch_id=A1, floor_number=1)
            f2 = await add("f2", "floors", "A", branch_id=A2, floor_number=1)
            r1 = await add("r1", "rooms", "A", branch_id=A1, floor_id=f1, room_type_id=rt, room_number="101")
            r2 = await add("r2", "rooms", "A", branch_id=A2, floor_id=f2, room_type_id=rt, room_number="201")
            await add("r2g", "rooms", "A", branch_id=A2, floor_id=f2, room_type_id=rt_global, room_number="202")
            fB = await add("fB", "floors", "B", branch_id=B1, floor_number=1)
            await add("rB", "rooms", "B", branch_id=B1, floor_id=fB, room_type_id=rt_global, room_number="1")

            shared = await add("g_shared", "guests", "A", first_name="Ali", last_name="Valiyev", passport_number="AA1")
            only2 = await add("g_only2", "guests", "A", first_name="Bek", last_name="Ikkinchi")
            comp = await add("g_comp", "guests", "A", first_name="Hamroh", last_name="Faqat")
            await add("g_none", "guests", "A", first_name="Hech", last_name="Qayerda")
            await add("g_B", "guests", "B", first_name="Boshqa", last_name="Mehmonxona")

            def res(key, branch, room, guest, companions=None, by=None):
                values = dict(branch_id=branch, room_id=room, guest_id=guest, created_by=by or admin)
                if companions is not None:
                    values["companions"] = json.dumps(companions)
                return add(key, "reservations", "A", **values)

            await res("res1", A1, r1, shared)
            await res("res2", A1, r1, shared)
            # A1: 4 ta bron; A2: bron + hamroh + kamera + shikoyat = 4 — teng,
            # asosiy yozuv asosiy filialda qoladi
            await res("res5", A1, r1, shared)
            await res("res6", A1, r1, shared)
            await res("res3", A2, r2, shared, companions=[{"guest_id": str(comp), "name": "Hamroh"}], by=emp2)
            await res("res4", A2, r2, only2, companions=[{"guest_id": str(shared).upper(), "name": "Ali"}, {"name": "ismsiz"}])
            await add("resB", "reservations", "B", branch_id=B1, room_id=ids["rB"], guest_id=ids["g_B"])

            inv = await add("inv3", "invoices", "A", reservation_id=ids["res3"], guest_id=shared)
            await add("pay3", "payments", "A", invoice_id=inv)
            await add("line3", "invoice_line_items", "A", invoice_id=inv)
            svc = await add("svc", "services", "G")
            hs = await add("hs", "hotel_services", "A", service_id=svc)
            await add("rs3", "reservation_services", "A", reservation_id=ids["res3"], hotel_service_id=hs)
            await add("pen3", "reservation_penalties", "A", reservation_id=ids["res3"])

            prod = await add("prod", "shop_products", "A", name="Suv")
            batch = await add("batch", "shop_batches", "A", product_id=prod, quantity=10, remaining=5)
            sale2 = await add("sale2", "shop_sales", "A", reservation_id=ids["res3"], created_by=emp2)
            await add("item2", "shop_sale_items", "A", sale_id=sale2, product_id=prod, batch_id=batch)
            await add("sp2", "shop_sale_payments", "A", sale_id=sale2)
            sale1 = await add("sale1", "shop_sales", "A", reservation_id=None, created_by=emp1)
            await add("item1", "shop_sale_items", "A", sale_id=sale1, product_id=prod, batch_id=batch)
            await add("wo", "shop_writeoffs", "A", product_id=prod)

            lp = await add("l_parent", "ledgers", "A", code="100", parent_id=None)
            await add("l_child", "ledgers", "A", code="110", parent_id=lp)
            je = await add("je", "journal_entries", "A")
            await add("jel", "journal_entry_lines", "A", journal_entry_id=je, ledger_id=lp)
            await add("ct", "checklist_templates", "A")

            await add("fp", "guest_face_profiles", "A", guest_id=shared)
            await add("sight2", "face_sightings", "A", branch_id=A2, guest_id=shared, reservation_id=None)
            await add("fb2", "guest_feedback", "A", branch_id=A2, guest_id=shared, reservation_id=None, room_id=None)
            await add("call3", "incoming_calls", "A", reservation_id=ids["res3"], guest_id=shared)
            await add("scan2", "document_scans", "A", guest_id=only2)
            await add("prob2", "problems", "A", branch_id=None, room_id=r2, task_id=None, reported_by=admin)
            await add("exp2", "expenses", "A", branch_id=None, created_by=emp2)
            await add("exp_admin", "expenses", "A", branch_id=None, created_by=admin)
            await add("note2", "notifications", "A", user_id=emp2)
            await add("note_admin", "notifications", "A", user_id=admin)
            await add("msg2", "staff_messages", "A", room_id=r2, created_by=admin)
            await add("audit2", "audit_logs", "A", user_id=emp2)
            await add("dev2", "trusted_devices", "A", last_user_id=emp2)
            await add("hist2", "room_status_history", "A", room_id=r2)
            await add("shift2", "shift_sessions", "A", branch_id=None, user_id=emp2)
            await add("report", "reports", "A", generated_by=emp1)
            await s.commit()
    finally:
        await engine.dispose()
    return ids


async def _branch_of(s, table, row_id):
    from sqlalchemy import text

    return (await s.execute(text(f'SELECT branch_id FROM "{table}" WHERE id = :i'), {"i": row_id})).scalar()


def test_rows_land_in_the_right_branch(migrated):
    db_url, ids = migrated
    A1, A2 = ids["A1"], ids["A2"]
    expected = {
        ("users", "admin"): A1, ("users", "empNull"): A1, ("users", "emp2"): A2,
        ("guests", "g_shared"): A1, ("guests", "g_only2"): A2, ("guests", "g_comp"): A2, ("guests", "g_none"): A1,
        ("invoices", "inv3"): A2, ("payments", "pay3"): A2, ("invoice_line_items", "line3"): A2,
        ("reservation_services", "rs3"): A2, ("reservation_penalties", "pen3"): A2,
        ("shop_sales", "sale2"): A2, ("shop_sales", "sale1"): A1, ("shop_sale_payments", "sp2"): A2,
        ("shop_products", "prod"): A1, ("shop_batches", "batch"): A1, ("shop_writeoffs", "wo"): A1,
        ("ledgers", "l_parent"): A1, ("journal_entries", "je"): A1, ("journal_entry_lines", "jel"): A1,
        ("checklist_templates", "ct"): A1, ("room_types", "rt"): A1, ("hotel_services", "hs"): A1,
        ("incoming_calls", "call3"): A2, ("document_scans", "scan2"): A2,
        ("problems", "prob2"): A2, ("expenses", "exp2"): A2, ("expenses", "exp_admin"): A1,
        ("notifications", "note2"): A2, ("notifications", "note_admin"): A1,
        ("staff_messages", "msg2"): A2, ("audit_logs", "audit2"): A2, ("trusted_devices", "dev2"): A2,
        ("room_status_history", "hist2"): A2, ("shift_sessions", "shift2"): A2, ("reports", "report"): A1,
        ("face_sightings", "sight2"): A2, ("guest_feedback", "fb2"): A2,
    }

    async def body(s):
        wrong = {}
        for (table, key), branch in expected.items():
            got = await _branch_of(s, table, ids[key])
            if got != branch:
                wrong[f"{table}:{key}"] = (got, branch)
        assert wrong == {}
        assert await _branch_of(s, "room_types", ids["rt_global"]) is None  # global tur umumiy
    run(db_url, body)


def test_no_scoped_row_is_left_without_a_branch(migrated):
    db_url, _ = migrated

    async def body(s):
        from sqlalchemy import text

        tables = (
            await s.execute(text(
                "SELECT c.table_name FROM information_schema.columns c "
                "JOIN information_schema.columns h ON h.table_name = c.table_name AND h.column_name = 'hotel_id' "
                "WHERE c.table_schema = 'public' AND c.column_name = 'branch_id'"
            ))
        ).scalars().all()
        empty = {}
        for t in tables:
            if t in ("vision_devices", "vision_cameras"):
                continue  # bo'sh — "umumiy" / "biriktirilmagan" ma'nosida
            n = (await s.execute(text(f'SELECT count(*) FROM "{t}" WHERE branch_id IS NULL AND hotel_id IS NOT NULL'))).scalar()
            if n:
                empty[t] = n
        assert empty == {}
    run(db_url, body)


def test_branch_rows_only_reference_rows_of_the_same_branch(migrated):
    """Filial ichida bog'lanish uzilmaydi: bola va ota yozuv bitta filialda."""
    db_url, _ = migrated

    async def body(s):
        from sqlalchemy import text

        fks = (
            await s.execute(text(
                "SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text "
                "FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                "WHERE c.contype = 'f' AND a.attname <> 'branch_id'"
            ))
        ).all()
        branch_tables = set((await s.execute(text(
            "SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='branch_id'"
        ))).scalars().all())
        bad = {}
        for child, col, parent in fks:
            # Xodim boshqa filialga o'tkazilishi mumkin — u alohida ko'riladi
            if parent in ("users", "branches", "hotels") or child not in branch_tables or parent not in branch_tables:
                continue
            n = (await s.execute(text(
                f'SELECT count(*) FROM "{child}" c JOIN "{parent}" p ON p.id = c."{col}" '
                "WHERE p.branch_id IS NOT NULL AND c.branch_id IS DISTINCT FROM p.branch_id"
            ))).scalar()
            if n:
                bad[f"{child}.{col}->{parent}"] = n
        assert bad == {}

        # Filial ustuni yo'q bolalar: savdo qatori — mahsulot va partiya savdo filialida
        n = (await s.execute(text(
            "SELECT count(*) FROM shop_sale_items si JOIN shop_sales s ON s.id = si.sale_id "
            "JOIN shop_products p ON p.id = si.product_id LEFT JOIN shop_batches b ON b.id = si.batch_id "
            "WHERE p.branch_id <> s.branch_id OR (b.id IS NOT NULL AND b.branch_id <> s.branch_id)"
        ))).scalar()
        assert n == 0
        # Hamrohlar ham o'z filialidagi mehmon yozuviga qaraydi
        n = (await s.execute(text(
            "SELECT count(*) FROM reservations r, jsonb_array_elements(r.companions) e "
            "JOIN guests g ON g.id::text = lower(e.value->>'guest_id') "
            "WHERE jsonb_typeof(r.companions) = 'array' AND g.branch_id <> r.branch_id"
        ))).scalar()
        assert n == 0
    run(db_url, body)


def test_shared_guest_is_split_without_losing_anything(migrated):
    db_url, ids = migrated

    async def body(s):
        from sqlalchemy import text

        q = lambda sql, **p: s.execute(text(sql), p)  # noqa: E731
        rows = (await q(
            "SELECT id, branch_id FROM guests WHERE first_name='Ali' AND last_name='Valiyev' AND passport_number='AA1'"
        )).all()
        assert sorted(r[1] == ids["A1"] for r in rows) == [False, True]  # asosiy + A2 nusxasi
        clone = next(r[0] for r in rows if r[1] == ids["A2"])
        assert (await q("SELECT guest_id FROM reservations WHERE id=:i", i=ids["res3"])).scalar() == clone
        assert (await q("SELECT guest_id FROM reservations WHERE id=:i", i=ids["res1"])).scalar() == ids["g_shared"]
        assert (await q("SELECT guest_id FROM invoices WHERE id=:i", i=ids["inv3"])).scalar() == clone
        assert (await q("SELECT guest_id FROM face_sightings WHERE id=:i", i=ids["sight2"])).scalar() == clone
        assert (await q("SELECT guest_id FROM incoming_calls WHERE id=:i", i=ids["call3"])).scalar() == clone
        companions = (await q("SELECT companions FROM reservations WHERE id=:i", i=ids["res4"])).scalar()
        assert companions[0]["guest_id"] == str(clone) and companions[0]["name"] == "Ali"
        assert companions[1] == {"name": "ismsiz"}
        # Yuz profili har ikkala yozuvda
        assert (await q("SELECT count(*) FROM guest_face_profiles WHERE guest_id IN (:a, :b)",
                        a=ids["g_shared"], b=clone)).scalar() == 2
        # Bron soni o'zgarmadi
        assert (await q("SELECT count(*) FROM reservations")).scalar() == 7
    run(db_url, body)


def test_catalogs_are_copied_per_branch(migrated):
    db_url, ids = migrated
    A1, A2 = ids["A1"], ids["A2"]

    async def body(s):
        from sqlalchemy import text

        q = lambda sql, **p: s.execute(text(sql), p)  # noqa: E731
        # Xona turi: A2 nusxasi, A2 xonasi unga ulangan; global tur joyida
        rt2 = (await q("SELECT id FROM room_types WHERE name='Lux' AND branch_id=:b", b=A2)).scalar()
        assert rt2 and rt2 != ids["rt"]
        assert (await q("SELECT room_type_id FROM rooms WHERE id=:i", i=ids["r2"])).scalar() == rt2
        assert (await q("SELECT room_type_id FROM rooms WHERE id=:i", i=ids["r1"])).scalar() == ids["rt"]
        assert (await q("SELECT room_type_id FROM rooms WHERE id=:i", i=ids["r2g"])).scalar() == ids["rt_global"]
        hrt = set((await q("SELECT branch_id, room_type_id FROM hotel_room_types")).all())
        assert hrt == {(A1, ids["rt"]), (A1, ids["rt_global"]), (A2, rt2), (A2, ids["rt_global"])}
        assert set((await q("SELECT branch_id FROM hotel_amenities")).scalars().all()) == {A1, A2}
        # Xizmat narxi: A2 bronining xizmati A2 nusxasiga qaraydi
        hs2 = (await q("SELECT id FROM hotel_services WHERE branch_id=:b", b=A2)).scalar()
        assert (await q("SELECT hotel_service_id FROM reservation_services WHERE id=:i", i=ids["rs3"])).scalar() == hs2
        # Do'kon: A2 mahsuloti, A2 savdosi partiya nusxasiga (qoldig'i 0), zaxira A1 da
        p2 = (await q("SELECT id FROM shop_products WHERE name='Suv' AND branch_id=:b", b=A2)).scalar()
        item2 = (await q("SELECT product_id, batch_id FROM shop_sale_items WHERE id=:i", i=ids["item2"])).first()
        assert item2[0] == p2 and item2[1] != ids["batch"]
        b2 = (await q("SELECT branch_id, quantity, remaining, product_id FROM shop_batches WHERE id=:i", i=item2[1])).first()
        assert tuple(b2) == (A2, 1, 0, p2)  # A2 da sotilgan 1 dona "o'tkazildi"
        # Asosiy partiya: 10 - 1 = 9 kirim, qoldiq 5 o'zgarmagan
        assert tuple((await q("SELECT quantity, remaining FROM shop_batches WHERE id=:i", i=ids["batch"])).first()) == (9, 5)
        item1 = (await q("SELECT product_id, batch_id FROM shop_sale_items WHERE id=:i", i=ids["item1"])).first()
        assert tuple(item1) == (ids["prod"], ids["batch"])
        # Hisob rejasi: A2 nusxasida ota schyot ham A2 niki
        child2 = (await q("SELECT parent_id FROM ledgers WHERE code='110' AND branch_id=:b", b=A2)).scalar()
        assert (await q("SELECT branch_id FROM ledgers WHERE id=:i", i=child2)).scalar() == A2
        assert (await q("SELECT count(*) FROM checklist_templates WHERE hotel_id=:h", h=ids["A"])).scalar() == 2
        # B mehmonxonasiga hech narsa nusxalanmadi
        assert (await q("SELECT count(*) FROM shop_products WHERE hotel_id=:h", h=ids["B"])).scalar() == 0
    run(db_url, body)


def test_settings_and_branchless_hotel(migrated):
    db_url, ids = migrated

    async def body(s):
        from sqlalchemy import text

        rows = dict((await s.execute(text("SELECT id, settings FROM branches WHERE hotel_id=:h"), {"h": ids["A"]})).all())
        assert rows[ids["A1"]] == rows[ids["A2"]] == {"booking": {"daily_unit": "24h"}, "debt": {"enabled": True}}
        main_c = (await s.execute(text(
            "SELECT name, code, is_main_branch FROM branches WHERE hotel_id=:h"), {"h": ids["C"]})).all()
        assert [tuple(r) for r in main_c] == [("Asosiy filial", "MAIN", True)]
    run(db_url, body)


def test_unique_keys_are_per_branch_now(migrated):
    db_url, ids = migrated

    async def body(s):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        # Boshqa filialda xuddi shu nom — mumkin; o'sha filialda — yo'q
        await s.execute(text(
            "INSERT INTO shop_products (id, hotel_id, branch_id, name, is_active, created_by, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :h, :b, 'Choy', true, :u, now(), now())"), {"h": ids["A"], "b": ids["A1"], "u": ids["admin"]})
        await s.execute(text(
            "INSERT INTO shop_products (id, hotel_id, branch_id, name, is_active, created_by, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :h, :b, 'Choy', true, :u, now(), now())"), {"h": ids["A"], "b": ids["A2"], "u": ids["admin"]})
        with pytest.raises(IntegrityError):
            await s.execute(text(
                "INSERT INTO shop_products (id, hotel_id, branch_id, name, is_active, created_by, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :h, :b, 'Choy', true, :u, now(), now())"), {"h": ids["A"], "b": ids["A2"], "u": ids["admin"]})
        await s.rollback()
    run(db_url, body)
