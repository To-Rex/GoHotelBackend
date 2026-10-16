"""Kassa topshirilishida admin/menejer tasdig'i (sozlama bilan).

Yoqilganda: xodim topshiradi -> sessiya PENDING_HANDOVER + `pending_approval`
bilan TASDIQ kutadi; keyingi xodim parol bilan qabul qila olmaydi; admin/
menejer tasdiqlagach sessiya yopiladi va (kassir ishda davom etsa) yangi
kassa 0 so'mdan ochiladi. Sozlama O'CHIQ bo'lsa — hozirgi oqim 100% o'zgarmaydi.

Bazasiz: sessiya o'rinbosarlari bilan.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.services import shift_service as ss
from app.application.services.notification_service import NotificationService
from app.application.services.shift_service import ShiftService, resolve_shift_settings
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
)

HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()
DILNOZA, AZIZ, ADMIN_ID = (uuid.uuid4() for _ in range(3))
T0 = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)

ADMIN = {"id": str(ADMIN_ID), "user_type": "ADMIN", "permissions": []}
MANAGER = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["shift.force_close"]}
RECEPTION = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["reservation.create"]}


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class Db:
    def __init__(self, payments=(), shop=(), expenses=(), sessions=(), user_obj=None, session_obj=None):
        self.payments = list(payments)
        self.shop = list(shop)
        self.expenses = list(expenses)
        self.sessions = list(sessions)
        self.user_obj = user_obj
        self.session_obj = session_obj
        self.added: list = []
        self.flushes = 0
        self.sql: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "FROM shop_sale_payments" in sql:
            return Rows(self.shop)
        if "FROM payments" in sql:
            return Rows(self.payments)
        if "FROM expenses" in sql:
            return Rows(self.expenses)
        if "FROM shift_sessions" in sql:
            return Rows(self.sessions)
        if "FROM users" in sql:
            return Rows([])
        raise AssertionError(sql)

    async def get(self, model, key):
        if model.__name__ == "User":
            return self.user_obj
        if model.__name__ == "ShiftSession":
            return self.session_obj
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1


def make_session(
    user=DILNOZA,
    status="ACTIVE",
    funded_hours_ago=1,
    opening=100000,
    pending_approval=None,
    continue_after_end=False,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hotel_id=HOTEL,
        branch_id=BRANCH,
        user_id=user,
        status=status,
        started_at=T0 - timedelta(hours=funded_hours_ago),
        ended_at=None,
        opening_cash=Decimal(str(opening)),
        expected_cash=None,
        counted_cash=None,
        cash_diff=None,
        accepted_by=None,
        accepted_at=None,
        force_closed=False,
        corrections=None,
        notes=None,
        closed_by=None,
        continue_after_end=continue_after_end,
        pending_approval=pending_approval,
    )


def make_service(monkeypatch, *, settings=None, s=None, user_obj=None, notify=None):
    svc = ShiftService.__new__(ShiftService)
    svc.session = Db(
        payments=[("CASH", 50000)],
        sessions=[s] if s else [],
        user_obj=user_obj or SimpleNamespace(first_name="Dilnoza", last_name="R"),
        session_obj=s,
    )

    async def get_settings(hotel_id):
        return {
            "mode": "cash",
            "day_close": "00:00",
            "day_close_required": True,
            "handover_approval": False,
            **(settings or {}),
        }

    svc.get_settings = get_settings

    approvals: list[tuple[str, str]] = []

    async def fake_notify_approvers(session, title, body):
        approvals.append((title, body))

    svc._notify_approvers = fake_notify_approvers

    if notify is not None:
        async def fake_notify(self, **kw):
            notify.append(kw)

        monkeypatch.setattr(NotificationService, "notify", fake_notify)

    return svc, approvals


# ------------------------------------------------------------ sozlama --

def test_approval_setting_default_off_and_resolves():
    assert resolve_shift_settings(None)["handover_approval"] is False
    assert resolve_shift_settings({})["handover_approval"] is False
    assert (
        resolve_shift_settings({"shift": {"handover_approval": True}})["handover_approval"]
        is True
    )
    # Buzuq qiymat cheklovni jimgina yoqib qo'ymasin
    assert (
        resolve_shift_settings({"shift": {"handover_approval": "yes"}})["handover_approval"]
        is False
    )


# ------------------------------------------------- close_cash va end_shift --

def test_close_cash_with_approval_waits_for_confirmation(monkeypatch):
    s = make_session(continue_after_end=True)
    svc, approvals = make_service(
        monkeypatch, settings={"handover_approval": True}, s=s
    )

    report = asyncio.run(svc.close_cash(HOTEL, {"id": str(DILNOZA)}, 155000.0))

    assert s.status == "PENDING_HANDOVER"
    assert s.pending_approval == "close_cash"
    assert float(s.counted_cash) == 155000.0
    assert svc.session.added == []  # yangi sessiya hali ochilmaydi
    assert report["pending_approval"] == "close_cash"
    assert "new_session" not in report
    assert approvals and "tasdiq" in approvals[0][0]
    assert "Dilnoza" in approvals[0][1]


def test_close_cash_without_approval_keeps_current_flow(monkeypatch):
    s = make_session(continue_after_end=True)
    svc, approvals = make_service(monkeypatch, s=s)

    report = asyncio.run(svc.close_cash(HOTEL, {"id": str(DILNOZA)}, 155000.0))

    assert s.status == "CLOSED"
    assert s.pending_approval is None
    assert len(svc.session.added) == 1
    new_s = svc.session.added[0]
    assert new_s.status == "ACTIVE" and float(new_s.opening_cash) == 0
    assert new_s.continue_after_end is True
    assert report["new_session"]["id"] == str(new_s.id)
    assert approvals == []


def test_end_shift_with_approval_marks_pending(monkeypatch):
    s = make_session(user=DILNOZA)
    svc, approvals = make_service(
        monkeypatch, settings={"handover_approval": True}, s=s
    )

    report = asyncio.run(svc.end_shift(HOTEL, {"id": str(DILNOZA)}, 150000.0))

    assert s.status == "PENDING_HANDOVER"
    assert s.pending_approval == "end_shift"
    assert report["pending_approval"] == "end_shift"
    assert approvals and "tasdiq" in approvals[0][0]


def test_end_shift_without_approval_unchanged(monkeypatch):
    s = make_session()
    svc, approvals = make_service(monkeypatch, s=s)

    asyncio.run(svc.end_shift(HOTEL, {"id": str(DILNOZA)}, 150000.0))

    assert s.status == "PENDING_HANDOVER"
    assert s.pending_approval is None
    assert approvals == []


# ---------------------------------------------------------- accept bloki --

def _accept_service(monkeypatch, pending, user_obj):
    svc = ShiftService.__new__(ShiftService)
    svc.session = Db(user_obj=user_obj)

    async def fake_open_sessions(hotel_id, branch_id):
        return [(pending, SimpleNamespace(first_name="Aziz", last_name="K"))]

    svc._open_sessions = fake_open_sessions
    monkeypatch.setattr(ss, "verify_password", lambda pw, h: True)
    return svc


def test_accept_blocked_while_approval_pending(monkeypatch):
    pending = make_session(user=DILNOZA, status="PENDING_HANDOVER", pending_approval="end_shift")
    pending.counted_cash = Decimal("150000")
    user_obj = SimpleNamespace(id=AZIZ, password_hash="x", first_name="Aziz", last_name="K")
    svc = _accept_service(monkeypatch, pending, user_obj)

    with pytest.raises(ConflictException) as exc:
        asyncio.run(svc.accept_shift(HOTEL, {"id": str(AZIZ)}, "parol"))

    assert exc.value.error_code == "APPROVAL_REQUIRED"
    assert pending.status == "PENDING_HANDOVER"


def test_accept_still_works_when_approval_not_required(monkeypatch):
    pending = make_session(user=DILNOZA, status="PENDING_HANDOVER", pending_approval=None)
    pending.counted_cash = Decimal("150000")
    user_obj = SimpleNamespace(id=AZIZ, password_hash="x", first_name="Aziz", last_name="K")
    svc = _accept_service(monkeypatch, pending, user_obj)

    asyncio.run(svc.accept_shift(HOTEL, {"id": str(AZIZ)}, "parol"))

    assert pending.status == "CLOSED"
    assert pending.accepted_by == AZIZ
    assert len(svc.session.added) == 1
    assert float(svc.session.added[0].opening_cash) == 150000.0


# ------------------------------------------------------------- tasdiqlash --

def test_approve_requires_permission(monkeypatch):
    s = make_session(status="PENDING_HANDOVER", pending_approval="close_cash")
    svc, _ = make_service(monkeypatch, s=s)

    with pytest.raises(ForbiddenException):
        asyncio.run(svc.approve_handover(HOTEL, RECEPTION, s.id))


def test_approve_only_pending_sessions(monkeypatch):
    active = make_session(status="ACTIVE")
    svc, _ = make_service(monkeypatch, s=active)
    with pytest.raises(ConflictException) as exc:
        asyncio.run(svc.approve_handover(HOTEL, ADMIN, active.id))
    assert exc.value.error_code == "SHIFT_NOT_PENDING"

    # Oddiy (tasdiqsiz) topshirilgan sessiya ham tasdiqlanmaydi
    plain = make_session(status="PENDING_HANDOVER", pending_approval=None)
    svc2, _ = make_service(monkeypatch, s=plain)
    with pytest.raises(ConflictException):
        asyncio.run(svc2.approve_handover(HOTEL, ADMIN, plain.id))


def test_approve_close_cash_opens_new_zero_session(monkeypatch):
    s = make_session(
        status="PENDING_HANDOVER",
        pending_approval="close_cash",
        continue_after_end=True,
    )
    s.counted_cash = Decimal("155000")
    s.expected_cash = Decimal("150000")
    s.cash_diff = Decimal("5000")
    notified: list[dict] = []
    svc, _ = make_service(monkeypatch, s=s, notify=notified)

    report = asyncio.run(svc.approve_handover(HOTEL, ADMIN, s.id, notes="Sanab oldim"))

    assert s.status == "CLOSED"
    assert s.pending_approval is None
    assert s.accepted_by == ADMIN_ID and s.accepted_at is not None
    assert "tasdiqlandi" in s.notes and "Sanab oldim" in s.notes
    # Kassir ishda davom etadi — yangi sessiya 0 dan
    assert len(svc.session.added) == 1
    new_s = svc.session.added[0]
    assert new_s.user_id == s.user_id and new_s.status == "ACTIVE"
    assert float(new_s.opening_cash) == 0 and new_s.continue_after_end is True
    assert report["new_session"]["id"] == str(new_s.id)
    assert report["accepted_at"]
    # Kassirga xabar
    assert notified and notified[0]["user_id"] == s.user_id
    assert "tasdiqlandi" in notified[0]["title"].lower()


def test_approve_end_shift_does_not_open_new_session(monkeypatch):
    s = make_session(status="PENDING_HANDOVER", pending_approval="end_shift")
    s.counted_cash = Decimal("150000")
    s.expected_cash = Decimal("150000")
    s.cash_diff = Decimal("0")
    notified: list[dict] = []
    svc, _ = make_service(monkeypatch, s=s, notify=notified)

    report = asyncio.run(svc.approve_handover(HOTEL, MANAGER, s.id))

    assert s.status == "CLOSED"
    assert s.accepted_by is not None
    assert svc.session.added == []
    assert report["new_session"] is None
    assert notified and notified[0]["user_id"] == s.user_id
