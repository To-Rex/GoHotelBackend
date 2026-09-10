"""Vazifalarni avtomatik yakunlash — umumiy o'chirgich.

Sozlamalarda "Vazifalarni avtomatik yakunlash" o'chirilsa rejalashtiruvchi
hech qanday vazifani o'zi yopmasligi kerak: xona holati faqat farrosh yoki
menejer/admin vazifani yakunlaganda o'zgaradi. Tur bo'yicha daqiqalar esa
saqlanib qoladi — qayta yoqilganda tiklanadi.
"""
from app.application.services.housekeeping_service import (
    HK_AUTO_COMPLETE_DEFAULTS,
    effective_auto_complete_minutes,
    resolve_auto_complete_enabled,
    resolve_auto_complete_minutes,
)


def test_enabled_by_default_keeps_old_behaviour():
    for settings in (None, {}, {"hk_auto_complete": {}}, {"hk_auto_complete": {"CLEANING": 45}}):
        assert resolve_auto_complete_enabled(settings) is True
    assert effective_auto_complete_minutes({"hk_auto_complete": {"CLEANING": 45}}, "CLEANING") == 45
    assert effective_auto_complete_minutes({}, "MAINTENANCE") == HK_AUTO_COMPLETE_DEFAULTS["MAINTENANCE"]


def test_disabled_means_never_auto_complete_but_minutes_are_kept():
    settings = {"hk_auto_complete": {"enabled": False, "CLEANING": 45, "INSPECTION": 0}}
    assert resolve_auto_complete_enabled(settings) is False
    # Rejalashtiruvchi ko'radigan qiymat — hamma tur uchun 0
    for task_type in HK_AUTO_COMPLETE_DEFAULTS:
        assert effective_auto_complete_minutes(settings, task_type) == 0
    # Sozlamalar sahifasi ko'radigan qiymatlar — saqlangan holicha
    assert resolve_auto_complete_minutes(settings, "CLEANING") == 45
    assert resolve_auto_complete_minutes(settings, "DEEP_CLEANING") == HK_AUTO_COMPLETE_DEFAULTS["DEEP_CLEANING"]


def test_enabled_flag_tolerates_string_values():
    assert resolve_auto_complete_enabled({"hk_auto_complete": {"enabled": "false"}}) is False
    assert resolve_auto_complete_enabled({"hk_auto_complete": {"enabled": "0"}}) is False
    assert resolve_auto_complete_enabled({"hk_auto_complete": {"enabled": "true"}}) is True
    assert resolve_auto_complete_enabled({"hk_auto_complete": {"enabled": 1}}) is True


def test_per_type_zero_still_disables_only_that_type_when_enabled():
    settings = {"hk_auto_complete": {"enabled": True, "CLEANING": 0}}
    assert effective_auto_complete_minutes(settings, "CLEANING") == 0
    assert effective_auto_complete_minutes(settings, "MAINTENANCE") == HK_AUTO_COMPLETE_DEFAULTS["MAINTENANCE"]
