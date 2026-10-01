#!/usr/bin/env python3
"""Xabarchi SMS servisi — sof mantiq.

Ishga tushirish:  python tests/test_sms_service.py

Tekshirilayotgani: telefon normallashuvi (SMS faqat to'g'ri raqamga),
kalit shifrlash aylanishi (bazada ochiq matn yotmasligi), niqoblash va
hodisa matnlarining kalitsiz/telefonsiz jim o'tishi.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.infrastructure.database.models  # noqa: E402,F401
from app.application.services import sms_service  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {str(got)[:40]}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


print("Telefon raqamini normallashtirish:")
check("9 xonali mahalliy", sms_service.normalize_phone("901234567"), "+998901234567")
check("bo'shliqlar bilan", sms_service.normalize_phone("90 123 45 67"), "+998901234567")
check("998 bilan", sms_service.normalize_phone("998901234567"), "+998901234567")
check("+998 bilan", sms_service.normalize_phone("+998 90 123-45-67"), "+998901234567")
check("qisqa raqam rad", sms_service.normalize_phone("12345"), None)
check("chet el raqami rad", sms_service.normalize_phone("+79161234567"), None)
check("bo'sh qiymat", sms_service.normalize_phone(None), None)

print("Kalit shifrlash:")
token = sms_service.encrypt_key("xab_live_7Kd2mQ9xRf4wTEST")
check("bazadagi qiymat ochiq matn emas", "xab_live" in token, False)
check("aylanish qaytadi", sms_service.decrypt_key(token), "xab_live_7Kd2mQ9xRf4wTEST")
check("buzilgan token None", sms_service.decrypt_key("buzilgan"), None)

print("Niqoblash:")
check(
    "uzun kalit niqoblanadi",
    sms_service.mask_key("xab_live_7Kd2mQ9xRf4wABCD"),
    "xab_live_7…ABCD",
)
check("qisqa kalit qisqartiriladi", sms_service.mask_key("qisqa"), "qisq…")

print("Summa formati:")
check("minglik ajratish", sms_service._fmt_amount(1234567), "1 234 567")
check("butun son", sms_service._fmt_amount(50000.0), "50 000")


# --- Hodisalar kalitsiz filialda jim o'tadi ----------------------------

class _FakeBranch:
    name = "Test filial"
    sms_api_key = None


class _FakeSession:
    async def get(self, model, key):
        return _FakeBranch()


class _FakeReservation:
    branch_id = "b1"
    guest_id = "g1"
    room_id = "r1"
    reservation_number = "RES-TEST-1"
    paid_amount = 0
    total_amount = 100


async def _quiet_events():
    # Kalit yo'q — hech narsa yuborilmaydi va xato ko'tarilmaydi
    await sms_service.notify_booking_created(_FakeSession(), _FakeReservation())
    await sms_service.notify_payment(_FakeSession(), _FakeReservation(), 5000)
    return True


print("Hodisalar:")
check("kalitsiz filial jim o'tadi", asyncio.run(_quiet_events()), True)


# --- Xabarchi manzili ---------------------------------------------------
#
# 2026-09: standart manzil Xabarchi VEB-SAYTIGA qarab qolgan edi — sayt
# POST'ga 405 qaytarardi va SMS umuman ketmasdi (faqat logda ko'rinardi).

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402

print("Xabarchi manzili:")
check(
    "standart manzil veb-saytga qaramaydi",
    "xabar-web" in settings.SMS_API_BASE,
    False,
)
check(
    "/api/v1 bilan",
    sms_service.api_url("https://x.uz/api/v1"),
    "https://x.uz/api/v1/public/messages",
)
check(
    "oxirida / bo'lsa",
    sms_service.api_url("https://x.uz/api/v1/"),
    "https://x.uz/api/v1/public/messages",
)
check(
    "/api/v1 yozilmagan bo'lsa qo'shiladi",
    sms_service.api_url("https://x.uz"),
    "https://x.uz/api/v1/public/messages",
)


# --- Javoblarni talqin qilish -------------------------------------------

class _FakeClient:
    """httpx.AsyncClient o'rnida: tayyor javobni qaytaradi yoki xato otadi."""

    reply: object = None
    sent: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.sent = {"url": url, "headers": headers, "json": json}
        if isinstance(_FakeClient.reply, Exception):
            raise _FakeClient.reply
        return _FakeClient.reply


def _send(reply):
    """send_sms ni soxta javob bilan chaqiradi -> natija yoki xato kodi."""
    _FakeClient.reply = reply
    real = httpx.AsyncClient
    httpx.AsyncClient = _FakeClient
    try:
        return asyncio.run(sms_service.send_sms("xab_live_TEST", "+998901234567", "Salom"))
    except sms_service.SmsError as exc:
        return f"{exc.code}|{exc}"
    finally:
        httpx.AsyncClient = real


print("Xabarchi javoblari:")
queued = _send(httpx.Response(201, json=[{"id": 42, "status": "queued", "to": "998901234567"}]))
check("201 -> navbatdagi xabar qaytadi", (queued or {}).get("id") if isinstance(queued, dict) else queued, 42)
check("kalit X-API-Key sarlavhasida", _FakeClient.sent["headers"]["X-API-Key"], "xab_live_TEST")
check("raqam ro'yxat ichida", _FakeClient.sent["json"]["to"], ["+998901234567"])
check("so'rov /public/messages ga", _FakeClient.sent["url"].endswith("/api/v1/public/messages"), True)

check(
    "405 (veb-sayt) -> manzil noto'g'ri",
    _send(httpx.Response(405, text="")).split("|")[0],
    "bad_api_url",
)
check(
    "200 HTML -> 'yuborildi' deb aldamaydi",
    _send(httpx.Response(200, text="<!doctype html><html></html>")).split("|")[0],
    "bad_api_url",
)
auth = _send(httpx.Response(401, json={"code": "auth_error", "message": "Invalid API key"}))
check("401 -> auth_error", auth.split("|")[0], "auth_error")
check("401 sababi xodim tilida", "kalit noto'g'ri" in auth, True)
check(
    "402 -> oylik limit",
    _send(httpx.Response(402, json={"code": "quota_exceeded", "message": "x"})).split("|")[0],
    "quota_exceeded",
)
check(
    "403 -> ruxsat (sms.send) yo'q",
    "sms.send" in _send(httpx.Response(403, json={"code": "forbidden", "message": "x"})),
    True,
)
check(
    "noma'lum kod -> Xabarchi matni o'zi",
    _send(httpx.Response(409, json={"code": "yangi_kod", "message": "Izoh"})),
    "yangi_kod|Izoh",
)
check(
    "422 (pydantic) -> umumiy xato",
    _send(httpx.Response(422, json={"detail": [{"msg": "x"}]})).split("|")[0],
    "api_error",
)
check(
    "tarmoq xatosi -> ulanib bo'lmadi",
    _send(httpx.ConnectError("rad etildi")).split("|")[0],
    "unreachable",
)


# --- Faktura to'lovi -----------------------------------------------------

class _NoInvoiceSession:
    async def get(self, model, key):
        return None


async def _invoice_quiet():
    # Faktura topilmasa yoki bronsiz bo'lsa — jim, xatosiz
    await sms_service.notify_invoice_payment(_NoInvoiceSession(), "i1", 5000)
    return True


print("Faktura to'lovi:")
check("bronsiz faktura jim o'tadi", asyncio.run(_invoice_quiet()), True)

print()
print(f"Jami: {ok} OK, {fail} XATO")
sys.exit(1 if fail else 0)
