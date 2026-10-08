"""Mehmonxonani BUTUNLAY o'chirish — haqiqiy PostgreSQL'da.

Talab: o'chirilgan mehmonxonaga tegishli HAMMA ma'lumot o'chsin, boshqa
mehmonxona ma'lumotlariga tegilmasin.

Sinov bazadagi HAR BIR jadvalni avtomatik to'ldiradi (ikki mehmonxona,
global kataloglar, boshqa mehmonxona ishlatgan "umumiy" mehmon), keyin:

* A mehmonxonaning birorta qatori qolmaganini (har jadval bo'yicha),
* boshqa barcha qatorlar BAYTMA-BAYT o'zgarmaganini (umumiy mehmondan
  tashqari — u B ga o'tadi),
* ko'rib chiqish hech narsani o'zgartirmasligini,
* boshqa mehmonxona bog'langan bo'lsa o'chirish to'xtashini tekshiradi.

Faqat alohida test bazasida ishlaydi:
    GOHOTEL_TEST_PG_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/gohotel_test
(migratsiya qilingan, nomida "test" bo'lgan lokal baza). Aks holda o'tkazib
yuboriladi.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import ConflictException
from app.superadmin import hotel_purge_service as hp

URL = os.environ.get("GOHOTEL_TEST_PG_URL", "")
pytestmark = pytest.mark.skipif(
    not URL or "test" not in URL.rsplit("/", 1)[-1] or not any(h in URL for h in ("127.0.0.1", "localhost")),
    reason="GOHOTEL_TEST_PG_URL (lokal test bazasi) berilmagan",
)


def run(coro_fn):
    async def main():
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        engine = create_async_engine(URL)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await coro_fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(main())


# ----------------------------------------------------------- to'ldirish --
class Seeder:
    """Har jadvalga NOT NULL ustunlarini to'ldirib qator qo'yadi."""

    def __init__(self, session):
        self.s = session
        self.n = 0
        self.ids: dict[tuple[str, str], object] = {}

    async def load(self):
        from sqlalchemy import text

        self.schema = await hp.HotelPurgeService(self.s).schema()
        rows = (
            await self.s.execute(
                text(
                    "SELECT table_name, column_name, data_type, is_nullable, column_default, "
                    "character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema='public' ORDER BY ordinal_position"
                )
            )
        ).all()
        self.columns: dict[str, list] = {}
        for r in rows:
            self.columns.setdefault(r[0], []).append(r)

    def order(self):
        """Ota-onalar oldin."""
        done, out = set(), []

        def visit(t):
            if t in done:
                return
            done.add(t)
            for fk in self.schema.by_child.get(t, []):
                if fk.parent != t:
                    visit(fk.parent)
            out.append(t)

        for t in sorted(self.schema.tables):
            visit(t)
        return out

    def token(self, table, col, limit):
        self.n += 1
        # Hisoblagich boshida — qisqa ustunlarda ham noyob qoladi
        value = f"{self.n}-{table[:8]}-{col[:8]}"
        return value[: limit or 255]

    async def insert(self, table, owner, overrides=None):
        """`owner` — mehmonxona kaliti ("A"/"B"/...) yoki "G" (global)."""
        from sqlalchemy import text

        fks = {fk.child_col: fk for fk in self.schema.by_child.get(table, [])}
        values = {}
        for _t, col, dtype, nullable, default, maxlen in self.columns[table]:
            if overrides and col in overrides:
                values[col] = overrides[col]
                continue
            if col == "hotel_id" and owner != "G":
                values[col] = self.ids[("hotels", owner)]
                continue
            if col in fks:
                fk = fks[col]
                if fk.parent == table:
                    continue  # o'z-o'ziga havola — bo'sh qoladi
                parent = self.ids.get((fk.parent, owner)) or self.ids.get((fk.parent, "G"))
                if parent is None and nullable == "YES":
                    continue
                values[col] = parent
                continue
            if col == "id" and dtype == "uuid":
                values[col] = uuid.uuid4()
                continue
            if nullable == "YES" or default is not None:
                continue
            values[col] = self.value(table, col, dtype, maxlen)
        names = ", ".join(f'"{c}"' for c in values)
        params = ", ".join(f":{c}" for c in values)
        returning = ' RETURNING "id"' if any(c[1] == "id" for c in self.columns[table]) else ""
        result = await self.s.execute(
            text(f'INSERT INTO "{table}" ({names}) VALUES ({params}){returning}'), values
        )
        new_id = result.scalar() if returning else None
        self.ids.setdefault((table, owner), new_id)
        return new_id

    def value(self, table, col, dtype, maxlen):
        today = date(2026, 10, 1)
        if col == "stars":
            return 3
        if col == "check_out_date":
            return today + timedelta(days=1)
        if dtype in ("integer", "smallint", "bigint", "numeric", "double precision", "real"):
            return 1
        if dtype == "boolean":
            return False
        if dtype == "date":
            return today
        if dtype.startswith("timestamp"):
            return datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        if dtype in ("json", "jsonb"):
            return "{}"
        if dtype == "bytea":
            return b"x"
        if dtype == "uuid":
            return uuid.uuid4()
        if dtype == "ARRAY":
            return []
        return self.token(table, col, maxlen)


async def seed(session):
    """Global kataloglar + A va B mehmonxonalari, har jadvalda kamida bitta qator."""
    seeder = Seeder(session)
    await seeder.load()
    order = seeder.order()
    owned = hp.owned_predicates(seeder.schema)
    for table in order:
        if table == "hotels":
            for key in ("A", "B"):
                await seeder.insert("hotels", key, {"status": "ACTIVE"})
            continue
        if table in owned:
            for key in ("A", "B"):
                await seeder.insert(table, key)
        else:
            await seeder.insert(table, "G")
    # A da ro'yxatga olingan mehmon B ning bronida ham — umumiy mehmon
    shared_guest = seeder.ids[("guests", "A")]
    b_res = await seeder.insert("reservations", "B", {"guest_id": shared_guest})
    await session.commit()
    return SimpleNamespace(
        A=seeder.ids[("hotels", "A")],
        B=seeder.ids[("hotels", "B")],
        shared_guest=shared_guest,
        b_reservation=b_res,
        seeder=seeder,
        owned=owned,
    )


async def wipe(session):
    from sqlalchemy import text

    tables = (
        await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND table_type='BASE TABLE' AND table_name <> 'alembic_version'"
            )
        )
    ).scalars().all()
    await session.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    await session.commit()


async def snapshot(session, tables):
    """Har jadval: qatorlarning md5 to'plami."""
    from sqlalchemy import text

    out = {}
    for t in sorted(tables):
        rows = (await session.execute(text(f'SELECT md5(t::text) FROM "{t}" t'))).scalars().all()
        out[t] = set(rows)
    return out


async def owned_rows(session, owned, hotel_id):
    from sqlalchemy import text

    out = {}
    for t, pred in owned.items():
        rows = (
            await session.execute(text(f'SELECT md5(t::text) FROM "{t}" t WHERE {pred}'), {"h": hotel_id})
        ).scalars().all()
        out[t] = set(rows)
    return out


class FakeMinio:
    def __init__(self, objects):
        self.objects = set(objects)
        self.removed = []

    def list_objects(self, bucket, prefix="", recursive=True):
        return [SimpleNamespace(object_name=p) for b, p in sorted(self.objects) if b == bucket and p.startswith(prefix)]

    def remove_object(self, bucket, path):
        self.removed.append((bucket, path))
        self.objects.discard((bucket, path))


# ----------------------------------------------------------- testlar --
def test_every_table_is_seeded_so_the_test_is_meaningful():
    async def body(s):
        await wipe(s)
        data = await seed(s)
        snap = await snapshot(s, data.seeder.schema.tables)
        empty = [t for t, rows in snap.items() if not rows]
        assert empty == [], f"to'ldirilmagan jadvallar: {empty}"
        mine = await owned_rows(s, data.owned, data.A)
        assert all(mine[t] for t in data.owned), [t for t in data.owned if not mine[t]]
    run(body)


def test_preview_changes_nothing_and_requires_a_stopped_hotel():
    async def body(s):
        from sqlalchemy import text

        await wipe(s)
        data = await seed(s)
        before = await snapshot(s, data.seeder.schema.tables)
        report = await hp.HotelPurgeService(s).preview(data.A)
        after = await snapshot(s, data.seeder.schema.tables)
        assert before == after
        assert report["can_purge"] is False and report["blocked_by"] == ["HOTEL_ACTIVE"]
        assert report["shared_guests"]["count"] == 1
        assert report["shared_guests"]["hotels"][0]["hotel_id"] == str(data.B)
        tables = {row["table"] for row in report["tables"]}
        assert {"reservations", "users", "shop_sale_items", "room_amenities", "user_sessions"} <= tables
        assert "amenities" not in tables and "permissions" not in tables  # global katalog

        await s.execute(text("UPDATE hotels SET status='INACTIVE' WHERE id=:h"), {"h": data.A})
        await s.commit()
        assert (await hp.HotelPurgeService(s).preview(data.A))["can_purge"] is True
    run(body)


def test_purge_removes_everything_of_the_hotel_and_nothing_else(monkeypatch):
    async def body(s):
        from sqlalchemy import text

        from app.infrastructure.storage import minio as storage

        await wipe(s)
        data = await seed(s)
        await s.execute(text("UPDATE hotels SET status='INACTIVE' WHERE id=:h"), {"h": data.A})
        # Fayllar: A ning yozuvlari, A prefiksidagi yetim obyekt, B niki
        await s.execute(
            text("UPDATE file_attachments SET minio_bucket='hotel-guests', minio_path=:p WHERE hotel_id=:h"),
            [{"p": f"{data.A}/guest/x/a.jpg", "h": data.A}, {"p": f"{data.B}/guest/y/b.jpg", "h": data.B}],
        )
        await s.commit()
        fake = FakeMinio({
            ("hotel-guests", f"{data.A}/guest/x/a.jpg"),
            ("hotel-documents", f"{data.A}/problem/1/orphan.jpg"),
            ("hotel-guests", f"{data.B}/guest/y/b.jpg"),
        })
        monkeypatch.setattr(storage, "get_minio_client", lambda: fake)

        tables = data.seeder.schema.tables
        before = await snapshot(s, tables)
        mine = await owned_rows(s, data.owned, data.A)

        report = await hp.HotelPurgeService(s).purge(data.A, actor="test")
        after = await snapshot(s, tables)

        # 1) A dan hech narsa qolmadi
        left = await owned_rows(s, data.owned, data.A)
        assert {t: len(r) for t, r in left.items() if r} == {}
        assert (await s.execute(text("SELECT count(*) FROM hotels WHERE id=:h"), {"h": data.A})).scalar() == 0

        # 2) Boshqa hamma qator baytma-bayt joyida — umumiy mehmondan tashqari
        shared_md5 = (
            await s.execute(text("SELECT md5(t::text) FROM guests t WHERE id=:g"), {"g": data.shared_guest})
        ).scalar()
        for t in tables:
            others_before = before[t] - mine.get(t, set())
            missing = others_before - after[t]
            extra = after[t] - others_before
            if t == "guests":
                assert extra == {shared_md5}, "faqat umumiy mehmon (B ga o'tgan) yangi ko'rinishda"
                assert len(missing) == 0
            else:
                assert missing == set() and extra == set(), t

        # 3) Umumiy mehmon B ga o'tdi, B ning broni unga qarab turibdi
        row = (
            await s.execute(
                text("SELECT g.hotel_id, r.guest_id FROM guests g JOIN reservations r ON r.guest_id = g.id WHERE r.id=:r"),
                {"r": data.b_reservation},
            )
        ).first()
        assert row[0] == data.B and row[1] == data.shared_guest
        assert report["guests_moved"] == 1 and report["deleted"]["hotels"] == 1
        # Hammasi o'chdi — umumiy mehmondan tashqari (u B ga o'tdi)
        assert report["total_rows"] == sum(len(v) for v in mine.values()) - 1

        # 4) MinIO: A niki o'chdi (yetim ham), B niki qoldi
        assert sorted(fake.removed) == sorted([
            ("hotel-documents", f"{data.A}/problem/1/orphan.jpg"),
            ("hotel-guests", f"{data.A}/guest/x/a.jpg"),
        ])
        assert ("hotel-guests", f"{data.B}/guest/y/b.jpg") in fake.objects
        assert report["files_removed"] == 2 and report["files_failed"] == 0
    run(body)


def test_an_active_hotel_cannot_be_purged():
    async def body(s):
        await wipe(s)
        data = await seed(s)
        before = await snapshot(s, data.seeder.schema.tables)
        with pytest.raises(ConflictException) as err:
            await hp.HotelPurgeService(s).purge(data.A)
        assert err.value.error_code == "HOTEL_ACTIVE"
        await s.rollback()
        assert await snapshot(s, data.seeder.schema.tables) == before
    run(body)


def test_another_hotel_depending_on_it_blocks_the_purge():
    async def body(s):
        from sqlalchemy import text

        await wipe(s)
        data = await seed(s)
        # B ning xarajati A ning xodimi nomidan (RESTRICT) — o'chirsak B buziladi
        a_user = data.seeder.ids[("users", "A")]
        await s.execute(text("UPDATE expenses SET created_by=:u WHERE hotel_id=:b"), {"u": a_user, "b": data.B})
        await s.execute(text("UPDATE hotels SET status='INACTIVE' WHERE id=:h"), {"h": data.A})
        await s.commit()
        before = await snapshot(s, data.seeder.schema.tables)

        preview = await hp.HotelPurgeService(s).preview(data.A)
        blocking = [c for c in preview["conflicts"] if c["blocking"]]
        assert preview["can_purge"] is False and "CROSS_HOTEL_REFERENCES" in preview["blocked_by"]
        assert {"table": "expenses", "column": "created_by", "references": "users"}.items() <= blocking[0].items()

        with pytest.raises(ConflictException) as err:
            await hp.HotelPurgeService(s).purge(data.A)
        assert err.value.error_code == "CROSS_HOTEL_REFERENCES"
        await s.rollback()
        assert await snapshot(s, data.seeder.schema.tables) == before
    run(body)


def test_system_accounts_are_kept_and_only_detached():
    """SUPER_ADMIN / sozlovchi A ga yozilgan bo'lsa ham o'chmaydi — ajratiladi."""
    async def body(s):
        from sqlalchemy import text

        await wipe(s)
        data = await seed(s)
        boss = await data.seeder.insert("users", "A", {"user_type": "SUPER_ADMIN"})
        cfg = await data.seeder.insert("users", "A", {"user_type": "CONFIGURATOR"})
        # Egasi B da ham ishlagan: B ning xarajati (RESTRICT) va sessiyasi
        await s.execute(text("UPDATE expenses SET created_by=:u WHERE hotel_id=:b"), {"u": boss, "b": data.B})
        session_id = await data.seeder.insert("user_sessions", "B", {"user_id": boss})
        await s.execute(text("UPDATE hotels SET status='INACTIVE' WHERE id=:h"), {"h": data.A})
        await s.commit()
        assert (await s.execute(text("SELECT branch_id FROM users WHERE id=:u"), {"u": boss})).scalar()

        preview = await hp.HotelPurgeService(s).preview(data.A)
        assert preview["can_purge"] is True, preview["blocked_by"]
        assert {u["id"] for u in preview["system_users"]} == {str(boss), str(cfg)}
        # Ko'rib chiqish hech narsani o'zgartirmagan
        assert (await s.execute(text("SELECT hotel_id FROM users WHERE id=:u"), {"u": boss})).scalar() == data.A

        report = await hp.HotelPurgeService(s).purge(data.A)
        assert {u["user_type"] for u in report["system_users_kept"]} == {"SUPER_ADMIN", "CONFIGURATOR"}
        rows = (
            await s.execute(text("SELECT id, hotel_id, branch_id FROM users WHERE id IN (:a, :b)"), {"a": boss, "b": cfg})
        ).all()
        assert len(rows) == 2 and all(r[1] is None and r[2] is None for r in rows)
        # B ning ma'lumoti joyida
        assert (await s.execute(text("SELECT count(*) FROM expenses WHERE hotel_id=:b AND created_by=:u"), {"b": data.B, "u": boss})).scalar() >= 1
        assert (await s.execute(text("SELECT count(*) FROM user_sessions WHERE id=:i"), {"i": session_id})).scalar() == 1
        # A ning oddiy xodimi va boshqa hamma narsasi o'chdi
        left = await owned_rows(s, data.owned, data.A)
        assert {t: len(r) for t, r in left.items() if r} == {}
        assert (await s.execute(text("SELECT count(*) FROM users WHERE id=:u"), {"u": data.seeder.ids[("users", "A")]})).scalar() == 0
    run(body)

def test_panel_endpoints_end_to_end():
    """Panel marshruti orqali: ko'rib chiqish → noto'g'ri kod → o'chirish."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import get_db
    from app.core.exceptions import AppException
    from app.superadmin import router as panel
    from app.superadmin import security

    async def prepare(s):
        await wipe(s)
        data = await seed(s)
        await s.execute(text("UPDATE hotels SET status='INACTIVE' WHERE id=:h"), {"h": data.A})
        await s.commit()
        code = (await s.execute(text("SELECT code FROM hotels WHERE id=:h"), {"h": data.A})).scalar()
        return data, code, await snapshot(s, data.seeder.schema.tables)

    data, code, before = run(prepare)
    tables = data.seeder.schema.tables
    engine = create_async_engine(URL, poolclass=NullPool)

    async def test_db():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    root = SimpleNamespace(id=uuid.uuid4(), is_root=True, label=None, password_hash=security.hash_password("p@ss"))
    api = FastAPI()
    api.include_router(panel.router)
    api.dependency_overrides[get_db] = test_db
    api.dependency_overrides[panel.current_panel_user] = lambda: root

    @api.exception_handler(AppException)
    async def handler(request: Request, exc: AppException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "error_code": exc.error_code})

    with TestClient(api) as client:
        preview = client.get(f"/hotels/{data.A}/purge-preview")
        assert preview.status_code == 200, preview.text
        assert preview.json()["can_purge"] is True and preview.json()["shared_guests"]["count"] == 1
        assert run(lambda s: snapshot(s, tables)) == before

        wrong = client.post(f"/hotels/{data.A}/purge", json={"confirm_code": "XATO", "password": "p@ss"})
        assert wrong.status_code == 422 and wrong.json()["error_code"] == "CONFIRM_CODE_MISMATCH"
        bad_pw = client.post(f"/hotels/{data.A}/purge", json={"confirm_code": code, "password": "xato"})
        assert bad_pw.status_code == 403 and bad_pw.json()["error_code"] == "WRONG_PASSWORD"
        assert run(lambda s: snapshot(s, tables)) == before

        done = client.post(f"/hotels/{data.A}/purge", json={"confirm_code": code, "password": "p@ss"})
        assert done.status_code == 200, done.text
        assert done.json()["deleted"]["hotels"] == 1 and done.json()["guests_moved"] == 1

        gone = client.get(f"/hotels/{data.A}/purge-preview")
        assert gone.status_code == 404

    async def check(s):
        left = await owned_rows(s, data.owned, data.A)
        assert {t: len(r) for t, r in left.items() if r} == {}
        b_left = await owned_rows(s, data.owned, data.B)
        assert all(b_left[t] for t in data.owned)

    run(check)
    asyncio.run(engine.dispose())
