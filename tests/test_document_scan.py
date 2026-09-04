#!/usr/bin/env python3
"""Telefondan kelgan hujjat skaneri — mehmonni topish va yozuv shakli.

Ishga tushirish:  python tests/test_document_scan.py

OCR bu yerda ishga tushmaydi: tekshirilayotgani raqamni solishtirishga
tayyorlash va ro'yxatga chiqadigan javob shakli — ular deterministik.
Aynan shu ikkisi noto'g'ri bo'lsa, veb ekrani mehmonni topa olmay yangi
mijozni QAYTA yaratib yuborardi.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.document_scan_service import (  # noqa: E402
    DocumentScanService,
    full_name_of,
    normalize_number,
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


print("--- raqamni solishtirishga tayyorlash ---")
check("bo'shliq olib tashlanadi", normalize_number("AA 1234567"), "AA1234567")
check("chiziqcha", normalize_number("AA-123-4567"), "AA1234567")
check("kichik harf kattaga", normalize_number("aa1234567"), "AA1234567")
check("№ belgisi", normalize_number("№ AB1234567"), "AB1234567")
check("JSHSHIR", normalize_number("3150390001 0015"), "315039000100 15".replace(" ", ""))
check("bo'sh qiymat", normalize_number(None), "")
check("faqat belgilar", normalize_number("-- //"), "")

print("--- to'liq ism ---")
# Ro'yxatda "Familiya Ism" tartibida ko'rinadi — mehmonlar ro'yxatidagidek
check(
    "familiya oldinda",
    full_name_of({"firstName": "JASUR", "lastName": "TOSHMATOV"}),
    "TOSHMATOV JASUR",
)
check("faqat ism", full_name_of({"firstName": "JASUR"}), "JASUR")
check("faqat familiya", full_name_of({"lastName": "TOSHMATOV"}), "TOSHMATOV")
check("ikkalasi ham yo'q", full_name_of({}), None)
check("bo'sh satr", full_name_of({"firstName": "  ", "lastName": ""}), None)


class FakeScan:
    """`_as_dict` uchun yetarli minimal yozuv."""

    def __init__(self, **kw):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.document_type = "PASSPORT"
        self.document_number = "AA1234567"
        self.full_name = "TOSHMATOV JASUR"
        self.guest_id = None
        self.guest_name = None
        self.verified = True
        self.fields = {"firstName": "JASUR", "documentNumber": "AA1234567"}
        self.created_at = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
        self.acknowledged_at = None
        for key, value in kw.items():
            setattr(self, key, value)


print("--- javob shakli ---")
row = DocumentScanService._as_dict(FakeScan())
check("topilmagan mehmon", row["matched"], False)
check("mehmon id yo'q", row["guest_id"], None)
check("hujjat maydonlari uzatiladi", row["document"]["firstName"], "JASUR")
check("yopilmagan", row["acknowledged"], False)
check("takror emas", row["duplicate"], False)
check("vaqt ISO", row["created_at"], "2026-09-04T10:00:00+00:00")

matched = DocumentScanService._as_dict(
    FakeScan(
        guest_id="22222222-2222-2222-2222-222222222222",
        guest_name="Jasur Toshmatov",
    )
)
check("topilgan mehmon", matched["matched"], True)
check("mehmon id satr", matched["guest_id"], "22222222-2222-2222-2222-222222222222")
check("mehmon ismi", matched["guest_name"], "Jasur Toshmatov")

closed = DocumentScanService._as_dict(
    FakeScan(acknowledged_at=datetime(2026, 9, 4, 10, 5, tzinfo=timezone.utc))
)
check("yopilgan yozuv", closed["acknowledged"], True)

dup = DocumentScanService._as_dict(FakeScan(), duplicate=True)
check("takror belgisi", dup["duplicate"], True)

# Maydonlari bo'sh skan ham javob bera olishi kerak: xodim keyin qo'lda
# to'ldiradi, ekran esa buzilmasligi kerak
empty = DocumentScanService._as_dict(
    FakeScan(fields=None, full_name=None, document_number=None, verified=False)
)
check("bo'sh maydonlar", empty["document"], {})
check("tasdiqlanmagan", empty["verified"], False)
check("raqamsiz", empty["document_number"], None)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
