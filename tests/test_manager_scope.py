"""Manager scope: receptionist template codes are assignable, admin codes are not."""
from app.presentation.api.v1.permissions import _is_manager_assignable

RECEPTIONIST_CODES = [
    "reservation.create",
    "reservation.update",
    "reservation.view",
    "guest.create",
    "guest.view",
    "guest.delete",
    "room.view",
    "room.status.update",
    "service.view",
    "finance.invoice.create",
    "finance.payment.create",
    "feedback.create",
    "feedback.delete",
]


def test_manager_can_assign_the_receptionist_template():
    for code in RECEPTIONIST_CODES:
        assert _is_manager_assignable(code), code


def test_manager_scope_still_blocks_everything_else():
    for code in (
        "reservation.cancel",
        "employee.create",
        "employee.update",
        "permission.assign",
        "finance.view",
        "finance.payment.update",
        "room.manage",
        "shift.force_close",
        "report.view",
    ):
        assert not _is_manager_assignable(code), code
