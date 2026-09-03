#!/usr/bin/env python3
"""Bronni cho'zish qoidalari.

Ishga tushirish:  python tests/test_reservation_extend.py

Nozik joylar:

1. Chegara — shu xonadagi KEYINGI bron. Keyingi bron bo'lmasa cheklov
   ham yo'q ("istalgancha cho'zish mumkin").

2. Soatlik bronda keyingi bron oldidan tozalash tanaffusi qoladi; kunlik
   bronda esa chiqish kuni keyingi mehmonning kirish kuni bo'la oladi va
   tanaffus ayirilmaydi. Ikkalasi bron YARATISHDAGI qoida bilan bir xil
   bo'lishi shart, aks holda cho'zib bo'lgan vaqtga bron ochib bo'lmay
   qolardi (yoki teskarisi).

3. Faqat cho'zish. Qisqartirish pul qaytarish savolini ochadi — u
   bekor qilishdagi kabi alohida qoida talab qiladi.

4. Bir kun ichidagi soatlik bronda `check_out_date` bazadagi
   `check_out_date > check_in_date` cheklovi uchun kirish kunidan bir
   kun keyin turadi. Cho'zishda bu buzilmasligi kerak.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.reservation_extend import (  # noqa: E402
    BLOCKING_STATUSES,
    LOCKED_STATUSES,
    ExtendError,
    Span,
    assert_extendable,
    checkout_date_for,
    extension_limit,
    span_of,
    validate_new_end,
)

TURNOVER = 15
ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<54} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<54} kutilgan {want}, chiqdi {got}")


def dt(day, hour=0, minute=0):
    return datetime(2026, 9, day, hour, minute)


class FakeRes:
    def __init__(self, booking_type, ci_date, co_date, ci_dt=None, co_dt=None):
        self.booking_type = booking_type
        self.check_in_date = ci_date
        self.check_out_date = co_date
        self.check_in_datetime = ci_dt
        self.check_out_datetime = co_dt


def hourly_span(start_h, end_h, day=3):
    return Span(start=dt(day, start_h), end=dt(day, end_h), hourly=True)


def daily_span(start_day, end_day):
    return Span(start=dt(start_day), end=dt(end_day), hourly=False)


print("--- vaqt oralig'ini o'qish ---")
h = span_of(FakeRes("HOURLY", date(2026, 9, 3), date(2026, 9, 4),
                    dt(3, 19, 17), dt(3, 21, 17)))
check("soatlik: boshlanish", h.start, dt(3, 19, 17))
check("soatlik: tugash", h.end, dt(3, 21, 17))
check("soatlik deb belgilandi", h.hourly, True)

d = span_of(FakeRes("DAILY", date(2026, 9, 3), date(2026, 9, 6)))
check("kunlik: kun boshidan", d.start, dt(3))
check("kunlik: chiqish kuni boshiga", d.end, dt(6))
check("kunlik deb belgilandi", d.hourly, False)

# Vaqti yo'q "soatlik" bron kunlik kabi o'qiladi — buzuq yozuv xatoga
# olib kelmasligi kerak
broken = span_of(FakeRes("HOURLY", date(2026, 9, 3), date(2026, 9, 4)))
check("vaqtsiz soatlik kunlikka tushadi", broken.hourly, False)

# Bazadan zona bilan keladi; taqqoslashda zonasiz paytlar ham qatnashadi
aware = span_of(FakeRes("HOURLY", date(2026, 9, 3), date(2026, 9, 4),
                        dt(3, 10).replace(tzinfo=timezone.utc),
                        dt(3, 12).replace(tzinfo=timezone.utc)))
check("zona olib tashlanadi", aware.end.tzinfo, None)
check("qiymat o'zgarmaydi", aware.end, dt(3, 12))


print("\n--- chegara: keyingi bron ---")
current = hourly_span(10, 12)
check("keyingi bron yo'q -> cheklovsiz", extension_limit(current, [], TURNOVER), None)
check(
    "keyingi bron 15:00 -> 14:45 gacha",
    extension_limit(current, [hourly_span(15, 17)], TURNOVER),
    dt(3, 14, 45),
)
check(
    "eng yaqini olinadi",
    extension_limit(
        current, [hourly_span(20, 22), hourly_span(15, 17), hourly_span(18, 19)], TURNOVER
    ),
    dt(3, 14, 45),
)
check(
    "o'tmishdagi bron chegara emas",
    extension_limit(current, [hourly_span(6, 8)], TURNOVER),
    None,
)
check(
    "aynan tugash paytida boshlanadigan bron -> cho'zib bo'lmaydi",
    extension_limit(current, [hourly_span(12, 14)], TURNOVER),
    dt(3, 11, 45),
)

# Soatlik bronning yo'lida KUNLIK bron tursa — u kun boshidan to'sadi
check(
    "keyingi kunlik bron kun boshidan to'sadi",
    extension_limit(current, [daily_span(4, 6)], TURNOVER),
    dt(3, 23, 45),
)

print("\n--- kunlik bronda tanaffus ayirilmaydi ---")
# Chiqish kuni keyingi mehmonning kirish kuni bo'la oladi — bron
# yaratishdagi qoida ham shunday
check(
    "kunlik: keyingi bron 8-kun -> 8-kungacha",
    extension_limit(daily_span(3, 6), [daily_span(8, 10)], TURNOVER),
    dt(8),
)
check(
    "kunlik: keyingi soatlik bron ham kun aniqligida",
    extension_limit(daily_span(3, 6), [hourly_span(14, 16, day=7)], TURNOVER),
    dt(7, 14),
)
check(
    "kunlik: keyingi bron yo'q",
    extension_limit(daily_span(3, 6), [], TURNOVER),
    None,
)


print("\n--- yangi tugash vaqtini tekshirish ---")


def verdict(current, new_end, limit):
    try:
        validate_new_end(current, new_end, limit)
        return None
    except ExtendError as e:
        return e.code


check("cheklovsiz cho'zish o'tadi", verdict(hourly_span(10, 12), dt(3, 23), None), None)
check(
    "chegaragacha o'tadi",
    verdict(hourly_span(10, 12), dt(3, 14, 45), dt(3, 14, 45)),
    None,
)
check(
    "chegaradan bir daqiqa oshsa to'siladi",
    verdict(hourly_span(10, 12), dt(3, 14, 46), dt(3, 14, 45)),
    "EXTENSION_BLOCKED",
)
check(
    "qisqartirish qabul qilinmaydi",
    verdict(hourly_span(10, 12), dt(3, 11), None),
    "NOT_AN_EXTENSION",
)
check(
    "o'zgarishsiz vaqt ham qabul qilinmaydi",
    verdict(hourly_span(10, 12), dt(3, 12), None),
    "NOT_AN_EXTENSION",
)
check(
    "zonali vaqt ham to'g'ri taqqoslanadi",
    verdict(hourly_span(10, 12), dt(3, 14).replace(tzinfo=timezone.utc), dt(3, 14, 45)),
    None,
)

# Chegara tugash vaqtidan oldinda bo'lsa — umuman cho'zib bo'lmaydi
check(
    "chegara o'tib ketgan bo'lsa",
    verdict(hourly_span(10, 12), dt(3, 12, 30), dt(3, 11, 45)),
    "EXTENSION_BLOCKED",
)


print("\n--- qaysi holatda cho'zish mumkin ---")


def locked(status):
    try:
        assert_extendable(status)
        return None
    except ExtendError as e:
        return e.code


for status in ("PENDING", "CONFIRMED", "CHECKED_IN"):
    check(f"{status} — mumkin", locked(status), None)
for status in LOCKED_STATUSES:
    check(f"{status} — mumkin emas", locked(status), "RESERVATION_LOCKED")
check("to'sadigan holatlar ro'yxati", BLOCKING_STATUSES, ("CONFIRMED", "CHECKED_IN"))


print("\n--- yangi check_out_date ---")
ci = date(2026, 9, 3)
check(
    "bir kun ichidagi soatlik -> kirish kuni + 1",
    checkout_date_for(ci, dt(3, 23), hourly=True),
    date(2026, 9, 4),
)
check(
    "yarim tundan oshgan soatlik -> haqiqiy kun",
    checkout_date_for(ci, dt(4, 2), hourly=True),
    date(2026, 9, 4),
)
check(
    "ertadan keyingi kunga cho'zilgan soatlik",
    checkout_date_for(ci, dt(5, 3), hourly=True),
    date(2026, 9, 5),
)
check(
    "kunlik bronda kun o'zi olinadi",
    checkout_date_for(ci, dt(8), hourly=False),
    date(2026, 9, 8),
)
# Cheklov: check_out_date HAR DOIM check_in_date dan katta bo'lishi kerak
for hours in (1, 12, 23):
    result = checkout_date_for(ci, dt(3, hours), hourly=True)
    check(f"soat {hours:02d}:00 -> sana kirish kunidan katta", result > ci, True)


print("\n--- to'liq stsenariy: 19:17-21:17 bronni cho'zish ---")
# Ishlab chiqarishdagi haqiqiy shakl: bir kun ichidagi soatlik bron
res = FakeRes("HOURLY", date(2026, 9, 3), date(2026, 9, 4), dt(3, 19, 17), dt(3, 21, 17))
current = span_of(res)
# Keyingi bron yarim tundan keyin: 01:00 dan 03:00 gacha
others = [Span(dt(4, 1), dt(4, 3), hourly=True)]
limit = extension_limit(current, others, TURNOVER)
check("chegara 01:00 dan 15 daqiqa oldin", limit, dt(4, 0, 45))
check("23:00 gacha cho'zish mumkin", verdict(current, dt(3, 23), limit), None)
check("01:00 gacha cho'zib bo'lmaydi", verdict(current, dt(4, 1), limit), "EXTENSION_BLOCKED")
check(
    "23:00 uchun check_out_date",
    checkout_date_for(res.check_in_date, dt(3, 23), True),
    date(2026, 9, 4),
)
check(
    "00:30 uchun check_out_date",
    checkout_date_for(res.check_in_date, dt(4, 0, 30), True),
    date(2026, 9, 4),
)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
