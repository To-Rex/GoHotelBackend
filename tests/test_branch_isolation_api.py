"""Filiallar to'liq ajratilgani — haqiqiy PostgreSQL va haqiqiy API orqali.

Bitta mehmonxonaning ikki filiali (A1 — asosiy, A2) va boshqa mehmonxona (B)
har bir jadvalda ma'lumot bilan to'ldiriladi. Keyin:

* A1 xodimi BARCHA ro'yxat endpointlarini (GET, parametrsiz — 80+ ta)
  chaqiradi: javobda A2 yoki B ning birorta yozuv ID'si bo'lmasligi kerak;
  A2 xodimi uchun ham teskarisi. Birorta endpoint 500 bermasligi kerak.
* Administrator standart holatda asosiy filialni ko'radi; POST /auth/context
  bilan A2 ni tanlagach — faqat A2 ni; boshqa mehmonxona filialini tanlay
  olmaydi; oddiy xodim umuman tanlay olmaydi.
* Boshqa filial yozuvini id bilan ochish/o'zgartirish — 404; boshqa filialga
  yozish — 403 BRANCH_SCOPE.
* Sozlamalar filialniki: A2 da o'zgargan sozlama A1 ga ta'sir qilmaydi.

Faqat lokal test bazasida: GOHOTEL_TEST_PG_URL.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, timedelta

import pytest

from tests.test_hotel_purge import Seeder, run, wipe

URL = os.environ.get("GOHOTEL_TEST_PG_URL", "")
pytestmark = pytest.mark.skipif(
    not URL or "test" not in URL.rsplit("/", 1)[-1] or not any(h in URL for h in ("127.0.0.1", "localhost")),
    reason="GOHOTEL_TEST_PG_URL (lokal test bazasi) berilmagan",
)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TODAY = date.today()

#: Holat ustunlari uchun haqiqiy qiymatlar (javob sxemalari enum/Literal kutadi)
VALID = {
    ("reservations", "status"): "CONFIRMED",
    ("reservations", "booking_type"): "DAILY",
    ("reservations", "payment_status"): "UNPAID",
    ("reservations", "daily_unit"): "12h",
    ("reservations", "check_in_date"): TODAY,
    ("reservations", "check_out_date"): TODAY + timedelta(days=1),
    ("rooms", "current_status"): "AVAILABLE",
    ("hotels", "status"): "ACTIVE",
    ("branches", "status"): "ACTIVE",
    ("users", "status"): "ACTIVE",
    ("users", "user_type"): "EMPLOYEE",
    ("invoices", "status"): "ISSUED",
    ("invoice_line_items", "line_type"): "ROOM_CHARGE",
    ("payments", "payment_method"): "CASH",
    ("payments", "status"): "COMPLETED",
    ("housekeeping_tasks", "status"): "OPEN",
    ("housekeeping_tasks", "task_type"): "CLEANING",
    ("housekeeping_tasks", "priority"): "MEDIUM",
    ("checklist_templates", "task_type"): "CLEANING",
    ("problems", "status"): "OPEN",
    ("problems", "category"): "Boshqa",
    ("guest_feedback", "status"): "NEW",
    ("guest_feedback", "feedback_type"): "COMPLAINT",
    ("guest_feedback", "priority"): "MEDIUM",
    ("shift_sessions", "status"): "CLOSED",
    ("journal_entries", "status"): "POSTED",
    ("ledgers", "type"): "ASSET",
    ("shop_sales", "status"): "PAID",
    ("shop_sales", "payment_method"): "CASH",
    ("shop_writeoffs", "kind"): "WRITEOFF",
    ("staff_messages", "status"): "OPEN",
    ("trusted_devices", "status"): "APPROVED",
    ("face_sightings", "status"): "unknown",
    ("expenses", "payment_method"): "CASH",
    ("document_scans", "document_type"): "PASSPORT",
    ("reservation_penalties", "kind"): "LATE_CHECKOUT",
    ("vision_devices", "status"): "ACTIVE",
}

#: Ro'yxat endpointlari — so'rov parametrlari bilan (sana oralig'i va h.k.)
EXTRA_PARAMS = {
    "/api/v1/finance/summary": {"date_from": str(TODAY - timedelta(days=30)), "date_to": str(TODAY + timedelta(days=30))},
    "/api/v1/finance/daily": {"date_from": str(TODAY - timedelta(days=7)), "date_to": str(TODAY + timedelta(days=7))},
    "/api/v1/finance/by-staff": {"date_from": str(TODAY - timedelta(days=30)), "date_to": str(TODAY + timedelta(days=30))},
    "/api/v1/reservations/calendar": {"start_date": str(TODAY - timedelta(days=7)), "end_date": str(TODAY + timedelta(days=7))},
    "/api/v1/reservations/availability": {"check_in": str(TODAY), "check_out": str(TODAY + timedelta(days=1))},
    "/api/v1/rooms/available": {"check_in": str(TODAY), "check_out": str(TODAY + timedelta(days=1))},
    "/api/v1/expenses/": {"date_from": str(TODAY - timedelta(days=30)), "date_to": str(TODAY + timedelta(days=30))},
    "/api/v1/reception/bookings": {"date": str(TODAY)},
}

#: Shaxsiy yoki hamma uchun umumiy endpointlar (filialga aloqasi yo'q)
SKIP = {"/health", "/api/v1/auth/webauthn/register/options"}


async def seed(session) -> dict:
    """H mehmonxonasi (A1, A2 filiallari) va B mehmonxonasi — har jadvalda."""
    from sqlalchemy import text

    sd = Seeder(session)
    await sd.load()

    real_value = sd.value

    def value(table, col, dtype, maxlen):
        if (table, col) in VALID:
            return VALID[(table, col)]
        return real_value(table, col, dtype, maxlen)

    sd.value = value
    owned = __import__("app.superadmin.hotel_purge_service", fromlist=["x"]).owned_predicates(sd.schema)

    H = await sd.insert("hotels", "A1", {"status": "ACTIVE", "settings": json.dumps({"booking": {"daily_unit": "12h"}})})
    sd.ids[("hotels", "A2")] = H
    B = await sd.insert("hotels", "B", {"status": "ACTIVE"})
    A1 = await sd.insert("branches", "A1", {"is_main_branch": True, "name": "Markaz", "status": "ACTIVE"})
    A2 = await sd.insert("branches", "A2", {"is_main_branch": False, "name": "Chilonzor", "status": "ACTIVE"})
    await sd.insert("branches", "B", {"is_main_branch": True, "status": "ACTIVE"})
    await session.execute(
        text("UPDATE branches SET settings = h.settings FROM hotels h WHERE h.id = branches.hotel_id")
    )
    # Administrator — mehmonxona darajasida (birinchi `users` qatori: Seeder
    # boshqa jadvallarning created_by sini A1 xodimiga ulaydi)
    e1 = await sd.insert("users", "A1", {"user_type": "EMPLOYEE", "branch_id": A1})
    e2 = await sd.insert("users", "A2", {"user_type": "EMPLOYEE", "branch_id": A2})
    await sd.insert("users", "B", {"user_type": "EMPLOYEE"})
    admin = await sd.insert("users", "G", {"user_type": "ADMIN", "hotel_id": H, "branch_id": A1})
    configurator = await sd.insert("users", "G", {"user_type": "CONFIGURATOR", "hotel_id": None, "branch_id": None})

    skip = {"hotels", "branches", "users"}
    for table in sd.order():
        if table in skip:
            continue
        if table in owned:
            for key in ("A1", "A2", "B"):
                await sd.insert(table, key)
        elif table not in sd.ids_tables():
            await sd.insert(table, "G")
    # Sessiyalar: sozlovchi va administrator tokeni tirik sessiyaga bog'langan
    for uid, jti in ((admin, "jti-admin"), (configurator, "jti-config"), (e1, "jti-e1"), (e2, "jti-e2")):
        await session.execute(
            text(
                "INSERT INTO user_sessions (id, user_id, token_jti, refresh_token_hash, expires_at, created_at) "
                "VALUES (gen_random_uuid(), :u, :j, 'x', now() + interval '1 day', now())"
            ),
            {"u": uid, "j": jti},
        )
    await session.commit()

    # Har filial/mehmonxona yozuvlarining ID'lari (javoblarda qidiriladi)
    rows: dict[str, set[str]] = {"A1": set(), "A2": set(), "B": set()}
    for table in owned:
        if table in ("hotels",):
            continue
        has_branch = (
            await session.execute(
                text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name='branch_id'"),
                {"t": table},
            )
        ).first()
        has_id = (
            await session.execute(
                text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name='id'"),
                {"t": table},
            )
        ).first()
        if not has_id or not has_branch:
            continue
        for key, branch in (("A1", A1), ("A2", A2)):
            ids = (await session.execute(text(f'SELECT id::text FROM "{table}" WHERE branch_id = :b'), {"b": branch})).scalars().all()
            rows[key] |= set(ids)
        ids = (await session.execute(text(f'SELECT id::text FROM "{table}" WHERE hotel_id = :h'), {"h": B})).scalars().all()
        rows["B"] |= set(ids)
    rows["A1"].discard(str(admin))
    return dict(H=H, B=B, A1=A1, A2=A2, e1=e1, e2=e2, admin=admin, configurator=configurator, rows=rows)


def _ids_tables(self):
    return {t for (t, _k) in self.ids}


Seeder.ids_tables = _ids_tables


@pytest.fixture(scope="module")
def world():
    async def body(s):
        await wipe(s)
        return await seed(s)

    return run(body)


@pytest.fixture(scope="module")
def client(world):
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import get_db
    from app.main import app

    engine = create_async_engine(URL, poolclass=NullPool)

    async def test_db():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = test_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)
        asyncio.run(engine.dispose())


ALL_PERMISSIONS = None


def permissions() -> list[str]:
    """Koddagi barcha `require_permission("...")` va `can("...")` kodlari —
    xodimga hamma ruxsat (filial chegarasi ruxsatdan qat'i nazar ishlaydi)."""
    global ALL_PERMISSIONS
    if ALL_PERMISSIONS is None:
        from pathlib import Path

        codes = set()
        for path in (Path(__file__).resolve().parents[1] / "app").rglob("*.py"):
            codes |= set(re.findall(r'"([a-z_]+\.[a-z_]+(?:\.[a-z_]+)?)"', path.read_text(encoding="utf-8")))
        ALL_PERMISSIONS = sorted(c for c in codes if not c.endswith((".py", ".json")))
    return ALL_PERMISSIONS


def token(user_id, user_type, hotel_id, branch_id=None, jti="j", perms=None) -> dict:
    from app.infrastructure.auth.jwt import create_access_token

    tok = create_access_token(
        {
            "sub": str(user_id),
            "user_type": user_type,
            "hotel_id": str(hotel_id) if hotel_id else None,
            "branch_id": str(branch_id) if branch_id else None,
            "permissions": perms if perms is not None else (permissions() if user_type == "EMPLOYEE" else []),
            "jti": jti,
            "device_id": None,
        }
    )
    return {"Authorization": f"Bearer {tok}"}


def list_paths(client) -> list[str]:
    paths = client.app.openapi()["paths"]
    return sorted(p for p, ops in paths.items() if "get" in ops and "{" not in p and "superadmin" not in p and p not in SKIP)


def crawl(client, headers) -> tuple[dict[str, int], str]:
    statuses, bodies = {}, []
    for path in list_paths(client):
        r = client.get(path, headers=headers, params=EXTRA_PARAMS.get(path))
        statuses[path] = r.status_code
        if r.status_code == 200:
            bodies.append(r.text)
    return statuses, "\n".join(bodies)


def leaks(text_: str, foreign: set[str]) -> set[str]:
    return set(UUID_RE.findall(text_)) & foreign


# ------------------------------------------------------------------ testlar --
def test_employee_of_branch_sees_only_own_branch(world, client):
    for me, other in (("A1", "A2"), ("A2", "A1")):
        user = world["e1"] if me == "A1" else world["e2"]
        statuses, body = crawl(client, token(user, "EMPLOYEE", world["H"], jti=f"jti-{'e1' if me == 'A1' else 'e2'}"))
        broken = {p: s for p, s in statuses.items() if s >= 500}
        assert broken == {}, broken
        foreign = world["rows"][other] | world["rows"]["B"]
        assert leaks(body, foreign) == set(), f"{me} xodimi {other}/B yozuvlarini ko'rdi"
        # O'z filiali ma'lumoti ko'rinadi (test bo'sh emas)
        assert len(leaks(body, world["rows"][me])) > 20, me


def test_admin_sees_main_branch_then_switches(world, client):
    admin = token(world["admin"], "ADMIN", world["H"], world["A1"], jti="jti-admin")
    statuses, body = crawl(client, admin)
    assert {p: s for p, s in statuses.items() if s >= 500} == {}
    assert leaks(body, world["rows"]["A2"] | world["rows"]["B"]) == set()
    assert len(leaks(body, world["rows"]["A1"])) > 20

    # Variantlar: faqat o'z mehmonxonasi va uning filiallari
    opts = client.get("/api/v1/auth/context/options", headers=admin).json()["hotels"]
    assert [h["id"] for h in opts] == [str(world["H"])]
    assert {b["id"] for b in opts[0]["branches"]} == {str(world["A1"]), str(world["A2"])}

    # Boshqa mehmonxona filiali — rad
    bad = client.post("/api/v1/auth/context", headers=admin, json={"hotel_id": str(world["B"]), "branch_id": None})
    assert bad.status_code == 403 and bad.json()["error_code"] == "CONTEXT_FORBIDDEN"

    switched = client.post(
        "/api/v1/auth/context", headers=admin, json={"hotel_id": str(world["H"]), "branch_id": str(world["A2"])}
    )
    assert switched.status_code == 200, switched.text
    new = {"Authorization": f"Bearer {switched.json()['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=new).json()
    assert me["branch_id"] == str(world["A2"]) and me["branch_name"] == "Chilonzor"
    statuses, body = crawl(client, new)
    assert {p: s for p, s in statuses.items() if s >= 500} == {}
    assert leaks(body, world["rows"]["A1"] | world["rows"]["B"]) == set()
    assert len(leaks(body, world["rows"]["A2"])) > 20

    # Yangilangan token ham tanlovni saqlaydi
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": switched.json()["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    again = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=again).json()["branch_id"] == str(world["A2"])


def test_employee_cannot_switch_branch(world, client):
    e1 = token(world["e1"], "EMPLOYEE", world["H"], jti="jti-e1")
    r = client.post("/api/v1/auth/context", headers=e1, json={"hotel_id": str(world["H"]), "branch_id": str(world["A2"])})
    assert r.status_code == 403
    # Tokenga boshqa filial yozib qo'yilsa ham xodim o'z filialida qoladi
    forged = token(world["e1"], "EMPLOYEE", world["H"], world["A2"], jti="jti-e1")
    assert client.get("/api/v1/auth/me", headers=forged).json()["branch_id"] == str(world["A1"])


def test_other_branch_records_are_not_reachable_by_id(world, client):
    from sqlalchemy import text

    async def a2_ids(s):
        out = {}
        for table in ("rooms", "reservations", "guests", "floors", "expenses"):
            out[table] = str((await s.execute(text(f'SELECT id FROM "{table}" WHERE branch_id = :b LIMIT 1'), {"b": world["A2"]})).scalar())
        return out

    ids = run(a2_ids)
    e1 = token(world["e1"], "EMPLOYEE", world["H"], jti="jti-e1")
    for path in (f"/api/v1/rooms/{ids['rooms']}", f"/api/v1/reservations/{ids['reservations']}",
                 f"/api/v1/guests/{ids['guests']}"):
        r = client.get(path, headers=e1)
        assert r.status_code == 404, (path, r.status_code, r.text[:200])
    # Boshqa filial xodimini ochish ham — 404
    assert client.get(f"/api/v1/employees/{world['e2']}", headers=e1).status_code == 404


def test_writing_into_another_branch_is_refused(world, client):
    from sqlalchemy import text

    async def a2_floor(s):
        floor = (await s.execute(text("SELECT id FROM floors WHERE branch_id = :b LIMIT 1"), {"b": world["A2"]})).scalar()
        rt = (await s.execute(text("SELECT id FROM room_types WHERE branch_id = :b LIMIT 1"), {"b": world["A2"]})).scalar()
        return str(floor), str(rt)

    floor, rt = run(a2_floor)
    e1 = token(world["e1"], "EMPLOYEE", world["H"], jti="jti-e1")
    r = client.post(
        "/api/v1/rooms/",
        headers=e1,
        json={
            "hotel_id": str(world["H"]), "branch_id": str(world["A2"]), "floor_id": floor,
            "room_type_id": rt, "room_number": "X-999", "current_status": "AVAILABLE",
        },
    )
    # Boshqa filial qavatini ko'rmaydi (404) yoki filialga yozish rad (403)
    assert r.status_code in (403, 404), r.text
    if r.status_code == 403:
        assert r.json().get("error_code") == "BRANCH_SCOPE", r.text

    async def created(s):
        return (await s.execute(text("SELECT count(*) FROM rooms WHERE room_number = 'X-999'"))).scalar()

    assert run(created) == 0


def test_settings_are_per_branch(world, client):
    a2_conf = token(world["configurator"], "CONFIGURATOR", world["H"], world["A2"], jti="jti-config")
    r = client.put("/api/v1/hotels/debt-settings", headers=a2_conf, json={"enabled": False, "interval_minutes": 60})
    assert r.status_code == 200, r.text
    e1 = token(world["e1"], "EMPLOYEE", world["H"], jti="jti-e1")
    e2 = token(world["e2"], "EMPLOYEE", world["H"], jti="jti-e2")
    assert client.get("/api/v1/hotels/debt-settings", headers=e2).json()["enabled"] is False
    assert client.get("/api/v1/hotels/debt-settings", headers=e1).json()["enabled"] is True


def test_new_records_land_in_the_request_branch(world, client):
    from sqlalchemy import text

    e2 = token(world["e2"], "EMPLOYEE", world["H"], jti="jti-e2")
    r = client.post(
        "/api/v1/guests/",
        headers=e2,
        json={"hotel_id": str(world["H"]), "first_name": "Yangi", "last_name": "Mehmon", "phone": "+998900000000"},
    )
    assert r.status_code in (200, 201), r.text

    async def where(s):
        return (await s.execute(text("SELECT branch_id FROM guests WHERE first_name='Yangi' AND last_name='Mehmon'"))).scalar()

    assert run(where) == world["A2"]
    e1 = token(world["e1"], "EMPLOYEE", world["H"], jti="jti-e1")
    assert "Yangi" not in client.get("/api/v1/guests/", headers=e1, params={"search": "Yangi"}).text
