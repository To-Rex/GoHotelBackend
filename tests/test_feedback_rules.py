"""Talab, taklif va shikoyatlar — kirish qoidalari va holat o'tishlari.

Bazasiz: feedback_service dagi sof funksiyalar sinaladi. Qoidalar router
va frontend (ROUTE_PERMISSIONS["/feedback"]) bilan bir xil bo'lishi kerak —
bu yerda o'zgarsa, u yerda ham o'zgarsin.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.application.services import feedback_service as rules
from app.core.config import settings

# Frontend shablonlari (permissionTemplates.ts) bo'yicha odatiy to'plamlar
RECEPTIONIST = ["reservation.create", "reservation.update", "reservation.view", "guest.view"]
MANAGER = ["reservation.create", "shift.force_close", "employee.create", "housekeeping.task.assign"]
HOUSEKEEPER = ["room.view", "room.status.update", "housekeeping.task.update"]
MAINTENANCE = ["room.view", "housekeeping.task.create", "housekeeping.task.update"]
VIEWER = ["reservation.view", "guest.view", "room.view", "finance.view"]
ACCOUNTANT = ["finance.view", "report.view", "expense.view"]


def user(user_type="EMPLOYEE", permissions=()):
    return {"user_type": user_type, "permissions": list(permissions)}


# ----------------------------------------------------------------- kirish --
def test_admins_can_do_everything():
    for t in ("ADMIN", "SUPER_ADMIN"):
        u = user(t)
        assert rules.can_view(u) and rules.can_create(u)
        assert rules.can_manage(u) and rules.can_delete(u)


def test_reception_works_without_new_codes():
    """Deploy'dan keyin qabulxona darhol yozadi va yopadi — yangi ruxsat shart emas."""
    u = user(permissions=RECEPTIONIST)
    assert rules.can_view(u)
    assert rules.can_create(u)
    assert rules.can_manage(u)
    # Kitobdan o'chirish — qabulxona ishi emas
    assert not rules.can_delete(u)


def test_manager_manages_but_does_not_delete_without_code():
    u = user(permissions=MANAGER)
    assert rules.can_view(u) and rules.can_create(u) and rules.can_manage(u)
    assert not rules.can_delete(u)


def test_cleaner_and_maintenance_see_nothing():
    """Murojaatda mehmon ismi va telefoni bor."""
    for perms in (HOUSEKEEPER, MAINTENANCE):
        u = user(permissions=perms)
        assert not rules.can_view(u)
        assert not rules.can_create(u)
        assert not rules.can_manage(u)


def test_viewer_reads_but_cannot_write():
    u = user(permissions=VIEWER)
    assert rules.can_view(u)
    assert not rules.can_create(u)
    assert not rules.can_manage(u)


def test_dedicated_codes_grant_access_to_other_roles():
    accountant = user(permissions=ACCOUNTANT)
    assert not rules.can_view(accountant)
    assert rules.can_view(user(permissions=ACCOUNTANT + ["feedback.view"]))
    creator = user(permissions=ACCOUNTANT + ["feedback.create"])
    assert rules.can_create(creator) and not rules.can_manage(creator)
    manager = user(permissions=ACCOUNTANT + ["feedback.manage"])
    assert rules.can_manage(manager) and rules.can_delete(manager)


def test_missing_permissions_key_is_not_an_error():
    assert not rules.can_view({"user_type": "EMPLOYEE"})


# --------------------------------------------------------- holat o'tishlari --
@pytest.mark.parametrize(
    "current,new,ok",
    [
        ("NEW", "IN_PROGRESS", True),
        ("NEW", "RESOLVED", True),
        ("NEW", "REJECTED", True),
        ("IN_PROGRESS", "RESOLVED", True),
        ("IN_PROGRESS", "REJECTED", True),
        ("IN_PROGRESS", "NEW", False),
        ("RESOLVED", "IN_PROGRESS", True),  # qayta ochish
        ("REJECTED", "IN_PROGRESS", True),
        ("RESOLVED", "NEW", False),
        ("RESOLVED", "REJECTED", False),
        ("REJECTED", "RESOLVED", False),
        ("NEW", "NEW", False),  # bir xil holat — servis alohida (idempotent) hal qiladi
        ("BOGUS", "RESOLVED", False),
    ],
)
def test_transitions(current, new, ok):
    assert rules.can_transition(current, new) is ok


def test_closing_requires_resolution_text():
    assert rules.requires_resolution("RESOLVED")
    assert rules.requires_resolution("REJECTED")
    assert not rules.requires_resolution("IN_PROGRESS")
    assert not rules.requires_resolution("NEW")


# ---------------------------------------------------------- kun chegaralari --
def test_local_day_bounds_shift_by_hotel_offset():
    offset = timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
    start, end = rules.local_day_bounds(date(2026, 9, 8), date(2026, 9, 8))
    # Mahalliy 00:00 → UTC da offset qadar orqada
    assert start == datetime(2026, 9, 8, tzinfo=timezone.utc) - offset
    assert end - start == timedelta(days=1)


def test_local_day_bounds_open_ended():
    assert rules.local_day_bounds(None, None) == (None, None)
    start, end = rules.local_day_bounds(date(2026, 9, 1), None)
    assert start is not None and end is None
