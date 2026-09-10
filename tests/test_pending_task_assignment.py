"""Kechiktirilgan biriktirish — farrosh ishga kelishi bilan vazifa unga tushadi.

Nega kerak: ish vaqtidan tashqarida farrosh tanlanmaydi (vazifa biriktirilmay
yaratiladi, push ketmaydi). Vazifa yo'qolib qolmasligi uchun rejalashtiruvchi
har tikda biriktirilmagan avtomatik vazifalarni qayta ko'radi.

Bazasiz: sessiya, farrosh tanlash va push o'rinbosarlar bilan.
"""
import asyncio
import uuid
from types import SimpleNamespace

from app.application.services import automation_service as auto
from app.application.services.automation_service import AutomationService
from app.application.services.notification_service import NotificationService


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, tasks, room):
        self.tasks = tasks
        self.room = room
        self.flushes = 0

    async def execute(self, _stmt):
        return FakeResult(list(self.tasks))

    async def get(self, model, key):
        return self.room

    async def flush(self):
        self.flushes += 1


def make_task():
    return SimpleNamespace(
        id=uuid.uuid4(),
        hotel_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        assigned_to=None,
    )


def build(monkeypatch, cleaner):
    task = make_task()
    room = SimpleNamespace(room_number="205")
    svc = AutomationService.__new__(AutomationService)
    svc.session = FakeSession([task], room)

    async def fake_find(self, hotel_id, branch_id):
        return cleaner

    sent = []

    async def fake_notify(self, **kw):
        sent.append(kw)

    monkeypatch.setattr(AutomationService, "_find_cleaner", fake_find)
    monkeypatch.setattr(NotificationService, "notify", fake_notify)
    return svc, task, sent


def test_task_assigned_and_pushed_when_cleaner_on_duty(monkeypatch):
    cleaner = uuid.uuid4()
    svc, task, sent = build(monkeypatch, cleaner)

    asyncio.run(svc._assign_pending_tasks())

    assert task.assigned_to == cleaner
    assert len(sent) == 1
    assert sent[0]["user_id"] == cleaner
    assert sent[0]["entity_id"] == task.id
    assert "205-xona" in sent[0]["body"]
    assert sent[0]["send_push"] is True


def test_task_left_alone_while_nobody_on_duty(monkeypatch):
    """Tunda hech kim ishda emas — vazifa tegilmaydi, keyingi tikda qayta ko'riladi."""
    svc, task, sent = build(monkeypatch, None)

    asyncio.run(svc._assign_pending_tasks())

    assert task.assigned_to is None
    assert sent == []
    assert svc.session.flushes == 0


def test_orphan_repair_note_is_shared_constant():
    """Tiklangan vazifa izohi bitta joyda — filtr va yaratish ajralib ketmasin."""
    assert "avtomatik tiklandi" in auto.ORPHAN_REPAIR_NOTE
