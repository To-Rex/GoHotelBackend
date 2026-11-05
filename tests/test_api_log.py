#!/usr/bin/env python3
"""So'rovlar jurnali (panel) — halqa bufer va middleware.

Ishga tushirish:  python tests/test_api_log.py

Tekshirilayotgani: sirlarning niqoblanishi, 500 talik chegara, filtrlash,
va eng nozigi — middleware so'rov tanasini o'qigach endpoint uni QAYTA
o'qiy olishi (oqim tiklanishi). Fayl yuklash tanasi jurnaldan chetda.
Xatolar (4xx/5xx) bazada 3 kun: yozish, o'qish, muddati o'tganini
o'chirish, jonli jurnal bilan takrorsiz birlashish, traceback.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.superadmin import api_log  # noqa: E402

# Middleware sinovlarida baza yo'q — fon yozuvi o'chirilgan; bazaga yozish
# pastda `persist` bilan to'g'ridan-to'g'ri sinaladi
api_log.persist_enabled = False

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {str(got)[:40]}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


print("Niqoblash:")
masked = api_log.redact('{"email": "a@b.c", "password": "Sir123!", "token": "abc"}')
check("parol niqoblandi", '"password": "***"' in masked, True)
check("token niqoblandi", '"token": "***"' in masked, True)
check("oddiy maydon tegilmadi", '"a@b.c"' in masked, True)

print("Chegara va filtrlar:")
api_log.clear()
for i in range(600):
    api_log._record(
        method="GET" if i % 2 == 0 else "POST",
        path=f"/api/v1/test/{i}",
        status=200 if i % 3 else 404,
        duration_ms=float(i % 10 + 1),
        ip="127.0.0.1",
        request_body=None,
        response_body=None,
        hotel_id="h1" if i % 2 == 0 else "h2",
        branch_id="b1",
        user_id="u1",
        user_type="EMPLOYEE",
        user_agent="test-agent",
    )
snap = api_log.snapshot()
check("600 tadan 500 tasi qoldi", len(snap["items"]), 500)
check("jami hisoblagich", snap["captured_total"] >= 600, True)
check("eng yangisi birinchi", snap["items"][0]["path"], "/api/v1/test/599")
check(
    "metod filtri",
    all(r["method"] == "POST" for r in api_log.snapshot(method="post")["items"]),
    True,
)
check(
    "holat sinfi 4xx",
    all(400 <= r["status"] < 500 for r in api_log.snapshot(status="4xx")["items"]),
    True,
)
check(
    "aniq holat 404",
    all(r["status"] == 404 for r in api_log.snapshot(status="404")["items"]),
    True,
)
check(
    "url qidiruvi",
    all("/test/59" in r["path"] for r in api_log.snapshot(q="/test/59")["items"]),
    True,
)
check("limit", len(api_log.snapshot(limit=7)["items"]), 7)
check(
    "kompaniya filtri",
    all(r["hotel_id"] == "h1" for r in api_log.snapshot(hotel_id="h1")["items"]),
    True,
)
durations = [
    r["duration_ms"] for r in api_log.snapshot(sort="duration_ms", order="asc")["items"]
]
check("davomiylik bo'yicha asc", durations == sorted(durations), True)
statuses = [
    r["status"] for r in api_log.snapshot(sort="status", order="desc")["items"]
]
check("holat bo'yicha desc", statuses == sorted(statuses, reverse=True), True)

print("Yozib olinmaydiganlar:")
check("OPTIONS", api_log._should_capture("OPTIONS", "/api/v1/rooms"), False)
check(
    "jurnalning o'zi",
    api_log._should_capture("GET", "/api/v1/superadmin/api-logs"),
    False,
)
check("hujjatlar", api_log._should_capture("GET", "/docs"), False)
check("oddiy so'rov", api_log._should_capture("POST", "/api/v1/auth/login"), True)

print("Middleware (sinov ilovasi bilan):")
test_app = FastAPI()
test_app.middleware("http")(api_log.middleware)


@test_app.post("/echo")
async def echo(request: Request):
    # Tana middleware'dan KEYIN ham o'qilishi shart — oqim tiklanganini
    # aynan shu isbotlaydi
    data = await request.json()
    return {"echo": data.get("value"), "token": "javob-siri"}


@test_app.post("/upload")
async def upload(request: Request):
    raw = await request.body()
    return {"size": len(raw)}


@test_app.get("/stream")
async def stream():
    async def gen():
        yield b"katta oqim"

    return StreamingResponse(gen(), media_type="application/octet-stream")


@test_app.get("/ctx")
async def ctx(request: Request):
    request.state.api_log_context = {
        "hotel_id": "hotel-1",
        "branch_id": "branch-1",
        "user_id": "user-1",
        "user_type": "EMPLOYEE",
    }
    return {"ok": True}


@test_app.get("/boom")
async def boom():
    raise ValueError("portladi")


api_log.clear()
with TestClient(test_app) as client:
    r = client.post("/echo", json={"value": 42, "password": "Sir!"})
    check("endpoint tanani qayta o'qidi", r.json()["echo"], 42)
    entry = api_log.snapshot(q="/echo")["items"][0]
    check("holat yozildi", entry["status"], 200)
    # JSON ixcham keladi — bo'shliqsiz solishtiramiz
    req = (entry["request_body"] or "").replace(" ", "")
    resp = (entry["response_body"] or "").replace(" ", "")
    check("so'rov tanasi bor", '"value":42' in req, True)
    check("so'rovdagi parol niqoblangan", '"password":"***"' in req, True)
    check("javob tanasi yozildi", '"echo":42' in resp, True)
    check("javobdagi sir niqoblangan", '"token":"***"' in resp, True)
    check("davomiylik musbat", entry["duration_ms"] >= 0, True)

    r = client.post(
        "/upload",
        content=b"x" * 1000,
        headers={"content-type": "application/octet-stream"},
    )
    check("yuklash ishladi", r.json()["size"], 1000)
    entry = api_log.snapshot(q="/upload")["items"][0]
    check(
        "fayl tanasi yozilmadi",
        "tana yozilmaydi" in (entry["request_body"] or ""),
        True,
    )

    r = client.get("/stream")
    check("oqim javobi buzilmadi", r.content, b"katta oqim")
    entry = api_log.snapshot(q="/stream")["items"][0]
    check(
        "oqim tanasi o'qilmadi",
        (entry["response_body"] or "").startswith("<application/octet-stream"),
        True,
    )

    client.get("/ctx")
    entry = api_log.snapshot(q="/ctx")["items"][0]
    check("kontekst: hotel", entry["hotel_id"], "hotel-1")
    check("kontekst: filial", entry["branch_id"], "branch-1")
    check("kontekst: user", entry["user_id"], "user-1")
    check("kontekst: tur", entry["user_type"], "EMPLOYEE")
    check(
        "kontekst: hotel filtri",
        all(
            r["hotel_id"] == "hotel-1"
            for r in api_log.snapshot(hotel_id="hotel-1")["items"]
        ),
        True,
    )

api_log.clear()
check("tozalash", len(api_log.snapshot()["items"]), 0)

print("Ishlov berilmagan xato:")
with TestClient(test_app, raise_server_exceptions=False) as client:
    r = client.get("/boom")
    check("mijozga 500", r.status_code, 500)
    entry = api_log.snapshot(q="/boom")["items"][0]
    check("jurnalda 500", entry["status"], 500)
    check("javob — umumiy ishlovchi matni", "Internal server error" in (entry["response_body"] or ""), True)
    check("traceback yozildi", "ValueError: portladi" in (entry["error"] or ""), True)
    check("yozuv kaliti — uuid", len(entry["id"]), 32)
    check("xato bazaga yoziladi (qoida)", api_log._should_persist({**entry, "status": 500}) or not api_log.persist_enabled, True)

print("Xatolar bazada (3 kun):")
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database.models.api_error_log import ApiErrorLog


def _entry(status, path, **extra):
    return api_log._record(
        method="POST", path=path, status=status, duration_ms=12.5, ip="10.0.0.1",
        request_body='{"room_id": "r1"}', response_body='{"detail": "x"}',
        hotel_id="h1", branch_id="b1", user_id="u1", user_type="ADMIN",
        user_agent="test", **extra,
    )


async def _db_checks():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(ApiErrorLog.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    api_log.persist_enabled = True
    check("200 bazaga yozilmaydi", api_log._should_persist(_entry(200, "/ok")), False)
    check("404 bazaga yoziladi", api_log._should_persist(_entry(404, "/yoq")), True)
    check("500 bazaga yoziladi", api_log._should_persist(_entry(500, "/xato")), True)

    api_log.clear()
    fresh = _entry(500, "/api/v1/reservations/", error="Traceback ...\nValueError: x")
    await api_log.persist(fresh, factory)
    old = {**_entry(404, "/api/v1/eski"), "ts": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()}
    await api_log.persist(old, factory)

    async with factory() as s:
        rows = await api_log.stored_errors(s)
        total = (await s.execute(select(func.count()).select_from(ApiErrorLog))).scalar()
    check("ikkala yozuv bazada", total, 2)
    check("muddati ichidagisi o'qiladi", [r["id"] for r in rows], [fresh["id"]])
    check("saqlangan belgisi", rows[0]["stored"], True)
    check("traceback saqlangan", "ValueError" in (rows[0]["error"] or ""), True)
    check("kontekst saqlangan", (rows[0]["hotel_id"], rows[0]["user_type"]), ("h1", "ADMIN"))
    check("ts zonali", rows[0]["ts"].endswith("+00:00"), True)

    async with factory() as s:
        check("filtr: holat 5xx", len(await api_log.stored_errors(s, status="5xx")), 1)
        check("filtr: holat 404 — muddati o'tgan ko'rinmaydi", len(await api_log.stored_errors(s, status="404")), 0)
        check("filtr: url", len(await api_log.stored_errors(s, q="RESERV")), 1)
        check("filtr: kompaniya", len(await api_log.stored_errors(s, hotel_id="h2")), 0)
        check("filtr: metod", len(await api_log.stored_errors(s, method="get")), 0)

    async with factory() as s:
        await api_log.purge(s)
        await s.commit()
        total = (await s.execute(select(func.count()).select_from(ApiErrorLog))).scalar()
    check("3 kundan eskisi o'chirildi", total, 1)

    # Jonli jurnal bilan birlashish
    api_log.clear()
    snap = api_log.snapshot(extra=rows)
    check("xotira bo'sh — saqlangan xato baribir ko'rinadi", [r["id"] for r in snap["items"]], [fresh["id"]])
    check("stored_errors / retention_days", (snap["stored_errors"], snap["retention_days"]), (1, 3))
    api_log._entries.append(fresh)
    snap = api_log.snapshot(extra=rows)
    check("xotirada ham bor — takrorlanmaydi", len(snap["items"]), 1)
    check("xotiradagi nusxa ustun (stored=False)", snap["items"][0]["stored"], False)
    newer = _entry(200, "/api/v1/rooms")
    snap = api_log.snapshot(extra=rows)
    check("tartib — eng yangisi birinchi", snap["items"][0]["id"], newer["id"])
    check("filtr saqlanganga ham: 5xx", [r["id"] for r in api_log.snapshot(status="5xx", extra=rows)["items"]], [fresh["id"]])
    api_log.persist_enabled = False
    await engine.dispose()


asyncio.run(_db_checks())

print()
print(f"Jami: {ok} OK, {fail} XATO")
sys.exit(1 if fail else 0)
