#!/usr/bin/env python3
"""So'rovlar jurnali (panel) — halqa bufer va middleware.

Ishga tushirish:  python tests/test_api_log.py

Tekshirilayotgani: sirlarning niqoblanishi, 500 talik chegara, filtrlash,
va eng nozigi — middleware so'rov tanasini o'qigach endpoint uni QAYTA
o'qiy olishi (oqim tiklanishi). Fayl yuklash tanasi jurnaldan chetda.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.superadmin import api_log  # noqa: E402

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
        duration_ms=1.0,
        ip="127.0.0.1",
        request_body=None,
        response_body=None,
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

api_log.clear()
check("tozalash", len(api_log.snapshot()["items"]), 0)

print()
print(f"Jami: {ok} OK, {fail} XATO")
sys.exit(1 if fail else 0)
