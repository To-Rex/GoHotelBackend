#!/usr/bin/env python3
"""Skaner kirish sifati: xira kadr darvozasi va kontrast kuchaytirish.

Ishga tushirish:  python tests/test_scan_quality.py

OCR modellari ishga tushmaydi — tekshirilayotgani `_decode` dagi sifat
darvozasi va CLAHE yordamchisi. Xira kadr eng ko'p xatoning manbai:
undan o'qilgan qiymat "deyarli to'g'ri" chiqib, sezilmay o'tib ketadi.
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.document_ocr import service  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


def encode(image) -> bytes:
    success, buffer = cv2.imencode(".png", image)
    assert success
    return buffer.tobytes()


def decode_error(image) -> str | None:
    try:
        service._decode(encode(image))
        return None
    except ValueError as exc:
        return str(exc)


# Matnli hujjatga o'xshash kadr: oq fonda qora chiziqlar (keskin qirralar)
sharp = np.full((400, 640, 3), 235, np.uint8)
for row in range(40, 360, 24):
    cv2.putText(sharp, "AA1234567<<TOSHMATOV", (20, row),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 2)

# O'sha kadrning kuchli xiralatilgani — silkinib olingan suratga o'xshaydi
blurry = cv2.GaussianBlur(sharp, (31, 31), 12)

# Bir tekis kulrang kadr — qopqog'i yopiq kamera yoki devor
flat = np.full((400, 640, 3), 128, np.uint8)

print("--- xira kadr darvozasi ---")
check("tiniq kadr o'tadi", decode_error(sharp), None)
check("xira kadr qaytariladi", decode_error(blurry), "IMAGE_BLURRY")
check("bo'sh tekis kadr ham qaytariladi", decode_error(flat), "IMAGE_BLURRY")

print("--- o'lcham tekshiruvlari saqlangan ---")
tiny = np.full((80, 200, 3), 235, np.uint8)
check("juda kichik rasm", decode_error(tiny), "IMAGE_TOO_SMALL")
try:
    service._decode(b"bu rasm emas")
    check("buzilgan bayt", None, "BAD_IMAGE")
except ValueError as exc:
    check("buzilgan bayt", str(exc), "BAD_IMAGE")

print("--- katta rasm kichraytiriladi (funksiya saqlangan) ---")
wide = np.full((1200, 3200, 3), 235, np.uint8)
for row in range(60, 1150, 40):
    cv2.putText(wide, "AA1234567<<TOSHMATOV JASUR", (40, row),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 3)
decoded = service._decode(encode(wide))
check("kenglik chegarasi", decoded.shape[1], service.MAX_WIDTH)

print("--- kontrast kuchaytirish ---")
dim = (sharp * 0.35 + 120).astype(np.uint8)  # kontrasti past nusxa
enhanced = service._clahe(dim)
check("o'lcham saqlanadi", enhanced.shape, dim.shape)
check("uch kanal", enhanced.shape[2], 3)
gray_before = cv2.cvtColor(dim, cv2.COLOR_BGR2GRAY)
gray_after = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
check(
    "kontrast oshadi",
    float(gray_after.std()) > float(gray_before.std()),
    True,
)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
