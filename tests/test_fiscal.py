"""Fiskal modul (E-POS): so'rov tuzish va fiskal cheklar navbati.

Ishga tushirish: pytest tests/test_fiscal.py

Nozik joylar:

1. E-POS kichik xatoni ham rad etadi: summalar tiyinda, pozitsiyalar
   yig'indisi naqd + kartaga TENG, QQS = net × 12 / 112.
2. Sozlama har filialda; yoqish uchun STIR va yashash IKPU kodi majburiy.
   Fiskal modul o'chiq bo'lsa hech narsa yozilmaydi.
3. Faqat YOQILGANDAN keyingi to'lovlar navbatga tushadi; bitta to'lov
   faqat bitta chekka kiradi (ikki marta fiskallashmaydi).
4. Bank o'tkazmasi fiskallashtirilmaydi (o'tkazib yuboriladi), qaytarim
   asl chekning fiskal ma'lumoti bilan ketadi.
5. Chekni bir vaqtda ikki qurilma yubora olmaydi; avval yuborilgan bo'lishi
   mumkin bo'lsa — tekshirish talab qilinadi.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.application.services import fiscal_payload as fp  # noqa: E402
from app.application.services import fiscal_service as fs  # noqa: E402
from app.application.services.fiscal_service import FiscalService, serialize  # noqa: E402
from app.core.exceptions import ConflictException, ValidationException  # noqa: E402
from app.infrastructure.database.models.base import Base  # noqa: E402
from app.infrastructure.database.models.branch import Branch  # noqa: E402
from app.infrastructure.database.models.fiscal import FiscalReceipt, FiscalReceiptLink  # noqa: E402
from app.infrastructure.database.models.guest import Guest  # noqa: E402
from app.infrastructure.database.models.hotel import Hotel  # noqa: E402
from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem  # noqa: E402
from app.infrastructure.database.models.payment import Payment  # noqa: E402
from app.infrastructure.database.models.reservation import Reservation  # noqa: E402
from app.infrastructure.database.models.room import Room  # noqa: E402
from app.infrastructure.database.models.shop import (  # noqa: E402
    ShopBatch,
    ShopProduct,
    ShopSale,
    ShopSaleItem,
    ShopSalePayment,
)
from app.infrastructure.database.models.user import User  # noqa: E402
from tests.test_finance_filters import BRANCH, HOTEL, at, seed  # noqa: E402

BASE = {
    "company_name": "Registon Grand MCHJ",
    "company_address": "Samarqand sh., Registon ko'chasi 1",
    "company_inn": "308 839 903",
    "items": {"room": {"class_code": "10406003004000000", "package_code": "1506556"}},
}
ENABLED = {**BASE, "enabled": True, "enabled_at": "2026-10-01T00:00:00+00:00"}


def cfg(**over) -> dict:
    return fp.resolve({**BASE, **over})


# --- 1. Sof funksiyalar -----------------------------------------------------


def test_resolve_defaults_and_validation():
    empty = fp.resolve(None)
    assert empty["enabled"] is False and empty["url"] == fp.DEFAULT_URL and empty["token"] == fp.EPOS_TOKEN
    assert empty["transport"] == "browser" and empty["vat_percent"] == 0 and empty["printer_size"] == 80
    assert empty["methods"] == {"CASH": "cash", "CARD": "card", "ONLINE": "card", "BANK_TRANSFER": "skip"}
    assert empty["auto_open_shift"] is True and empty["epos_prints"] is False
    # Bo'sh sozlamani yoqib bo'lmaydi
    problems = fp.validate(empty)
    assert any("STIR" in p for p in problems) and any("IKPU" in p for p in problems)

    ok = cfg(vat_percent=12, printer_size=58, transport="server", methods={"ONLINE": "skip", "BITCOIN": "cash"})
    assert fp.validate(ok) == []
    assert ok["company_inn"] == "308839903"                  # faqat raqamlar
    assert ok["vat_percent"] == 12 and ok["printer_size"] == 58 and ok["transport"] == "server"
    assert ok["methods"]["ONLINE"] == "skip" and "BITCOIN" not in ok["methods"]
    # Noto'g'ri qiymatlar standartga qaytadi
    bad = cfg(vat_percent=20, printer_size=72, transport="ftp")
    assert (bad["vat_percent"], bad["printer_size"], bad["transport"]) == (0, 80, "browser")
    # IKPU va o'ram kodi juft bo'lishi kerak
    half = cfg(items={**BASE["items"], "shop": {"class_code": "02202003001002001"}})
    assert any("birga" in p for p in fp.validate(half))
    assert any("http" in p for p in fp.validate(cfg(url="localhost:8347")))


def test_money_vat_and_methods():
    assert fp.to_tiyin(5000) == 500000 and fp.to_tiyin("1250.5") == 125050 and fp.to_tiyin(0.005) == 1
    assert fp.vat_of(250000, 12) == 26786                    # (price) × 12 / 112
    assert fp.vat_of(250000, 0) == 0 and fp.vat_of(0, 12) == 0
    assert fp.canonical_method("credit_card") == "CARD" and fp.canonical_method("TRANSFER") == "BANK_TRANSFER"
    sums = fp.split_methods(
        [("CASH", 100_000), ("CREDIT_CARD", 50_000), ("MOBILE_PAYMENT", 25_000), ("TRANSFER", 70_000)], cfg()
    )
    assert sums == {"cash": 100_000.0, "card": 75_000.0, "skip": 70_000.0}
    assert fp.new_number().startswith("GH-") and len(fp.new_number()) == 13


def test_reservation_items_sum_to_payment():
    c = cfg(vat_percent=12, items={**BASE["items"], "penalty": {"class_code": "10499001001000000", "package_code": "1"}})
    items = fp.reservation_items({"room": 300_000, "penalty": 50_000.4}, 350_000, c, "xona 12")
    assert sum(i["price"] for i in items) == 35_000_000        # aynan to'lov summasi (tiyin)
    room = next(i for i in items if i["name"].startswith("Mehmonxona"))
    penalty = next(i for i in items if i["name"].startswith("Jarima"))
    assert room["name"].endswith("— xona 12") and room["amount"] == 1000
    assert penalty["classCode"] == "10499001001000000" and room["classCode"] == "10406003004000000"
    assert room["vat"] == fp.vat_of(room["price"], 12) and room["vatPercent"] == 12
    # Taqsimot noma'lum bo'lsa — bitta yashash pozitsiyasi
    only = fp.reservation_items({}, 120_000, cfg())
    assert len(only) == 1 and only[0]["price"] == 12_000_000 and only[0]["vat"] == 0
    # Xizmat kodi kiritilmagan — yashash kodiga tushadi
    assert fp.item_codes(cfg(), "service") == ("10406003004000000", "1506556")


def test_shop_items_partial_payment_and_own_codes():
    rows = [
        {"name": "Suv 0.5", "quantity": 2, "total": 10_000, "ikpu": "02201001001001001", "package": "1254782"},
        {"name": "Shokolad", "quantity": 1, "total": 15_001, "ikpu": None, "package": None},
    ]
    full = fp.shop_items(rows, 25_001, 25_001, cfg())
    assert [i["price"] for i in full] == [1_000_000, 1_500_100] and full[0]["amount"] == 2000
    assert full[0]["classCode"] == "02201001001001001" and full[1]["classCode"] == "10406003004000000"
    half = fp.shop_items(rows, 10_000, 25_001, cfg())
    assert sum(i["price"] for i in half) == 1_000_000          # yaxlitlash farqi oxirgi pozitsiyada


def test_refund_items_and_request():
    c = cfg(vat_percent=12)
    sale = fp.reservation_items({"room": 300_000}, 300_000, c)
    part = fp.refund_items(sale, 100_000, c)
    assert sum(i["price"] for i in part) == 10_000_000 and part[0]["vat"] == fp.vat_of(10_000_000, 12)
    assert fp.refund_items(sale, 900_000, c)[0]["price"] == 30_000_000   # asl chekdan oshmaydi

    req = fp.build_request(
        c, kind="refund", number="GH-1", items=part, cash=100_000, card=0, staff_name="Aziz",
        client_name="Ali", note="Qaytarim",
        refund_info={"terminal_id": "T1", "receipt_seq": "7", "fiscal_datetime": "20261126", "fiscal_sign": "S"},
    )
    assert req["method"] == "refund" and "token" not in req
    assert req["refundInfo"] == {"terminalId": "T1", "receiptSeq": "7", "dateTime": "20261126", "fiscalSign": "S"}
    assert req["params"]["receivedCash"] == 10_000_000 and req["params"]["receivedCard"] == 0
    assert req["params"]["paycheckNumber"] == req["params"]["externalId"] == "GH-1"
    assert req["companyINN"] == "308839903" and req["staffName"] == "Aziz"
    # Sotuv: chekni o'zimiz chiqaramiz — fastSale; E-POS chop etsa — sale
    assert fp.sale_method(c, "sale") == "fastSale" and fp.sale_method(cfg(epos_prints=True), "sale") == "sale"
    assert fp.sale_method(c, "advance") == "advance"


def test_parse_result_shapes():
    fast = {"error": False, "message": {"terminalId": "UZ1", "receiptSeq": "2478", "dateTime": "20260305235115",
                                        "fiscalSign": "256", "qrCodeURL": "https://ofd.soliq.uz/check?t=UZ1"}}
    sale = {"error": False, "paycheck": "JVBER", "info": {"terminalId": "UZ1", "receiptSeq": "2469",
                                                          "fiscalSign": "222", "dateTime": "2026", "qrCodeURL": "u"}}
    exists = {"error": False, "data": {"exists": True, "receipt": {
        "terminal_id": "NA1", "receipt_seq": 3457, "fiscal_sign": "921", "date_time": "20260120", "qr_code_url": "q"}}}
    assert fp.parse_result(fast)["qr_url"].startswith("https://ofd.soliq.uz") and fp.parse_result(fast)["ok"]
    assert fp.parse_result(sale)["receipt_seq"] == "2469"
    assert fp.parse_result(exists) == {
        "ok": True, "error": None, "terminal_id": "NA1", "receipt_seq": "3457", "fiscal_sign": "921",
        "fiscal_datetime": "20260120", "qr_url": "q",
    }
    err = fp.parse_result({"error": True, "message": " EN: cannot connect card \n Ru: Модуль не подключен"})
    assert err["ok"] is False and err["error"] == "EN: cannot connect card Ru: Модуль не подключен"
    assert fp.parse_result({"error": False, "message": "SUCCESS"})["ok"] is False   # fiskal belgi yo'q
    assert fp.parse_result(None)["ok"] is False


# --- 2. Navbat (SQLite) -----------------------------------------------------


def run(body, settings: dict | None = ENABLED):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [
            Hotel.__table__, Branch.__table__, User.__table__, Guest.__table__, Room.__table__,
            Reservation.__table__, Invoice.__table__, InvoiceLineItem.__table__, Payment.__table__,
            ShopProduct.__table__, ShopBatch.__table__, ShopSale.__table__, ShopSaleItem.__table__,
            ShopSalePayment.__table__, FiscalReceipt.__table__, FiscalReceiptLink.__table__,
        ]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        window = fs.PENDING_WINDOW
        fs.PENDING_WINDOW = timedelta(days=3650)          # namunaviy to'lovlar 2026-noyabr boshida
        try:
            async with maker() as session:
                # Qaytarim — manfiy to'lov; modeldagi `amount > 0` CHECK faqat SQLite jadvalida bor
                await session.execute(text("PRAGMA ignore_check_constraints = 1"))
                ids = await seed(session)
                session.add(Branch(
                    id=BRANCH, hotel_id=HOTEL, name="Asosiy", code="MAIN", is_main_branch=True,
                    settings={fp.SETTINGS_KEY: settings} if settings else {},
                ))
                await session.commit()
                await body(session, ids)
        finally:
            fs.PENDING_WINDOW = window
            await engine.dispose()

    asyncio.run(main())


async def invoice_of(session) -> Invoice:
    from sqlalchemy import select

    return (await session.execute(select(Invoice))).scalars().first()


def test_disabled_branch_writes_nothing():
    async def body(session, ids):
        svc = FiscalService(session)
        assert (await svc.pending(HOTEL)) == {"enabled": False, "targets": [], "receipts": [], "count": 0}
        invoice = await invoice_of(session)
        assert await svc.prepare_reservation(HOTEL, ids["aziz"], invoice_id=invoice.id) == []
        assert (await svc.list(HOTEL))["total"] == 0

    run(body, settings=None)


def test_settings_enable_requires_fields_and_stamps_time():
    async def body(session, ids):
        svc = FiscalService(session)
        with pytest.raises(ValidationException):
            await svc.save_settings(HOTEL, {"enabled": True})
        saved = await svc.save_settings(HOTEL, {**BASE, "enabled": True})
        assert saved["enabled"] and saved["enabled_at"]
        stamp = saved["enabled_at"]
        # Qayta saqlash yoqilgan vaqtni o'zgartirmaydi
        again = await svc.save_settings(HOTEL, {"vat_percent": 12})
        assert again["enabled_at"] == stamp and again["vat_percent"] == 12 and again["company_inn"] == "308839903"
        # O'chirib-yoqilsa — vaqt yangilanadi (oradagi to'lovlar fiskallashmaydi)
        off = await svc.save_settings(HOTEL, {"enabled": False})
        assert off["enabled"] is False and off["enabled_at"] is None
        assert (await svc.pending(HOTEL))["enabled"] is False

    run(body, settings=None)


def test_only_payments_after_enabling_are_queued():
    async def body(session, ids):
        svc = FiscalService(session)
        pending = await svc.pending(HOTEL)
        # 1- va 2-noyabr to'lovlari yoqilishidan oldin; 4-noyabrdagilar keyin
        assert pending["count"] == 1 and pending["targets"][0]["source"] == "RESERVATION"
        invoice = await invoice_of(session)
        receipts = await svc.prepare_reservation(HOTEL, ids["aziz"], invoice_id=invoice.id)
        assert len(receipts) == 1 and float(receipts[0].amount) == 120_000.0
        assert (await svc.pending(HOTEL))["targets"] == []

    run(body, settings={**ENABLED, "enabled_at": at(3, 12).isoformat()})


def test_reservation_receipt_groups_payments_and_skips_transfer():
    async def body(session, ids):
        invoice = await invoice_of(session)
        session.add(Payment(
            hotel_id=HOTEL, branch_id=BRANCH, invoice_id=invoice.id, payment_number="PAY-9", amount=40_000,
            payment_method="BANK_TRANSFER", payment_date=date(2026, 11, 4), created_by=ids["aziz"],
            created_at=at(4, 12),
        ))
        await session.commit()
        svc = FiscalService(session)
        assert (await svc.pending(HOTEL))["count"] == 1
        receipts = await svc.prepare_reservation(HOTEL, ids["aziz"], reservation_id=invoice.reservation_id)
        by = {r.status: r for r in receipts}
        assert set(by) == {"skipped", "pending"}
        assert float(by["skipped"].amount) == 40_000.0 and by["skipped"].payload is None
        r = by["pending"]
        # P1 300 000 naqd + P2 50 000 karta + P3 100 000 online + P4 20 000 naqd
        assert (float(r.amount), float(r.cash_amount), float(r.card_amount)) == (470_000.0, 320_000.0, 150_000.0)
        assert r.kind == "sale" and r.title == "RES-1 · xona 12" and r.number.startswith("GH-")
        params = r.payload["params"]
        assert r.payload["method"] == "fastSale" and r.payload["staffName"] == "Aziz Karimov"
        assert params["clientName"] == "Ali Valiyev" and params["note"] == "Bron RES-1"
        assert (params["receivedCash"], params["receivedCard"]) == (32_000_000, 15_000_000)
        assert sum(i["price"] for i in params["items"]) == 47_000_000
        names = [i["name"] for i in params["items"]]
        assert any(n.startswith("Jarima") for n in names) and names[-1].startswith("Mehmonxona xizmati")
        # Ikkinchi marta — hech narsa (to'lovlar allaqachon chekda)
        assert await svc.prepare_reservation(HOTEL, ids["aziz"], invoice_id=invoice.id) == []
        assert (await svc.pending(HOTEL))["targets"] == []
        assert (await svc.pending(HOTEL))["receipts"][0]["id"] == str(r.id)

    run(body)


def test_claim_complete_and_refund_links_original():
    async def body(session, ids):
        svc = FiscalService(session)
        invoice = await invoice_of(session)
        (sale,) = await svc.prepare_reservation(HOTEL, ids["aziz"], invoice_id=invoice.id)
        claimed = await svc.claim(HOTEL, sale.id)
        assert claimed["status"] == "sent" and claimed["attempts"] == 1 and claimed["verify"] is False
        assert claimed["request"]["token"] == fp.EPOS_TOKEN and claimed["request"]["method"] == "fastSale"
        # Shu paytda boshqa qurilma ola olmaydi
        with pytest.raises(ConflictException):
            await svc.claim(HOTEL, sale.id)
        with pytest.raises(ValidationException):
            await svc.complete(HOTEL, sale.id, {"fiscal_sign": " "})
        done = await svc.complete(HOTEL, sale.id, {
            "terminal_id": "UZ1", "receipt_seq": "77", "fiscal_sign": "123456", "fiscal_datetime": "20261104120000",
            "qr_url": "https://ofd.soliq.uz/check?t=UZ1&r=77",
        })
        assert done.status == "done" and done.completed_at and done.qr_url.endswith("r=77")
        assert (await svc.pending(HOTEL))["count"] == 0
        # Bajarilgan chekni qayta olish — so'rovsiz (qayta yuborilmaydi)
        assert (await svc.claim(HOTEL, sale.id))["request"] is None
        assert (await svc.for_target(HOTEL, reservation_id=invoice.reservation_id))[0]["fiscal_sign"] == "123456"

        # Qaytarim: manfiy to'lov → asl chekka bog'langan refund
        session.add(Payment(
            hotel_id=HOTEL, branch_id=BRANCH, invoice_id=invoice.id, payment_number="PAY-R", amount=-30_000,
            payment_method="CASH", payment_date=date(2026, 11, 5), created_by=ids["aziz"], created_at=at(5, 10),
        ))
        await session.commit()
        (refund,) = await svc.prepare_reservation(HOTEL, ids["aziz"], invoice_id=invoice.id)
        assert refund.kind == "refund" and refund.status == "pending" and refund.refund_of_id == sale.id
        assert refund.payload["method"] == "refund"
        assert refund.payload["refundInfo"] == {
            "terminalId": "UZ1", "receiptSeq": "77", "dateTime": "20261104120000", "fiscalSign": "123456",
        }
        assert refund.payload["params"]["receivedCash"] == 3_000_000
        assert sum(i["price"] for i in refund.payload["params"]["items"]) == 3_000_000

    run(body)


def test_refund_without_original_is_skipped_and_failed_can_retry():
    async def body(session, ids):
        svc = FiscalService(session)
        invoice = await invoice_of(session)
        session.add(Payment(
            hotel_id=HOTEL, branch_id=BRANCH, invoice_id=invoice.id, payment_number="PAY-R", amount=-10_000,
            payment_method="CASH", payment_date=date(2026, 11, 5), created_by=ids["aziz"], created_at=at(5, 10),
        ))
        await session.commit()
        receipts = await svc.prepare_reservation(HOTEL, ids["aziz"], invoice_id=invoice.id)
        refund = next(r for r in receipts if r.kind == "refund")
        assert refund.status == "skipped" and "Asl fiskal chek topilmadi" in refund.error

        sale = next(r for r in receipts if r.kind == "sale")
        await svc.claim(HOTEL, sale.id)
        failed = await svc.fail(HOTEL, sale.id, " EN: cannot connect card \n Ru: Модуль не подключен ")
        assert failed.status == "failed" and failed.error == "EN: cannot connect card Ru: Модуль не подключен"
        assert (await svc.pending(HOTEL))["receipts"][0]["status"] == "failed"
        # Qayta urinish: avval yuborilgan bo'lishi mumkin — tekshirish kerak
        again = await svc.claim(HOTEL, sale.id)
        assert again["attempts"] == 2 and again["verify"] is True
        # "Yuborilmoqda" holatida qotib qolgan chek 90 soniyadan keyin qayta olinadi
        row = await session.get(FiscalReceipt, sale.id)
        row.sent_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()
        assert (await svc.pending(HOTEL))["receipts"][0]["status"] == "sent"
        assert (await svc.claim(HOTEL, sale.id))["attempts"] == 3
        dismissed = await svc.dismiss(HOTEL, sale.id, "Kassa almashtirildi")
        assert dismissed.status == "skipped" and dismissed.error == "Kassa almashtirildi"
        assert (await svc.pending(HOTEL))["count"] == 0
        listed = await svc.list(HOTEL, status="skipped")
        assert listed["total"] == 2 and {i["kind"] for i in listed["items"]} == {"sale", "refund"}

    run(body)


def test_advance_for_prepayment_when_configured():
    async def body(session, ids):
        from sqlalchemy import select

        reservation = (await session.execute(select(Reservation))).scalars().first()
        reservation.status = "CONFIRMED"
        await session.commit()
        invoice = await invoice_of(session)
        (r,) = await FiscalService(session).prepare_reservation(HOTEL, ids["aziz"], invoice_id=invoice.id)
        assert r.kind == "advance" and r.payload["method"] == "advance"

    run(body, settings={**ENABLED, "prepayment_as_advance": True})


def test_shop_sale_receipt_and_cancel_refund():
    async def body(session, ids):
        product = ShopProduct(hotel_id=HOTEL, branch_id=BRANCH, name="Suv", ikpu_code="02201001001001001",
                              package_code="1254782", created_by=ids["aziz"])
        other = ShopProduct(hotel_id=HOTEL, branch_id=BRANCH, name="Shokolad", created_by=ids["aziz"])
        session.add_all([product, other])
        await session.flush()
        sale = ShopSale(hotel_id=HOTEL, branch_id=BRANCH, total_amount=25_000, status="PAID",
                        paid_amount=25_000, created_by=ids["aziz"])
        session.add(sale)
        await session.flush()
        session.add_all([
            ShopSaleItem(sale_id=sale.id, product_id=product.id, product_name="Suv", quantity=2,
                         unit_price=5_000, total_price=10_000),
            ShopSaleItem(sale_id=sale.id, product_id=other.id, product_name="Shokolad", quantity=1,
                         unit_price=15_000, total_price=15_000),
            ShopSalePayment(hotel_id=HOTEL, branch_id=BRANCH, sale_id=sale.id, amount=20_000,
                            payment_method="CASH", paid_at=at(4, 15), created_by=ids["aziz"]),
            ShopSalePayment(hotel_id=HOTEL, branch_id=BRANCH, sale_id=sale.id, amount=5_000,
                            payment_method="CARD", paid_at=at(4, 15), created_by=ids["aziz"]),
        ])
        await session.commit()
        svc = FiscalService(session)
        targets = (await svc.pending(HOTEL))["targets"]
        assert {"source": "SHOP", "sale_id": str(sale.id)} in targets
        (r,) = await svc.prepare_shop(HOTEL, ids["aziz"], sale.id)
        items = r.payload["params"]["items"]
        assert [(i["name"], i["price"], i["amount"]) for i in items] == [
            ("Suv", 1_000_000, 2000), ("Shokolad", 1_500_000, 1000),
        ]
        assert items[0]["classCode"] == "02201001001001001" and items[1]["classCode"] == "10406003004000000"
        assert (float(r.cash_amount), float(r.card_amount)) == (20_000.0, 5_000.0) and r.source == "SHOP"
        assert await svc.prepare_shop(HOTEL, ids["aziz"], sale.id) == []

        # Fiskallashmagan sotuv bekor qilinsa — chek navbatdan olinadi
        assert await svc.queue_sale_cancel(HOTEL, ids["aziz"], sale.id) == 0
        assert (await session.get(FiscalReceipt, r.id)).status == "skipped"
        # Fiskallashgan sotuv bekor qilinsa — qaytarim cheki navbatga qo'yiladi
        row = await session.get(FiscalReceipt, r.id)
        row.status, row.fiscal_sign, row.terminal_id, row.receipt_seq = "done", "999", "UZ1", "5"
        await session.commit()
        assert await svc.queue_sale_cancel(HOTEL, ids["aziz"], sale.id) == 1
        refund = next(x for x in (await svc.pending(HOTEL))["receipts"] if x["kind"] == "refund")
        assert refund["refund_of_id"] == str(r.id) and refund["amount"] == 25_000.0
        # Ikkinchi marta qaytarim qo'yilmaydi
        assert await svc.queue_sale_cancel(HOTEL, ids["aziz"], sale.id) == 0
        assert serialize(row)["status"] == "done"

    run(body)


def test_relay_guards():
    async def body(session, ids):
        svc = FiscalService(session)
        with pytest.raises(ValidationException):            # brauzer rejimida relay yo'q
            await svc.relay(HOTEL, {"method": "checkStatus"})

    run(body)

    async def server(session, ids):
        svc = FiscalService(session)
        with pytest.raises(ValidationException):            # ruxsat etilmagan usul
            await svc.relay(HOTEL, {"method": "setCashNumber"})
        with pytest.raises(ValidationException):            # serverning o'z tarmog'iga yo'l yo'q
            await svc.relay(HOTEL, {"method": "checkStatus"})

    run(server, settings={**ENABLED, "transport": "server", "url": "http://127.0.0.1:8347/uzpos"})
    with pytest.raises(ValidationException):
        fs.assert_public_url("ftp://example.com")
    with pytest.raises(ValidationException):
        fs.assert_public_url("http://10.0.0.5:8347/uzpos")
