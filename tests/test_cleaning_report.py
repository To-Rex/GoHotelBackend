#!/usr/bin/env python3
"""Chiqishlar va tozalashlarni solishtirish hisobidagi yordamchi hisoblar.

Ishga tushirish:  python tests/test_cleaning_report.py

Nozik joylar: vaqtlar mintaqali va mintaqasiz kelishi mumkin (eski
yozuvlar), teskari oraliq esa hisobni buzmasligi kerak.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.cleaning_report_service import (  # noqa: E402
    _avg,
    _minutes_between,
)

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


base = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

print("--- oraliq hisobi ---")
check("20 daqiqa", _minutes_between(base, base + timedelta(minutes=20)), 20.0)
check("yarim daqiqa yaxlitlanadi", _minutes_between(base, base + timedelta(seconds=30)), 0.5)
check("boshlanish yo'q", _minutes_between(None, base), None)
check("tugash yo'q", _minutes_between(base, None), None)
check("ikkalasi ham yo'q", _minutes_between(None, None), None)

# Teskari oraliq — ma'lumot buzilgan bo'lsa hisobni buzmasin
check("teskari oraliq e'tiborga olinmaydi", _minutes_between(base, base - timedelta(minutes=5)), None)
check("nol oraliq", _minutes_between(base, base), 0.0)

print("--- mintaqasiz vaqtlar ---")
naive = datetime(2026, 9, 2, 12, 0)
check(
    "mintaqasiz boshlanish UTC deb olinadi",
    _minutes_between(naive, base + timedelta(minutes=10)),
    10.0,
)
check(
    "ikkalasi ham mintaqasiz",
    _minutes_between(naive, naive + timedelta(minutes=25)),
    25.0,
)

print("--- o'rtacha ---")
check("oddiy o'rtacha", _avg([10.0, 20.0, 30.0]), 20.0)
check("bitta qiymat", _avg([17.5]), 17.5)
check("bo'sh ro'yxat None beradi", _avg([]), None)
check("yaxlitlash", _avg([10.0, 11.0]), 10.5)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
