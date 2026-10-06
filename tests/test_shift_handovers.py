"""Smenadan smenaga o'tgan pullar — `/shifts/handovers`.

Foydalanuvchi talabi: Smenalar sahifasida kassada hozir qancha pul borligi
va smenadan smenaga o'tgan pullar ko'rinsin. Endpoint YANGI — mavjud smena
oqimi (ochish, topshirish, qabul, majburiy yopish) o'zgarmadi.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.services import shift_service as ss
from app.application.services.shift_service import ShiftService
from app.core.config import settings as app_settings
from app.core.exceptions import ForbiddenException, ValidationException
from app.presentation.api.v1 import shifts as shifts_api

HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()
DILNOZA, AZIZ, SARDOR, ADMIN_ID = (uuid.uuid4() for _ in range(4))
NAMES = {DILNOZA: ("Dilnoza", "R"), AZIZ: ("Aziz", "K"), SARDOR: ("Sardor", "T"), ADMIN_ID: ("Admin", "A")}
T0 = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)


def session(user, status, *, ended, counted, expected, accepted_by=None, accepted_at=None,
            force=False, opening=0, started=None, corrections=None, closed_by=None):
    return SimpleNamespace(
        id=uuid.uuid4(), hotel_id=HOTEL, branch_id=BRANCH, user_id=user, status=status,
        started_at=started or (ended - timedelta(hours=8) if ended else T0), ended_at=ended,
        opening_cash=Decimal(str(opening)), expected_cash=None if expected is None else Decimal(str(expected)),
        counted_cash=None if counted is None else Decimal(str(counted)),
        cash_diff=None if counted is None or expected is None else Decimal(str(counted - expected)),
        accepted_by=accepted_by, accepted_at=accepted_at, force_closed=force,
        corrections=corrections, notes=None, closed_by=closed_by or user,
    )


def user(uid):
    first, last = NAMES[uid]
    return SimpleNamespace(id=uid, first_name=first, last_name=last)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self.rows)


class Db:
    def __init__(self, rows, receivers=()):
        self.rows = rows
        self.receivers = list(receivers)
        self.sql = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "JOIN users" in sql:
            return Rows(self.rows)
        if "FROM users" in sql:
            return Rows([user(u) for u in NAMES])
        if "FROM branches" in sql:
            return Rows([SimpleNamespace(id=BRANCH, name="Markaziy")])
        if "FROM shift_sessions" in sql:
            return Rows(self.receivers)
        raise AssertionError(sql)


def service(rows, receivers=(), mode="cash"):
    svc = ShiftService.__new__(ShiftService)
    svc.session = Db(rows, receivers)

    async def settings(hotel_id):
        return {"mode": mode, "day_close": "00:00", "day_close_required": True}

    svc.get_settings = settings
    return svc


ADMIN = {"id": str(ADMIN_ID), "user_type": "ADMIN", "permissions": []}
MANAGER = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["shift.force_close"]}
RECEPTION = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["reservation.create"]}


def test_kind_rules():
    assert ss.handover_kind(SimpleNamespace(status="PENDING_HANDOVER", accepted_by=None, force_closed=False)) == "PENDING"
    assert ss.handover_kind(SimpleNamespace(status="CLOSED", accepted_by=AZIZ, force_closed=True)) == "HANDOVER"
    assert ss.handover_kind(SimpleNamespace(status="CLOSED", accepted_by=None, force_closed=True)) == "FORCE_TAKEN"
    assert ss.handover_kind(SimpleNamespace(status="CLOSED", accepted_by=None, force_closed=False)) == "CASH_OUT"


def test_local_day_bounds():
    start, end = ss.local_day_bounds(date(2026, 10, 5), date(2026, 10, 5))
    offset = timedelta(minutes=app_settings.APP_TZ_OFFSET_MINUTES)
    assert start == datetime(2026, 10, 5, tzinfo=timezone.utc) - offset
    assert end - start == timedelta(days=1)
    assert ss.local_day_bounds(None, None) == (None, None)


def test_chain_of_money_between_shifts():
    accepted_at = T0 + timedelta(hours=1)
    handover = session(DILNOZA, "CLOSED", ended=T0, counted=1_200_000, expected=1_250_000,
                       accepted_by=AZIZ, accepted_at=accepted_at, opening=100_000)
    receiver = SimpleNamespace(id=uuid.uuid4(), user_id=AZIZ, started_at=accepted_at + timedelta(seconds=3),
                               opening_cash=Decimal("1200000"))
    # Boshqa (uzoq) sessiya bog'lanmasin
    other = SimpleNamespace(id=uuid.uuid4(), user_id=AZIZ, started_at=accepted_at - timedelta(minutes=30),
                            opening_cash=Decimal("0"))
    pending = session(AZIZ, "PENDING_HANDOVER", ended=T0 + timedelta(hours=9), counted=800_000, expected=790_000)
    day_close = session(SARDOR, "CLOSED", ended=T0 - timedelta(hours=2), counted=500_000, expected=500_000)
    forced = session(SARDOR, "CLOSED", ended=T0 - timedelta(hours=5), counted=300_000, expected=300_000,
                     force=True, closed_by=ADMIN_ID)
    rows = [(s, user(s.user_id)) for s in (pending, handover, day_close, forced)]
    out = asyncio.run(service(rows, [other, receiver]).get_handovers(HOTEL, ADMIN))

    assert out["mode"] == "cash"
    kinds = {i["id"]: i for i in out["items"]}
    h = kinds[str(handover.id)]
    assert h["kind"] == "HANDOVER"
    assert h["from_user_name"] == "Dilnoza R" and h["to_user_name"] == "Aziz K"
    assert h["counted_cash"] == 1_200_000 and h["cash_diff"] == -50_000
    assert h["received_opening_cash"] == 1_200_000
    assert h["received_session_id"] == str(receiver.id)
    assert h["branch_name"] == "Markaziy"
    assert kinds[str(pending.id)]["kind"] == "PENDING"
    assert kinds[str(day_close.id)]["kind"] == "CASH_OUT"
    f = kinds[str(forced.id)]
    assert f["kind"] == "FORCE_TAKEN" and f["closed_by_name"] == "Admin A"

    s = out["summary"]
    assert s["handed_over_total"] == 1_200_000 and s["handed_over_count"] == 1
    assert s["pending_total"] == 800_000 and s["pending_count"] == 1
    assert s["taken_out_total"] == 800_000 and s["taken_out_count"] == 2
    assert s["shortage_total"] == 50_000 and s["surplus_total"] == 10_000


def test_dates_and_hotel_scope():
    svc = service([])
    asyncio.run(svc.get_handovers(HOTEL, MANAGER, date(2026, 10, 1), date(2026, 10, 5)))
    main = svc.session.sql[0]
    assert "shift_sessions.hotel_id" in main
    assert "shift_sessions.ended_at >=" in main and "shift_sessions.ended_at <" in main
    assert "shift_sessions.status IN" in main
    out = asyncio.run(service([]).get_handovers(HOTEL, ADMIN))
    assert out["items"] == [] and out["summary"]["handed_over_count"] == 0


def test_only_admin_and_manager():
    with pytest.raises(ForbiddenException):
        asyncio.run(service([]).get_handovers(HOTEL, RECEPTION))


def test_endpoint_validates_the_range():
    admin = {**ADMIN, "hotel_id": str(HOTEL)}
    with pytest.raises(ValidationException):
        asyncio.run(shifts_api.get_handovers(
            date_from=date(2026, 10, 5), date_to=date(2026, 10, 1), limit=10,
            session=Db([]), current_user=admin,
        ))
