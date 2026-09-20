"""`claim` rejimi — bo'sh vazifani birinchi bosgan farrosh oladi.

Ikki narsa sinaladi:
  * ro'yxat doirasi — kimga biriktirilmagan vazifalar ko'rinadi;
  * egallash — ikki farrosh bir vaqtda bossa faqat bittasi yutadi
    (shartli UPDATE, `rowcount == 0` bo'lsa yutqazgan xato oladi).

Bazasiz: sessiya va yordamchi servislar o'rinbosarlar bilan.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.application.services import checklist_template_service as cts
from app.application.services.mobile_tasks_service import MobileTasksService
from app.core.exceptions import ValidationException

HOUSEKEEPER = ["room.view", "room.status.update", "housekeeping.task.update"]
MANAGER = ["shift.force_close", "housekeeping.task.assign", "reservation.create"]
RECEPTION = ["reservation.create", "reservation.update", "guest.view"]


class FakeUpdateResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class FakeSession:
    """Faqat `start_task` va `claim_pool_visible` uchun yetarli minimal sessiya."""

    def __init__(self, rowcount: int = 1, hotel_settings: dict | None = None):
        self.rowcount = rowcount
        self.statements = []
        self.hotel = SimpleNamespace(settings=hotel_settings or {})

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeUpdateResult(self.rowcount)

    async def get(self, _model, _key):
        return self.hotel

    async def flush(self):
        pass

    def expire(self, *_args):
        pass


def make_task(assigned_to=None, status="OPEN"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hotel_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        assigned_to=assigned_to,
        status=status,
        started_at=None,
    )


@pytest.fixture
def service(monkeypatch):
    """`start_task` ni bazasiz ishlatish uchun tayyorlangan servis."""

    def build(task, rowcount: int = 1):
        svc = MobileTasksService(FakeSession(rowcount=rowcount))

        async def fake_get_task(self, task_id, hotel_id):
            return task

        async def fake_enrich(self, t, hotel_id):
            return {"id": str(t.id), "status": t.status, "assigned_to": t.assigned_to}

        async def fake_attach(self, _task):
            return False

        monkeypatch.setattr(MobileTasksService, "_get_task", fake_get_task)
        monkeypatch.setattr(MobileTasksService, "_enrich_task", fake_enrich)
        monkeypatch.setattr(cts.ChecklistTemplateService, "attach_to_task", fake_attach)
        return svc

    return build


# ------------------------------------------------------------- egallash --
def test_free_task_is_claimed_by_whoever_starts_it(service):
    task = make_task(assigned_to=None)
    svc = service(task, rowcount=1)
    me = uuid.uuid4()

    out = asyncio.run(svc.start_task(task.id, task.hotel_id, me))

    assert task.assigned_to == me
    assert task.status == "IN_PROGRESS"
    assert out["status"] == "IN_PROGRESS"
    # Shartli UPDATE yuborilgan bo'lishi kerak
    assert len(svc.session.statements) == 1


def test_lost_race_raises_and_does_not_start(service):
    """Ikkinchi farrosh bir zumda kechikdi — UPDATE hech qatorga tegmadi."""
    task = make_task(assigned_to=None)
    svc = service(task, rowcount=0)

    with pytest.raises(ValidationException) as err:
        asyncio.run(svc.start_task(task.id, task.hotel_id, uuid.uuid4()))

    assert err.value.error_code == "TASK_ALREADY_CLAIMED"
    assert task.status == "OPEN"
    assert task.assigned_to is None


def test_own_task_starts_without_claiming(service):
    """Navbat rejimi o'zgarmaydi: biriktirilgan vazifa avvalgidek boshlanadi."""
    me = uuid.uuid4()
    task = make_task(assigned_to=me)
    svc = service(task)

    asyncio.run(svc.start_task(task.id, task.hotel_id, me))

    assert task.status == "IN_PROGRESS"
    assert svc.session.statements == []  # egallash kerak emas


def test_other_cleaners_task_is_rejected(service):
    task = make_task(assigned_to=uuid.uuid4())
    svc = service(task)

    with pytest.raises(ValidationException) as err:
        asyncio.run(svc.start_task(task.id, task.hotel_id, uuid.uuid4()))

    assert err.value.error_code == "TASK_ASSIGNED_TO_OTHER"
    assert task.status == "OPEN"


def test_admin_may_start_someone_elses_task(service):
    """Administrator uchun avvalgi erkinlik saqlanadi."""
    task = make_task(assigned_to=uuid.uuid4())
    svc = service(task)

    asyncio.run(
        svc.start_task(
            task.id, task.hotel_id, uuid.uuid4(), allow_other_assignee=True
        )
    )

    assert task.status == "IN_PROGRESS"


def test_already_started_task_still_rejected(service):
    """Eski tekshiruv birinchi o'rinda qoladi."""
    task = make_task(assigned_to=None, status="IN_PROGRESS")
    svc = service(task)

    with pytest.raises(ValidationException) as err:
        asyncio.run(svc.start_task(task.id, task.hotel_id, uuid.uuid4()))

    assert err.value.error_code == "INVALID_STATUS"
    assert svc.session.statements == []


# --------------------------------------------------------- ro'yxat doirasi --
def run_visible(settings, permissions):
    svc = MobileTasksService(FakeSession(hotel_settings=settings))
    return asyncio.run(svc.claim_pool_visible(uuid.uuid4(), permissions))


CLAIM = {"hk_assign": {"mode": "claim"}}
QUEUE = {"hk_assign": {"mode": "queue"}}


def test_pool_visible_only_for_housekeepers_in_claim_mode():
    assert run_visible(CLAIM, HOUSEKEEPER) is True


def test_pool_hidden_in_queue_mode():
    """Navbat rejimida xodim avvalgidek faqat o'z vazifalarini ko'radi."""
    assert run_visible(QUEUE, HOUSEKEEPER) is False
    assert run_visible({}, HOUSEKEEPER) is False
    assert run_visible(None, HOUSEKEEPER) is False


def test_pool_hidden_from_non_housekeepers():
    assert run_visible(CLAIM, MANAGER) is False
    assert run_visible(CLAIM, RECEPTION) is False
    # Ruxsatlar yuborilmagan (eski mijoz) yoki bo'sh (admin) — ko'rinmaydi
    assert run_visible(CLAIM, []) is False
    assert run_visible(CLAIM, None) is False
