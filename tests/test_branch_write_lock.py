"""Fon xizmatlari filial yozuv qulfini KUTMASDAN oladi.

Nega: xodim so'rovlari (POST/PUT/DELETE) filial qulfi bilan navbatda turadi.
Fon vazifasi (avto-chiqish, vazifalarni biriktirish/yopish, qarz
eslatmalari) qulfsiz yozsa, ikkalasi bir filialning qatorlarini teskari
tartibda qulflab deadlock qilardi — xodim "so'rovlar to'qnashdi" (409)
ko'rardi. Endi fon vazifasi ham qulfni oladi, lekin kutmaydi: filial band
bo'lsa shu filial keyingi tikda qayta ko'riladi, xodim esa fon ishini hech
qachon kutib qolmaydi. Push esa fonda ketadi — tranzaksiya va qulf
Firebase'ni kutmaydi.

Bazasiz: soxta sessiyalar.
"""
import asyncio
import uuid
from types import SimpleNamespace

from app.application.services import automation_service as auto
from app.application.services.automation_service import _by_branch
from app.infrastructure.push import firebase
from app.infrastructure.tenant.branch_scope import try_branch_write_lock
from tests.test_pending_task_assignment import build

H, B1, B2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class PgSession:
    """PostgreSQL ko'rinishidagi sessiya: qulf so'rovi berilgan javobni qaytaradi."""

    def __init__(self, granted=True, raise_exc=False):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.granted = granted
        self.raise_exc = raise_exc
        self.queries = []

    async def execute(self, stmt, params=None):
        self.queries.append((str(stmt), params))
        if self.raise_exc:
            raise RuntimeError("baza yo'q")
        return _Scalar(self.granted)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ qulfning o'zi --

def test_lock_granted_or_busy_without_waiting():
    s = PgSession(granted=True)
    assert run(try_branch_write_lock(s, H, B1)) is True
    sql, params = s.queries[0]
    assert "pg_try_advisory_xact_lock" in sql  # kutmaydigan variant
    assert isinstance(params["key"], int) and params["key"] > 0
    assert run(try_branch_write_lock(PgSession(granted=False), H, B1)) is False


def test_lock_never_breaks_the_background_job():
    # Qulf so'rovi xato bersa — qulfsiz davom (avvalgidek)
    assert run(try_branch_write_lock(PgSession(raise_exc=True), H, B1)) is True
    # PostgreSQL bo'lmasa (SQLite testlari) — qulf yo'q, davom
    sqlite = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    assert run(try_branch_write_lock(sqlite, H, B1)) is True
    # Mehmonxonasiz yozuv — kalit yo'q, so'rov ham yo'q
    s = PgSession()
    assert run(try_branch_write_lock(s, None, B1)) is True
    assert s.queries == []


def test_same_branch_same_key_as_request_lock():
    """Fon vazifasi va xodim so'rovi BIR kalitni oladi — aks holda navbat
    bo'lmasdi."""
    from app.infrastructure.tenant.branch_scope import branch_lock_key

    s = PgSession()
    run(try_branch_write_lock(s, H, B1))
    assert s.queries[0][1]["key"] == branch_lock_key(H, B1)
    assert branch_lock_key(H, B1) != branch_lock_key(H, B2)


# ------------------------------------------------------- filial guruhlari --

def test_by_branch_groups_in_order():
    a1 = SimpleNamespace(hotel_id=H, branch_id=B1, n=1)
    b1 = SimpleNamespace(hotel_id=H, branch_id=B2, n=2)
    a2 = SimpleNamespace(hotel_id=H, branch_id=B1, n=3)
    groups = _by_branch([a1, b1, a2])
    assert list(groups) == [(H, B1), (H, B2)]
    assert [x.n for x in groups[(H, B1)]] == [1, 3]
    assert _by_branch([]) == {}


# --------------------------------------------- fon bosqichi band filialda --

def test_busy_branch_is_skipped_until_next_tick(monkeypatch):
    """Filial band — vazifa tegilmaydi, push yo'q, commit yo'q; keyingi tikda
    qulf bo'sh bo'lsa — biriktiriladi va shu filial commit qilinadi."""
    cleaner = uuid.uuid4()
    svc, task, sent = build(monkeypatch, cleaner)

    async def busy(session, hotel_id, branch_id):
        assert (hotel_id, branch_id) == (task.hotel_id, task.branch_id)
        return False

    monkeypatch.setattr(auto, "try_branch_write_lock", busy)
    asyncio.run(svc._assign_pending_tasks())
    assert task.assigned_to is None
    assert sent == []
    assert svc.session.commits == 0

    async def free(session, hotel_id, branch_id):
        return True

    monkeypatch.setattr(auto, "try_branch_write_lock", free)
    asyncio.run(svc._assign_pending_tasks())
    assert task.assigned_to == cleaner
    assert len(sent) == 1
    assert svc.session.commits == 1  # filial alohida tranzaksiyada yakunlandi


# ------------------------------------------------------------ push fonda --

def test_push_goes_in_background(monkeypatch):
    """`send_push_background` darhol qaytadi; yuborish keyingi loop tikida."""
    calls = []

    async def fake_send(tokens, title, body=None, data=None, data_only=False):
        calls.append((tokens, title, data_only))
        return 1

    monkeypatch.setattr(firebase, "send_push", fake_send)

    async def body():
        assert firebase.send_push_background("tok", "Salom", "matn", data={"k": "v"}, data_only=True) is True
        assert calls == []  # chaqiruvchi kutmadi
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == [("tok", "Salom", True)]

    asyncio.run(body())
    # Event loop bo'lmasa — jim, hech narsa yuborilmaydi
    assert firebase.send_push_background("tok", "Salom") is False
