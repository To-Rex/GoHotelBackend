"""Tozalash vazifasi qaysi farroshga tushishi — qaror qoidasi.

Nega kerak: "Mehmon chiqmoqda" bosilganda vazifa menejer yoki texnik
xodimga tushib qolardi — ularda ham `housekeeping.*` kodlari bor. Farrosh
vazifani ko'rmas, menejer esa "xonani tozalang" pushini olardi.

Faqat qaror mantig'i sinaladi (`pick_cleaner`, `classify_role`) — bazasiz.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.application.services.cleaner_assignment import (
    ROLE_HOUSEKEEPER,
    ROLE_MAINTENANCE,
    ROLE_MANAGER,
    ROLE_RECEPTION,
    ROLE_STAFF,
    CleanerCandidate,
    classify_role,
    pick_cleaner,
)

# Frontend'dagi rol shablonlari (permissionTemplates.ts) — yulduzchalar
# seed_permissions.py dagi haqiqiy kodlarga yoyilgan
HOUSEKEEPER = {
    "room.view",
    "room.status.update",
    "housekeeping.task.update",
    "housekeeping.cleaning.start",
    "housekeeping.cleaning.complete",
}
HOUSEKEEPING_LEAD = HOUSEKEEPER | {
    "housekeeping.task.create",
    "housekeeping.task.assign",
    "room.manage",
    "employee.view",
    "report.view",
}
RECEPTIONIST = {
    "reservation.create",
    "reservation.update",
    "reservation.view",
    "guest.create",
    "guest.update",
    "guest.view",
    "room.view",
    "room.status.update",
    "service.view",
    "finance.invoice.create",
    "finance.payment.create",
}
MANAGER = HOUSEKEEPING_LEAD | RECEPTIONIST | {
    "reservation.cancel",
    "employee.create",
    "employee.update",
    "shift.force_close",
}
MAINTENANCE = {
    "room.view",
    "room.status.update",
    "housekeeping.task.create",
    "housekeeping.task.update",
}
ACCOUNTANT = {"finance.view", "report.view", "reservation.view", "guest.view"}

BRANCH = uuid4()
OTHER_BRANCH = uuid4()
T0 = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)


def cand(
    codes,
    active=0,
    last=None,
    branch=BRANCH,
    order=0,
    user_id: UUID | None = None,
) -> CleanerCandidate:
    return CleanerCandidate(
        user_id=user_id or uuid4(),
        branch_id=branch,
        codes=frozenset(codes),
        active_tasks=active,
        last_assigned_at=last,
        order=order,
    )


# ----------------------------------------------------------------- rollar --
def test_templates_classify_like_the_mobile_app():
    """Har bir shablon mobil RoleRegistry qaysi bo'limni ochsa, shu rol."""
    assert classify_role(HOUSEKEEPER) == ROLE_HOUSEKEEPER
    assert classify_role(HOUSEKEEPING_LEAD) == ROLE_HOUSEKEEPER
    assert classify_role(MANAGER) == ROLE_MANAGER
    assert classify_role(RECEPTIONIST) == ROLE_RECEPTION
    assert classify_role(MAINTENANCE) == ROLE_MAINTENANCE
    assert classify_role(ACCOUNTANT) == ROLE_STAFF
    assert classify_role(set()) == ROLE_STAFF


def test_room_status_alone_counts_as_housekeeper():
    """Eski ruxsat to'plami: faqat xona holatini yangilay oladigan farrosh."""
    assert classify_role({"room.view", "room.status.update"}) == ROLE_HOUSEKEEPER


# ------------------------------------------------- faqat farroshga tushadi --
def test_manager_and_maintenance_never_receive_cleaning():
    """Aynan shu holat nosozlik edi: menejer va texnikda housekeeping.* bor."""
    manager = cand(MANAGER, active=0)
    tech = cand(MAINTENANCE, active=0)
    reception = cand(RECEPTIONIST, active=0)
    cleaner = cand(HOUSEKEEPER, active=5)  # band bo'lsa ham
    assert pick_cleaner([manager, tech, reception, cleaner], BRANCH) == cleaner.user_id


def test_lead_only_when_no_line_cleaner():
    lead = cand(HOUSEKEEPING_LEAD, active=0)
    cleaner = cand(HOUSEKEEPER, active=3)
    assert pick_cleaner([lead, cleaner], BRANCH) == cleaner.user_id
    # Oddiy farrosh yo'q — boshliq oladi
    assert pick_cleaner([lead, cand(MANAGER)], BRANCH) == lead.user_id


def test_fallback_to_old_rule_when_hotel_has_no_housekeeper():
    """Kichik hostel: tozalashni texnik/qabulxona qiladi — vazifa yetim qolmasin."""
    tech = cand(MAINTENANCE, active=1)
    manager = cand(MANAGER, active=0)
    # Eski qoida: istalgan housekeeping.* egasi, eng kam yuklamalisi
    assert pick_cleaner([tech, manager], BRANCH) == manager.user_id


def test_nobody_suitable_gives_none():
    assert pick_cleaner([cand(ACCOUNTANT), cand(RECEPTIONIST)], BRANCH) is None
    assert pick_cleaner([], BRANCH) is None


# ---------------------------------------------------------- bo'sh birinchi --
def test_idle_cleaner_beats_busy_ones():
    busy_a = cand(HOUSEKEEPER, active=1, last=T0)
    idle = cand(HOUSEKEEPER, active=0, last=T0 - timedelta(hours=3))
    busy_b = cand(HOUSEKEEPER, active=2, last=T0 - timedelta(days=1))
    assert pick_cleaner([busy_a, idle, busy_b], BRANCH) == idle.user_id


def test_least_loaded_when_everyone_is_busy():
    two = cand(HOUSEKEEPER, active=2, last=T0 - timedelta(hours=2))
    one = cand(HOUSEKEEPER, active=1, last=T0)
    assert pick_cleaner([two, one], BRANCH) == one.user_id


# ---------------------------------------------------------------- navbat --
def test_round_robin_when_load_is_equal():
    """Foydalanuvchi ta'rifi: hammada bittadan xona — keyingisi birinchi
    farroshga, undan keyingisi ikkinchisiga."""
    a = cand(HOUSEKEEPER, active=1, last=T0 - timedelta(minutes=30), order=0)
    b = cand(HOUSEKEEPER, active=1, last=T0 - timedelta(minutes=10), order=1)

    first = pick_cleaner([a, b], BRANCH)
    assert first == a.user_id  # a vazifani oldinroq olgan — navbat unda

    # a oldi: yuklamasi 2, vaqti hozir. Endi b kam yuklamali
    a2 = cand(HOUSEKEEPER, active=2, last=T0, order=0, user_id=a.user_id)
    assert pick_cleaner([a2, b], BRANCH) == b.user_id

    # Ikkalasi ham 2 ta: b keyinroq oldi — navbat yana a da
    b2 = cand(HOUSEKEEPER, active=2, last=T0 + timedelta(minutes=1), order=1, user_id=b.user_id)
    assert pick_cleaner([a2, b2], BRANCH) == a.user_id


def test_never_assigned_goes_before_previously_assigned():
    veteran = cand(HOUSEKEEPER, active=0, last=T0 - timedelta(days=30), order=0)
    newcomer = cand(HOUSEKEEPER, active=0, last=None, order=1)
    assert pick_cleaner([veteran, newcomer], BRANCH) == newcomer.user_id


def test_order_does_not_depend_on_input_sequence():
    a = cand(HOUSEKEEPER, active=1, last=T0 - timedelta(hours=1), order=0)
    b = cand(HOUSEKEEPER, active=1, last=T0, order=1)
    assert pick_cleaner([a, b], BRANCH) == pick_cleaner([b, a], BRANCH) == a.user_id


def test_full_tie_breaks_by_hire_order():
    a = cand(HOUSEKEEPER, active=0, last=None, order=0)
    b = cand(HOUSEKEEPER, active=0, last=None, order=1)
    assert pick_cleaner([b, a], BRANCH) == a.user_id


def test_naive_and_aware_timestamps_do_not_crash():
    """Bazadan tz-aware, testlardan naive kelishi mumkin — solishtirish yiqilmasin."""
    a = cand(HOUSEKEEPER, active=1, last=datetime(2026, 9, 7, 9, 0))
    b = cand(HOUSEKEEPER, active=1, last=T0)
    assert pick_cleaner([a, b], BRANCH) in {a.user_id, b.user_id}


# ---------------------------------------------------------------- filial --
def test_same_branch_preferred_even_if_busier():
    here = cand(HOUSEKEEPER, active=3, branch=BRANCH)
    there = cand(HOUSEKEEPER, active=0, branch=OTHER_BRANCH)
    assert pick_cleaner([here, there], BRANCH) == here.user_id


def test_other_branch_used_when_own_branch_has_no_cleaner():
    there = cand(HOUSEKEEPER, active=0, branch=OTHER_BRANCH)
    manager_here = cand(MANAGER, active=0, branch=BRANCH)
    assert pick_cleaner([there, manager_here], BRANCH) == there.user_id
