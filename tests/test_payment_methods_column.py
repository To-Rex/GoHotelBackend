"""Shaxsiy hisobot — "Bronlarim"dagi "To'lov turi" ustuni.

Bron bo'yicha to'lovlar usul bo'yicha yig'iladi. Nozik joylar: eski usul
kodlari kanonik ustunga tushishi, qaytarim (manfiy to'lov) yig'indini
kamaytirishi va to'liq qaytarilgan usul ro'yxatdan chiqishi, tartib —
summasi kattasi birinchi.
"""
from decimal import Decimal

from app.application.services.personal_report_service import (
    METHODS,
    summarize_payment_methods,
)


def test_single_method():
    assert summarize_payment_methods([("CASH", 250000)]) == [
        {"method": "cash", "amount": 250000.0}
    ]


def test_split_payment_is_ordered_by_amount_desc():
    """Bo'lib to'langan bron: katta summa birinchi turadi."""
    out = summarize_payment_methods([("CARD", 100000), ("CASH", 150000)])
    assert out == [
        {"method": "cash", "amount": 150000.0},
        {"method": "card", "amount": 100000.0},
    ]


def test_legacy_codes_collapse_into_canonical_columns():
    """CREDIT_CARD + DEBIT_CARD — ikkalasi ham "card"; ustun ikkilanmaydi."""
    out = summarize_payment_methods(
        [("CREDIT_CARD", 50000), ("DEBIT_CARD", 30000), ("TRANSFER", 20000)]
    )
    assert out == [
        {"method": "card", "amount": 80000.0},
        {"method": "bank_transfer", "amount": 20000.0},
    ]


def test_refund_reduces_and_fully_refunded_method_disappears():
    """Qaytarim manfiy to'lov — usul nolga tushsa "Naqd 0" ko'rinmasin."""
    out = summarize_payment_methods(
        [("CASH", 100000), ("CASH", -100000), ("CARD", 40000), ("CARD", -10000)]
    )
    assert out == [{"method": "card", "amount": 30000.0}]


def test_unknown_or_missing_code_goes_to_other():
    out = summarize_payment_methods([("CRYPTO", 5000), (None, 1000)])
    assert out == [{"method": "other", "amount": 6000.0}]


def test_no_payments_gives_empty_list():
    assert summarize_payment_methods([]) == []


def test_equal_amounts_follow_column_order():
    """Teng summalar — hisobot ustunlari tartibida (naqd, karta, online...)."""
    out = summarize_payment_methods([("BANK_TRANSFER", 1000), ("CASH", 1000)])
    assert [o["method"] for o in out] == ["cash", "bank_transfer"]
    assert METHODS.index("cash") < METHODS.index("bank_transfer")


def test_decimal_inputs_are_accepted():
    out = summarize_payment_methods([("CASH", Decimal("1234.50"))])
    assert out == [{"method": "cash", "amount": 1234.5}]
