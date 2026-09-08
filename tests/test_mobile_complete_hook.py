"""Mobil ilovadan yakunlash — web bilan bitta yo'l.

Nega kerak: farrosh telefonidan "Yakunlash" bosganda (PUT /tasks/{id}/progress,
100%) status to'g'ridan-to'g'ri yozilar, bron esa "chiqish jarayonida" turib
qolardi — bronni CHECKED_OUT qiladigan hook faqat HousekeepingService da edi.
Endi 100% HousekeepingService.update_task_status orqali o'tishi kerak.

Bazasiz: _get_task/_enrich_task va HousekeepingService o'rinbosarlar bilan.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.application.services import housekeeping_service as hk
from app.application.services.mobile_tasks_service import MobileTasksService


class FakeSession:
    async def flush(self):
        pass


def make_task():
    return SimpleNamespace(
        id=uuid.uuid4(),
        hotel_id=uuid.uuid4(),
        assigned_to=uuid.uuid4(),
        created_by=uuid.uuid4(),
        status="IN_PROGRESS",
        progress=40,
    )


@pytest.fixture
def wired(monkeypatch):
    task = make_task()
    calls = []

    async def fake_get_task(self, task_id, hotel_id):
        return task

    async def fake_enrich(self, t, hotel_id):
        return {"id": str(t.id), "progress": t.progress, "status": t.status}

    async def fake_update_status(self, task_id, hotel_id, status, user_id, notes=None):
        calls.append((task_id, hotel_id, status, user_id))
        task.status = status
        return task

    monkeypatch.setattr(MobileTasksService, "_get_task", fake_get_task)
    monkeypatch.setattr(MobileTasksService, "_enrich_task", fake_enrich)
    monkeypatch.setattr(hk.HousekeepingService, "update_task_status", fake_update_status)
    return MobileTasksService(FakeSession()), task, calls


def test_partial_progress_only_updates_progress(wired):
    service, task, calls = wired
    out = asyncio.run(
        service.update_progress(task.id, task.hotel_id, 60, user_id=uuid.uuid4())
    )
    assert out["progress"] == 60
    assert task.status == "IN_PROGRESS"
    assert calls == []


def test_full_progress_goes_through_housekeeping_service(wired):
    """Aynan shu holat nosozlik edi: 100% da bron hook'i chetlab o'tilardi."""
    service, task, calls = wired
    actor = uuid.uuid4()
    out = asyncio.run(
        service.update_progress(task.id, task.hotel_id, 100, user_id=actor)
    )
    assert calls == [(task.id, task.hotel_id, "COMPLETED", actor)]
    assert out["status"] == "COMPLETED"
    assert out["progress"] == 100


def test_missing_user_falls_back_to_assignee(wired):
    """Eski chaqiruvchi user_id bermasa ham yo'l o'zgarmaydi — mas'ul farrosh."""
    service, task, calls = wired
    asyncio.run(service.update_progress(task.id, task.hotel_id, 100))
    assert calls[0][3] == task.assigned_to
