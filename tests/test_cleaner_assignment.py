"""Tozalash vazifasi qaysi farroshga tushishi — qaror qoidasi.

Nega kerak: "Mehmon chiqmoqda" bosilganda vazifa menejer yoki texnik
xodimga tushib qolardi — ularda ham `housekeeping.*` kodlari bor. Farrosh
vazifani ko'rmas, menejer esa "xonani tozalang" pushini olardi.

Faqat qaror mantig'i sinaladi (`pick_cleaner`, `classify_role`) — bazasiz.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.application.services.cleaner_assignment import (
    ASSIGN_MODE_CLAIM,
    ASSIGN_MODE_QUEUE,
    ROLE_HOUSEKEEPER,
    ROLE_MAINTENANCE,
    ROLE_MANAGER,
    ROLE_RECEPTION,
    ROLE_STAFF,
    CleanerCandidate,
    classify_role,
    eligible_cleaners,
    is_on_duty,
    pick_cleaner,
    plan_assignment,
    resolve_assign_mode,
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


# ------------------------------------------------------------- ish vaqti --
def test_is_on_duty_day_shift():
    from datetime import time

    assert is_on_duty("09:00", "18:00", time(9, 0))  # boshlanish kiradi
    assert is_on_duty("09:00", "18:00", time(13, 30))
    assert not is_on_duty("09:00", "18:00", time(18, 0))  # tugash kirmaydi
    assert not is_on_duty("09:00", "18:00", time(3, 15))


def test_is_on_duty_night_shift_crosses_midnight():
    from datetime import time

    assert is_on_duty("22:00", "06:00", time(23, 0))
    assert is_on_duty("22:00", "06:00", time(2, 0))
    assert not is_on_duty("22:00", "06:00", time(12, 0))
    assert not is_on_duty("22:00", "06:00", time(6, 0))


def test_is_on_duty_bad_or_missing_data_never_blocks():
    """Ma'lumot xatosi vazifani to'sib qo'ymasin — xodim ishda deb olinadi."""
    from datetime import time

    assert is_on_duty(None, None, time(3, 0))
    assert is_on_duty("", "18:00", time(3, 0))
    assert is_on_duty("abc", "18:00", time(3, 0))
    assert is_on_duty("09:00", "09:00", time(3, 0))  # 24 soatlik


def test_off_duty_cleaner_never_gets_the_task():
    """Foydalanuvchi talabi: ish vaqtidan tashqarida vazifa yuborilmaydi."""
    night = CleanerCandidate(
        user_id=uuid4(), branch_id=BRANCH, codes=frozenset(HOUSEKEEPER),
        active_tasks=0, on_duty=False,
    )
    day = CleanerCandidate(
        user_id=uuid4(), branch_id=BRANCH, codes=frozenset(HOUSEKEEPER),
        active_tasks=5, on_duty=True,
    )
    # Ishdagi farrosh band bo'lsa ham, ishda bo'lmaganiga bermaydi
    assert pick_cleaner([night, day], BRANCH) == day.user_id


# --------------------------------------------------------- taqsimlash rejimi --
def test_assign_mode_defaults_to_queue():
    """Sozlama yo'q/buzuq — avvalgi xatti-harakat (navbat)."""
    for value in (None, {}, {"hk_assign": {}}, {"hk_assign": {"mode": ""}},
                  {"hk_assign": {"mode": "bogus"}}, {"hk_assign": {"mode": None}}):
        assert resolve_assign_mode(value) == ASSIGN_MODE_QUEUE


def test_assign_mode_parsing():
    assert resolve_assign_mode({"hk_assign": {"mode": "claim"}}) == ASSIGN_MODE_CLAIM
    assert resolve_assign_mode({"hk_assign": {"mode": "CLAIM"}}) == ASSIGN_MODE_CLAIM
    assert resolve_assign_mode({"hk_assign": {"mode": " queue "}}) == ASSIGN_MODE_QUEUE


def test_queue_mode_assigns_one_and_notifies_only_that_one():
    a = cand(HOUSEKEEPER, active=0, order=0)
    b = cand(HOUSEKEEPER, active=3, order=1)
    plan = plan_assignment([a, b], BRANCH, ASSIGN_MODE_QUEUE)
    assert plan.assignee == a.user_id
    assert plan.notify == (a.user_id,)
    assert plan.is_claim is False


def test_claim_mode_assigns_nobody_and_notifies_the_whole_pool():
    """Foydalanuvchi talabi: hammaga yuboriladi, biriktirish yo'q."""
    a = cand(HOUSEKEEPER, active=0, order=0)
    b = cand(HOUSEKEEPER, active=3, order=1)
    manager = cand(MANAGER, active=0, order=2)
    plan = plan_assignment([a, b, manager], BRANCH, ASSIGN_MODE_CLAIM)
    assert plan.assignee is None
    assert plan.is_claim is True
    # Menejer doiraga kirmaydi — faqat farroshlar
    assert set(plan.notify) == {a.user_id, b.user_id}


def test_claim_mode_respects_working_hours_and_branch():
    here = cand(HOUSEKEEPER, branch=BRANCH, order=0)
    off = CleanerCandidate(
        user_id=uuid4(), branch_id=BRANCH, codes=frozenset(HOUSEKEEPER), on_duty=False
    )
    there = cand(HOUSEKEEPER, branch=OTHER_BRANCH, order=2)
    plan = plan_assignment([here, off, there], BRANCH, ASSIGN_MODE_CLAIM)
    assert plan.notify == (here.user_id,)


def test_claim_mode_with_nobody_on_duty_notifies_nobody():
    off = CleanerCandidate(
        user_id=uuid4(), branch_id=BRANCH, codes=frozenset(HOUSEKEEPER), on_duty=False
    )
    plan = plan_assignment([off], BRANCH, ASSIGN_MODE_CLAIM)
    assert plan.assignee is None and plan.notify == ()


def test_eligible_pool_is_shared_by_both_modes():
    """Ikkala rejim ham bir xil doiradan foydalanadi — qoida ajralib ketmasin."""
    people = [cand(HOUSEKEEPER, order=0), cand(MANAGER, order=1), cand(MAINTENANCE, order=2)]
    pool = {c.user_id for c in eligible_cleaners(people, BRANCH)}
    queue = plan_assignment(people, BRANCH, ASSIGN_MODE_QUEUE)
    claim = plan_assignment(people, BRANCH, ASSIGN_MODE_CLAIM)
    assert queue.assignee in pool
    assert set(claim.notify) == pool


def test_pick_cleaner_is_queue_mode_regardless_of_settings():
    """pick_cleaner — navbat qoidasi; rejim unga ta'sir qilmaydi."""
    a = cand(HOUSEKEEPER, active=0, order=0)
    b = cand(HOUSEKEEPER, active=2, order=1)
    assert pick_cleaner([a, b], BRANCH) == a.user_id


def test_nobody_on_duty_leaves_task_unassigned():
    """Hech kim ishda emas — None: vazifa biriktirilmay yaratiladi, push yo'q;
    rejalashtiruvchi farrosh ishga kelishi bilan biriktiradi."""
    a = CleanerCandidate(
        user_id=uuid4(), branch_id=BRANCH, codes=frozenset(HOUSEKEEPER), on_duty=False
    )
    b = CleanerCandidate(
        user_id=uuid4(), branch_id=BRANCH, codes=frozenset(HOUSEKEEPER), on_duty=False
    )
    assert pick_cleaner([a, b], BRANCH) is None
    # Eski keng qoidada ham xuddi shunday
    tech = CleanerCandidate(
        user_id=uuid4(), branch_id=BRANCH, codes=frozenset(MAINTENANCE), on_duty=False
    )
    assert pick_cleaner([tech], BRANCH) is None
