#!/usr/bin/env python3
"""Moliya sahifasining sahifalash, qidiruv, saralash va yig'indisi.

Ishga tushirish:  python tests/test_finance_pagination.py

Nozik joylar:

1. Saralash ustunlari ro'yxati YOPIQ bo'lishi. Mijozdan kelgan nomni
   to'g'ridan-to'g'ri `order_by` ga qo'yish ochiq eshik bo'lardi.

2. Ro'yxat va sanoq BIR XIL shartlardan foydalanishi. Ular ajralib ketsa
   sahifalagichdagi "jami" soni ro'yxatga mos kelmay qolardi.

3. Har bir so'rov PostgreSQL da haqiqatan kompilyatsiya bo'lishi —
   ayniqsa qarzdorlikdagi GREATEST va qaytarimdagi CASE.

4. Yig'indi formulalari mijozdagi eski hisob-kitobning aynan o'zi
   qolishi: sahifadagi raqamlar o'zgarmasligi kerak edi.
"""
import asyncio
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.application.services.finance_service import (  # noqa: E402
    INVOICE_SORTS,
    PAYMENT_SORTS,
    FinanceService,
    _ordering,
)
from app.application.services.finance_summary_service import (  # noqa: E402
    NON_DEBT_STATUSES,
    UNKNOWN_METHOD,
    FinanceSummaryService,
    _method_key,
    _num,
)
from app.infrastructure.database.models.invoice import Invoice  # noqa: E402
from app.infrastructure.database.models.payment import Payment  # noqa: E402

DIALECT = postgresql.dialect()
HOTEL = uuid.uuid4()
FROM, TO = date(2026, 9, 1), date(2026, 9, 30)

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


def compiles(label, stmt):
    """So'rov Postgresda tuziladimi — matnini qaytaradi."""
    global ok, fail
    try:
        text = " ".join(str(stmt.compile(dialect=DIALECT)).split())
        ok += 1
        print(f"  OK   {label}")
        return text
    except Exception as e:  # noqa: BLE001
        fail += 1
        print(f"  XATO {label}: {e}")
        return ""


def svc():
    s = FinanceService.__new__(FinanceService)
    s.session = None
    return s


print("--- saralash ustunlari ---")
# Bu nomlar frontendda `SortableHead column=...` da yoziladi; ular
# mos kelmasa saralash jimgina standart ustunga tushib qolardi
check(
    "hisob-faktura ustunlari",
    sorted(INVOICE_SORTS),
    sorted([
        "created_at", "discount_amount", "due_date", "invoice_date",
        "invoice_number", "paid_amount", "remaining", "status", "total_amount",
    ]),
)
check(
    "to'lov ustunlari",
    sorted(PAYMENT_SORTS),
    sorted(["amount", "created_at", "payment_date", "payment_method",
            "payment_number"]),
)

print("\n--- saralash yo'nalishi ---")
check(
    "standart — kamayish",
    str(_ordering(PAYMENT_SORTS, "amount", None, Payment.created_at)).endswith("DESC"),
    True,
)
check(
    "asc so'ralganda o'sish",
    str(_ordering(PAYMENT_SORTS, "amount", "asc", Payment.created_at)).endswith("ASC"),
    True,
)
check(
    "katta harf ham tushuniladi",
    str(_ordering(PAYMENT_SORTS, "amount", "ASC", Payment.created_at)).endswith("ASC"),
    True,
)
# Noma'lum nom standart ustunga tushadi — SQL ga tushmaydi
for bad in ("", "yoq_bunday", "amount; DROP TABLE payments", "1=1"):
    check(
        f"noma'lum nom standartga qaytadi: {bad!r}",
        "created_at" in str(_ordering(PAYMENT_SORTS, bad, "asc", Payment.created_at)),
        True,
    )

print("\n--- filtr shartlari ---")
check("hech narsa berilmasa shart yo'q", len(svc()._invoice_conditions(None, None, None, None, None)), 0)
check("mehmonxona", len(svc()._invoice_conditions(HOTEL, None, None, None, None)), 1)
check(
    "mehmonxona + holat + ikki sana + qidiruv",
    len(svc()._invoice_conditions(HOTEL, "PAID", FROM, TO, "INV-1")),
    5,
)
check(
    "faqat bo'shliqdan iborat qidiruv hisobga olinmaydi",
    len(svc()._invoice_conditions(HOTEL, None, None, None, "   ")),
    1,
)
check("to'lov: mehmonxona + sanalar", len(svc()._payment_conditions(HOTEL, FROM, TO, None)), 3)
check(
    "to'lov: qidiruv bitta OR shartiga yig'iladi",
    len(svc()._payment_conditions(HOTEL, FROM, TO, "naqd")),
    4,
)

print("\n--- ro'yxat va sanoq bir xil shartdan ---")
inv_conditions = svc()._invoice_conditions(HOTEL, "PAID", FROM, TO, "INV-1")
listing = compiles(
    "hisob-fakturalar ro'yxati",
    select(Invoice).where(*inv_conditions).order_by(
        _ordering(INVOICE_SORTS, "remaining", "desc", Invoice.created_at), Invoice.id
    ).offset(100).limit(50),
)
counting = compiles(
    "hisob-fakturalar sanog'i",
    select(func.count(Invoice.id)).where(*inv_conditions),
)
# WHERE bo'lagi ikkalasida bir xil bo'lishi shart
check(
    "WHERE bo'lagi bir xil",
    listing.split(" WHERE ")[-1].split(" ORDER BY ")[0],
    counting.split(" WHERE ")[-1],
)
check("ro'yxatda LIMIT bor", "LIMIT" in listing, True)
check("sanoqda LIMIT yo'q", "LIMIT" in counting, False)
check("qat'iy tartib uchun ikkinchi mezon", "invoices.id" in listing.split("ORDER BY")[-1], True)

pay_conditions = svc()._payment_conditions(HOTEL, FROM, TO, "PAY-7")
pay_listing = compiles(
    "to'lovlar ro'yxati",
    select(Payment).where(*pay_conditions).order_by(
        _ordering(PAYMENT_SORTS, "amount", "asc", Payment.created_at), Payment.id
    ).offset(50).limit(50),
)
check("qidiruv uchta maydonni qamraydi", pay_listing.lower().count("ilike"), 3)
check("qat'iy tartib uchun ikkinchi mezon", "payments.id" in pay_listing.split("ORDER BY")[-1], True)

print("\n--- barcha saralash ustunlari Postgresda tuziladi ---")
for key in INVOICE_SORTS:
    compiles(f"hisob-faktura: {key}", select(Invoice).order_by(
        _ordering(INVOICE_SORTS, key, "asc", Invoice.created_at)))
for key in PAYMENT_SORTS:
    compiles(f"to'lov: {key}", select(Payment).order_by(
        _ordering(PAYMENT_SORTS, key, "desc", Payment.created_at)))


print("\n--- yig'indi so'rovlari ---")


class CapturingSession:
    """So'rovlarni ushlab qoladi — baza kerak emas."""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        try:
            width = len(stmt.selected_columns)
        except Exception:  # noqa: BLE001
            width = 1
        return _Rows(width)


class _Rows:
    def __init__(self, width):
        self.width = width

    def one(self):
        return tuple(0 for _ in range(self.width))

    def all(self):
        return []


session = CapturingSession()
summary_service = FinanceSummaryService.__new__(FinanceSummaryService)
summary_service.session = session
result = asyncio.run(summary_service.build(HOTEL, FROM, TO, status="PAID"))

labels = [
    "to'lovlar yig'indisi",
    "to'lovlar usul bo'yicha",
    "hisob-fakturalar yig'indisi",
    "xarajatlar yig'indisi",
    "xarajatlar usul bo'yicha",
    "xarajatlar toifasi bo'yicha",
    "do'kon to'langan savdo",
    "do'kon qarzi",
]
check("so'rovlar soni", len(session.statements), len(labels))
sql = " | ".join(
    compiles(labels[i], stmt) for i, stmt in enumerate(session.statements)
)

check("qarzdorlikda GREATEST", "greatest" in sql.lower(), True)
check("qaytarimda CASE", "case when" in sql.lower(), True)

# Holatlar SQL matnida emas, bog'langan parametrlarda turadi — shuning
# uchun qiymatlar bilan birga kompilyatsiya qilinadi
invoice_sql = str(
    session.statements[2].compile(
        dialect=DIALECT, compile_kwargs={"literal_binds": True}
    )
)
check(
    "bekor qilinganlar qarzga kirmaydi",
    all(f"'{status}'" in invoice_sql for status in NON_DEBT_STATUSES),
    True,
)

# Do'kon qarzi JORIY qoldiq: davr o'zgarganda o'zgarmasligi kerak, ya'ni
# so'rovda to'lov sanasi umuman qatnashmaydi
shop_paid_sql = " ".join(str(session.statements[6].compile(dialect=DIALECT)).split())
shop_debt_sql = " ".join(str(session.statements[7].compile(dialect=DIALECT)).split())
check("to'langan savdo to'lov sanasi bo'yicha", "paid_at" in shop_paid_sql, True)
check("do'kon qarzida sana filtri YO'Q", "paid_at" in shop_debt_sql, False)
check("do'kon qarzi mehmonxona bilan chegaralangan", "hotel_id" in shop_debt_sql, True)

print("\n--- javob tarkibi ---")
for key in (
    "income", "payment_count", "refunds",
    "invoice_total", "invoice_discount", "invoice_paid", "invoice_count", "debt",
    "expense_total", "expense_count", "expense_categories",
    "shop_revenue", "shop_paid_count", "shop_debt", "shop_debt_count",
    "methods",
):
    check(f"maydon bor: {key}", key in result, True)

print("\n--- usul kaliti ---")
check("kichik harf kattaga aylanadi", _method_key("cash"), "CASH")
check("bo'sh qiymat", _method_key(""), UNKNOWN_METHOD)
check("None", _method_key(None), UNKNOWN_METHOD)
check("o'zgarmaydi", _method_key("BANK_TRANSFER"), "BANK_TRANSFER")

print("\n--- son o'girish ---")
check("None nol bo'ladi", _num(None), 0.0)
check("satr son", _num("125.5"), 125.5)
check("manfiy saqlanadi", _num(-300), -300.0)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
