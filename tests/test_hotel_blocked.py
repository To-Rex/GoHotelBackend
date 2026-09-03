#!/usr/bin/env python3
"""Mehmonxona to'xtatilganda kirish qanday to'siladi.

Ishga tushirish:  python tests/test_hotel_blocked.py

Muhim joyi — `error_code`. Klient aynan shu kodga qarab "xizmat
to'xtatilgan" ekranini ko'rsatadi; matnga tayanib bo'lmaydi, u
o'zgarishi mumkin. Faol mehmonxona uchun esa tekshiruv KO'RINMASLIGI
kerak: oddiy ish avvalgidek davom etadi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.hotel_access import hotel_block_error  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


class FakeHotel:
    def __init__(self, status, name="Grand Plaza"):
        self.status = status
        self.name = name


print("--- faol mehmonxona to'silmaydi ---")
check("ACTIVE", hotel_block_error(FakeHotel("ACTIVE")), None)
check("kichik harf ham", hotel_block_error(FakeHotel("active")), None)

print("--- to'xtatilgan mehmonxona ---")
inactive = hotel_block_error(FakeHotel("INACTIVE"))
check("xato qaytdi", inactive is not None, True)
check("kod", inactive.error_code, "HOTEL_INACTIVE")
check("holat 403", inactive.status_code, 403)
check("nom matnda", "Grand Plaza" in inactive.detail, True)
check("matn o'zbekcha", "to'xtatilgan" in inactive.detail, True)
check(
    "ma'lumot saqlanishi aytiladi",
    "saqlanmoqda" in inactive.detail,
    True,
)

suspended = hotel_block_error(FakeHotel("SUSPENDED"))
check("vaqtincha to'xtatish kodi", suspended.error_code, "HOTEL_SUSPENDED")
check("vaqtincha matni boshqa", suspended.detail != inactive.detail, True)

print("--- chekka holatlar ---")
check(
    "noma'lum holat ham to'sadi",
    hotel_block_error(FakeHotel("ARCHIVED")).error_code,
    "HOTEL_ARCHIVED",
)
check(
    "bo'sh holat",
    hotel_block_error(FakeHotel("")).error_code,
    "HOTEL_BLOCKED",
)
check(
    "holat None",
    hotel_block_error(FakeHotel(None)).error_code,
    "HOTEL_BLOCKED",
)
missing = hotel_block_error(None)
check("mehmonxona topilmadi kodi", missing.error_code, "HOTEL_NOT_FOUND")

# Nomsiz obyektda matn ikki nuqta bilan boshlanib qolmasin
nameless = hotel_block_error(FakeHotel("INACTIVE", name="  "))
check("nomsiz obyekt", nameless.detail.startswith("Mehmonxona"), True)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
