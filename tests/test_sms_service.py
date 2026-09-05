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

print()
print(f"Jami: {ok} OK, {fail} XATO")
sys.exit(1 if fail else 0)
