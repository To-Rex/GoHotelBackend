"""Qabulxona to'lov tahriri sozlamasi va qoidalari (payment_edit_settings).

Ishga tushirish: pytest tests/test_payment_edit_settings.py

1. Sozlama yo'q yoki buzuq — standart: o'chiq, chegara 2.
2. Chegara faqat ruxsat etilgan qiymatlardan (1, 2, 3).
3. Tahrirlar soni to'lovning o'z tarixidan: butun va qisman tahrirlar,
   eski `kind` siz yozuvlar ham; ajralgan qatorning `split_from` i emas.
4. Vazifa: admin/super admin, menejer, qabulxona; boshqalar — None.
"""
from app.application.services.payment_edit_settings import (
    ALLOWED_MAX_EDITS,
    DEFAULT_PAYMENT_EDIT_SETTINGS,
    PAYMENT_EDIT_SETTINGS_KEY,
    RECEPTION_DISABLED,
    ReceptionEditRules,
    edit_count,
    editor_role,
    resolve_payment_edit_settings,
)


def test_defaults_when_missing_or_broken():
    assert resolve_payment_edit_settings(None) == DEFAULT_PAYMENT_EDIT_SETTINGS
    assert resolve_payment_edit_settings({}) == DEFAULT_PAYMENT_EDIT_SETTINGS
    assert resolve_payment_edit_settings({PAYMENT_EDIT_SETTINGS_KEY: "x"}) == DEFAULT_PAYMENT_EDIT_SETTINGS
    broken = {PAYMENT_EDIT_SETTINGS_KEY: {"reception_enabled": "ha", "reception_max_edits": "ko'p"}}
    assert resolve_payment_edit_settings(broken) == {"reception_enabled": False, "reception_max_edits": 2}
    out_of_range = {PAYMENT_EDIT_SETTINGS_KEY: {"reception_enabled": True, "reception_max_edits": 9}}
    assert resolve_payment_edit_settings(out_of_range) == {"reception_enabled": True, "reception_max_edits": 2}
    assert RECEPTION_DISABLED.enabled is False and RECEPTION_DISABLED.max_edits == 2


def test_saved_values_are_kept():
    for n in ALLOWED_MAX_EDITS:
        saved = {PAYMENT_EDIT_SETTINGS_KEY: {"reception_enabled": True, "reception_max_edits": str(n)}}
        assert resolve_payment_edit_settings(saved) == {"reception_enabled": True, "reception_max_edits": n}
    rules = ReceptionEditRules.from_settings({PAYMENT_EDIT_SETTINGS_KEY: {"reception_enabled": True, "reception_max_edits": 3}})
    assert rules.enabled is True and rules.max_edits == 3


def test_edit_count_ignores_split_origin_and_garbage():
    assert edit_count(None) == 0
    assert edit_count([]) == 0
    assert edit_count("nope") == 0
    history = [
        {"kind": "full", "old": "CASH", "new": "CARD"},
        {"old": "CARD", "new": "ONLINE"},  # eski yozuv — kind yo'q
        {"kind": "partial", "amount": 5000},
        {"kind": "split_from", "from_payment_number": "PAY-1"},
        "buzuq",
    ]
    assert edit_count(history) == 3


def test_editor_role():
    assert editor_role({"user_type": "ADMIN", "permissions": []}) == "ADMIN"
    assert editor_role({"user_type": "SUPER_ADMIN"}) == "SUPER_ADMIN"
    assert editor_role({"user_type": "EMPLOYEE", "permissions": ["shift.force_close", "finance.payment.create"]}) == "MANAGER"
    assert editor_role({"user_type": "EMPLOYEE", "permissions": ["finance.payment.create"]}) == "RECEPTION"
    assert editor_role({"user_type": "EMPLOYEE", "permissions": ["room.view"]}) is None
    assert editor_role(None) is None
