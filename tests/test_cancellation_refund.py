#!/usr/bin/env python3
"""Bekor qilishda pul qaytarish hisobi.

Ishga tushirish:  python tests/test_cancellation_refund.py

Nega kerak: bu pul bilan bog'liq va orqaga qaytarib bo'lmaydigan amal.
Mehmonxonalar bir xil emas — biri to'lovni to'liq qaytaradi, biri jarima
ushlab qoladi — shuning uchun foiz sozlamadan keladi va uning chegaralari
(bo'sh sozlama, buzuq qiymat, 100 dan katta) tekshirilishi kerak.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.reservation_service import (  # noqa: E402
    DEFAULT_CANCELLATION_FEE_PERCENT,
    compute_cancellation_refund,
    resolve_cancellation_fee_percent,
)
from app.core.exceptions import ValidationException  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


def raises(label, fn):
    global ok, fail
    try:
        fn()
    except ValidationException as e:
        ok += 1
        print(f"  OK   {label:<52} {e.args[0][:40]}")
        return
    except Exception as e:  # noqa: BLE001
        fail += 1
        print(f"  XATO {label:<52} kutilmagan xato: {e!r}")
        return
    fail += 1
    print(f"  XATO {label:<52} xato kutilgan edi, chiqmadi")


print("--- sozlamadagi foiz ---")
check("sozlanmagan mehmonxona — to'liq qaytariladi",
      resolve_cancellation_fee_percent(None), 0.0)
check("bo'sh sozlama", resolve_cancellation_fee_percent({}), 0.0)
check("30 foiz",
      resolve_cancellation_fee_percent({"cancellation_policy": {"fee_percent": 30}}), 30.0)
check("satr ko'rinishidagi son",
      resolve_cancellation_fee_percent({"cancellation_policy": {"fee_percent": "15.5"}}), 15.5)
check("100 dan katta qiymat cheklanadi",
      resolve_cancellation_fee_percent({"cancellation_policy": {"fee_percent": 150}}), 100.0)
check("manfiy qiymat nolga tushadi",
      resolve_cancellation_fee_percent({"cancellation_policy": {"fee_percent": -5}}), 0.0)
check("buzuq qiymat standartga qaytadi",
      resolve_cancellation_fee_percent({"cancellation_policy": {"fee_percent": "xx"}}),
      DEFAULT_CANCELLATION_FEE_PERCENT)

print("--- qaytarim hisobi (sozlama bo'yicha) ---")
check("0% — hammasi qaytariladi", compute_cancellation_refund(500000, 0), (500000.0, 0.0))
check("100% — hech nima qaytarilmaydi", compute_cancellation_refund(500000, 100), (0.0, 500000.0))
check("30% jarima", compute_cancellation_refund(500000, 30), (350000.0, 150000.0))
check("kasrli foiz yaxlitlanadi", compute_cancellation_refund(333333, 15.5), (281666.39, 51666.61))
check("to'lanmagan bron", compute_cancellation_refund(0, 30), (0.0, 0.0))
check("manfiy to'lov nol deb qaraladi", compute_cancellation_refund(-100, 30), (0.0, 0.0))

print("--- xodim summani o'zi kiritganda ---")
check("qisman qaytarish", compute_cancellation_refund(500000, 30, 200000), (200000.0, 300000.0))
check("sozlamadan ko'ra saxiyroq — ruxsat",
      compute_cancellation_refund(500000, 30, 500000), (500000.0, 0.0))
check("nol qaytarish", compute_cancellation_refund(500000, 0, 0), (0.0, 500000.0))
raises("to'langandan ko'p qaytarib bo'lmaydi",
       lambda: compute_cancellation_refund(500000, 0, 600000))
raises("manfiy summa qabul qilinmaydi",
       lambda: compute_cancellation_refund(500000, 0, -1))
check("yaxlitlash chegarasi to'langan summagacha qisqartiriladi",
      compute_cancellation_refund(500000, 0, 500000.005), (500000.0, 0.0))

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
