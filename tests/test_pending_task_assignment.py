"""Kechiktirilgan biriktirish — vazifa hech qachon egasiz qolmaydi.

Nega kerak: ish vaqtidan tashqarida farrosh tanlanmaydi (vazifa biriktirilmay
yaratiladi, push ketmaydi). Vazifa yo'qolib qolmasligi uchun rejalashtiruvchi
har tikda biriktirilmagan avtomatik vazifalarni qayta ko'radi.

`claim` rejimi ham xuddi shunday kafolatlanadi: avval "kim birinchi olsa"
oynasi ochiq turadi, lekin sozlamadagi muddat (`claim_fallback_minutes`,
standart 30 daqiqa) o'tgach vazifa navbat qoidasi bo'yicha biriktiriladi.
0 — sof claim saqlanadi (fallback o'chirilgan).

Bazasiz: sessiya, farrosh tanlash va push o'rinbosarlar bilan.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.application.services import automation_service as auto
from app.application.services.automation_service import AutomationService
from app.application.services.cleaner_assignment import (
    DEFAULT_CLAIM_FALLBACK_MINUTES,
    resolve_claim_fallback_minutes,
)
from app.application.services.notification_service import NotificationService
from tests._branch_fakes import BranchResult, fake_branch, is_branch_query


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, tasks, room, hotel_settings=None):
        self.tasks = tasks
        self.room = room
        # Taqsimlash rejimi mehmonxona sozlamasidan o'qiladi (standart — navbat)
        self.hotel = SimpleNamespace(settings=hotel_settings or {})
        self.flushes = 0

    async def execute(self, _stmt):
        if is_branch_query(_stmt):  # sozlamalar filialda
            return BranchResult(fake_branch(None, self.hotel.settings))
        return FakeResult(list(self.tasks))

    async def get(self, model, _key):
        if model.__name__ == "Branch":
            return None  # vazifa filiali topilmasa — asosiy filial so'raladi
        return self.hotel if model.__name__ == "Hotel" else self.room

    async def flush(self):
        self.flushes += 1


def make_task(created_minutes_ago: float = 0.0):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hotel_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        assigned_to=None,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=created_minutes_ago),
    )


def build(monkeypatch, cleaner, hotel_settings=None, task=None):
    task = task or make_task()
    room = SimpleNamespace(room_number="205")
    svc = AutomationService.__new__(AutomationService)
    svc.session = FakeSession([task], room, hotel_settings)

    async def fake_find(self, hotel_id, branch_id):
        return cleaner

    sent = []

    async def fake_notify(self, **kw):
        sent.append(kw)

    monkeypatch.setattr(AutomationService, "_find_cleaner", fake_find)
    monkeypatch.setattr(NotificationService, "notify", fake_notify)
    return svc, task, sent


# ------------------------------------------------- navbat (queue) rejimi --

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


# ----------------------------------------------- claim: fallback kafolati --

CLAIM = {"hk_assign": {"mode": "claim"}}


def test_claim_window_is_kept_for_fresh_tasks(monkeypatch):
    """Yangi vazifa avvalgidek egasiz turadi — farroshlar o'zi oladi."""
    cleaner = uuid.uuid4()
    svc, task, sent = build(monkeypatch, cleaner, CLAIM, task=make_task(5))

    asyncio.run(svc._assign_pending_tasks())

    assert task.assigned_to is None
    assert sent == []


def test_claim_task_assigned_after_default_fallback(monkeypatch):
    """Muddat (standart 30 daq) o'tib hech kim olmasa — navbat bo'yicha biriktiriladi."""
    cleaner = uuid.uuid4()
    svc, task, sent = build(
        monkeypatch, cleaner, CLAIM, task=make_task(DEFAULT_CLAIM_FALLBACK_MINUTES + 5)
    )

    asyncio.run(svc._assign_pending_tasks())

    assert task.assigned_to == cleaner
    assert len(sent) == 1 and sent[0]["user_id"] == cleaner
    assert sent[0]["entity_id"] == task.id


def test_claim_fallback_respects_configured_minutes(monkeypatch):
    """Sozlamadagi muddat: hali o'tmagan bo'lsa tegilmaydi."""
    cleaner = uuid.uuid4()
    settings = {"hk_assign": {"mode": "claim", "claim_fallback_minutes": 180}}
    svc, task, sent = build(monkeypatch, cleaner, settings, task=make_task(60))

    asyncio.run(svc._assign_pending_tasks())

    assert task.assigned_to is None and sent == []


def test_claim_fallback_can_be_disabled(monkeypatch):
    """0 — sof claim rejimi (fallback o'chirilgan) saqlanadi."""
    cleaner = uuid.uuid4()
    settings = {"hk_assign": {"mode": "claim", "claim_fallback_minutes": 0}}
    svc, task, sent = build(monkeypatch, cleaner, settings, task=make_task(10_000))

    asyncio.run(svc._assign_pending_tasks())

    assert task.assigned_to is None and sent == []


def test_claim_fallback_waits_for_a_cleaner(monkeypatch):
    """Hech kim ishda bo'lmasa — vazifa egasiz qoladi, keyingi tikda biriktiriladi."""
    svc, task, sent = build(monkeypatch, None, CLAIM, task=make_task(120))

    asyncio.run(svc._assign_pending_tasks())

    assert task.assigned_to is None and sent == []


# --------------------------------------------------- sozlama qiymati --

def test_claim_fallback_default_is_on():
    assert resolve_claim_fallback_minutes(None) == DEFAULT_CLAIM_FALLBACK_MINUTES
    assert resolve_claim_fallback_minutes({}) == DEFAULT_CLAIM_FALLBACK_MINUTES
    assert resolve_claim_fallback_minutes(
        {"hk_assign": {"mode": "claim"}}
    ) == DEFAULT_CLAIM_FALLBACK_MINUTES


def test_claim_fallback_value_is_clamped_and_safe():
    assert resolve_claim_fallback_minutes(
        {"hk_assign": {"claim_fallback_minutes": 45}}
    ) == 45
    assert resolve_claim_fallback_minutes(
        {"hk_assign": {"claim_fallback_minutes": 0}}
    ) == 0
    assert resolve_claim_fallback_minutes(
        {"hk_assign": {"claim_fallback_minutes": -10}}
    ) == 0
    assert resolve_claim_fallback_minutes(
        {"hk_assign": {"claim_fallback_minutes": 10_000}}
    ) == 24 * 60
    # Buzuq qiymat standartga qaytadi
    assert resolve_claim_fallback_minutes(
        {"hk_assign": {"claim_fallback_minutes": "abc"}}
    ) == DEFAULT_CLAIM_FALLBACK_MINUTES


def test_orphan_repair_note_is_shared_constant():
    """Tiklangan vazifa izohi bitta joyda — filtr va yaratish ajralib ketmasin."""
    assert "avtomatik tiklandi" in auto.ORPHAN_REPAIR_NOTE
