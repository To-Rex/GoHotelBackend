"""System diagnostics: conflicts are counted from read-only SQL checks."""
import asyncio
import os
from types import SimpleNamespace

from app.superadmin.system_service import SystemService, _int


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _Session:
    def __init__(self, values):
        self.values = values

    async def execute(self, statement, *_args, **_kwargs):
        sql = str(statement)
        for needle, value in self.values.items():
            if needle in sql:
                return _Result(value)
        return _Result(0)


def test_diagnostics_reports_every_conflict():
    values = {
        "pg_blocking_pids": 1,
        "now() - query_start": 2,
        "idle in transaction": 3,
        "COALESCE(a.booking_type": 4,
        "a.booking_type = 'HOURLY'": 5,
        "r.current_status = 'OCCUPIED'": 6,
        "r.current_status <> 'OCCUPIED'": 7,
        "r.is_deleted = true": 8,
        "JOIN users u ON u.id = s.user_id": 9,
        "status = 'PENDING_HANDOVER'": 10,
        "FROM invoices": 11,
        "status <> 'CANCELLED'": 12,
        "status IN ('OPEN','IN_PROGRESS')": 13,
        "JOIN branches b ON b.id = r.branch_id": 14,
        "JOIN branches b ON b.id = u.branch_id": 15,
    }
    svc = SystemService(_Session(values))
    diagnostics = asyncio.run(svc._diagnostics())
    conflicts = diagnostics["conflicts"]

    assert conflicts["blocked_queries"] == 1
    assert conflicts["long_running_queries"] == 2
    assert conflicts["idle_in_transactions"] == 3
    assert conflicts["double_bookings"] == 4
    assert conflicts["hourly_overlaps"] == 5
    assert conflicts["stale_occupied_rooms"] == 6
    assert conflicts["checked_in_rooms_not_occupied"] == 7
    assert conflicts["active_in_archived_rooms"] == 8
    assert conflicts["open_shifts_inactive_staff"] == 9
    assert conflicts["pending_handovers"] == 10
    assert conflicts["overpaid_invoices"] == 11
    assert conflicts["overpaid_reservations"] == 12
    assert conflicts["stuck_tasks"] == 13
    assert conflicts["room_branch_mismatch"] == 14
    assert conflicts["user_branch_mismatch"] == 15
    assert diagnostics["checked_at"]
    assert diagnostics["capacity"]["disk_total"] is not None
    assert diagnostics["runtime"]["pid"] == os.getpid()


def test_failed_check_is_not_reported_as_zero():
    class Broken(_Session):
        async def execute(self, statement, *_args, **_kwargs):
            raise RuntimeError("boom")

    svc = SystemService(Broken({}))
    diagnostics = asyncio.run(svc._diagnostics())
    assert diagnostics["conflicts"]["blocked_queries"] is None


def test_int_conversion():
    assert _int("7") == 7
    assert _int(None) is None
    assert _int("x") is None
    assert _int(SimpleNamespace()) is None
