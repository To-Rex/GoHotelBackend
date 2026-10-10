"""Mehmonni yuzidan tanish — "boshqa odamni tanimaslik" qoidalari (bazasiz).

"Tanilgan" faqat to'rt shart birga bajarilsa: ball, margin, kadrlar
konsensusi, statistik ustunlik; sifatsiz kadrdan "tanilgan" chiqmaydi.
"Bu u emas" moslikni bekor qilib xato shablonni o'chiradi; biriktirishda
boshqa mehmonning yuzi 409 bilan to'xtatiladi.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

import app.infrastructure.database.models  # noqa: F401
from app.application.services import guest_face_service as gfs
from app.core.exceptions import ConflictException, ValidationException
from app.presentation.api.v1 import vision as vision_api

D = gfs.EMBEDDING_DIM


def unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return gfs.l2_normalize(rng.normal(size=D).astype(np.float32))


def near(base: np.ndarray, cos: float, seed: int) -> np.ndarray:
    """`base` bilan kosinusi AYNAN `cos` bo'lgan vektor (ortogonal tasodifiy
    yo'nalish qo'shiladi)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(size=D).astype(np.float32)
    r -= float(np.dot(r, base)) * base
    r = gfs.l2_normalize(r)
    return gfs.l2_normalize(cos * base + float(np.sqrt(1.0 - cos * cos)) * r)


def make_index(entries):
    """entries: [(guest_id, vector), ...] → _FaceIndex."""
    matrix = np.vstack([v for _, v in entries]).astype(np.float32)
    return gfs._FaceIndex(
        matrix=matrix,
        guest_ids=np.array([g for g, _ in entries], dtype=object),
        profile_ids=[uuid.uuid4() for _ in entries],
        version=0,
        built_at=datetime.now(timezone.utc),
    )


TARGET = uuid.uuid4()
TARGET_VEC = unit(1)
# 12 boshqa mehmon — statistik taqsimot uchun yetarli
OTHERS = [(uuid.uuid4(), unit(100 + i)) for i in range(12)]
INDEX = make_index([(TARGET, TARGET_VEC), *OTHERS])


def template(vec, samples=1, cohesion=0.9):
    return gfs.Template(vector=vec, sample_count=samples, dropped=0, cohesion=cohesion)


# ----------------------------------------------------------------- qaror
def test_clear_match_is_recognized():
    probe = near(TARGET_VEC, 0.9, 7)
    result = gfs.decide(INDEX, template(probe, samples=3), [probe, near(TARGET_VEC, 0.88, 8), near(TARGET_VEC, 0.86, 9)], quality=0.8, face_pixels=120)
    assert result.status == "recognized" and result.guest_id == TARGET
    assert result.outlier_z >= gfs.OUTLIER_Z and result.reason == ""


def test_unrelated_face_is_unknown_not_somebody_else():
    stranger = unit(555)
    result = gfs.decide(INDEX, template(stranger), [], quality=0.9, face_pixels=120)
    assert result.status == "unknown" and result.guest_id is None


def test_collapsed_embedding_similar_to_everyone_is_not_recognized():
    """Kichik/xira yuz: vektor hamma bilan birdek 0.6+ o'xshaydi — ball va
    margin bor, lekin ustunlik yo'q → "aniq emas"."""
    # Indeks a'zolari bir "o'rtacha yuz" atrofida; probe ham shunday
    mean_face = unit(42)
    entries = [(uuid.uuid4(), near(mean_face, 0.8, 300 + i)) for i in range(12)]
    index = make_index(entries)
    probe = near(mean_face, 0.82, 999)
    scores = index.matrix @ probe
    # Sinov sharti: hammasi chegaradan yuqori bo'lsin (haqiqiy "kollaps")
    assert float(scores.min()) > 0.5
    result = gfs.search_index(index, probe)
    assert result.status != "recognized", (result.score, result.margin, result.outlier_z)
    assert result.reason in {"outlier", "margin"}


def test_sample_consensus_blocks_mixed_episode():
    """O'rtacha shablon mehmonga yaqin, lekin kadrlarning ko'pi boshqa odam."""
    probe = near(TARGET_VEC, 0.9, 7)
    stranger = unit(777)
    samples = [probe, stranger, near(stranger, 0.9, 1), near(stranger, 0.9, 2)]
    result = gfs.decide(INDEX, template(probe, samples=4), samples, quality=0.8, face_pixels=120)
    assert result.status == "uncertain" and result.reason == "consensus"
    assert result.guest_id == TARGET  # nomzod saqlanadi — xodim tasdiqlashi mumkin


def test_low_quality_or_small_face_cannot_be_recognized():
    probe = near(TARGET_VEC, 0.9, 7)
    small = gfs.decide(INDEX, template(probe), [], quality=0.9, face_pixels=50)
    assert small.status == "uncertain" and small.reason == "quality"
    blurry = gfs.decide(INDEX, template(probe), [], quality=0.3, face_pixels=120)
    assert blurry.status == "uncertain" and blurry.reason == "quality"


def test_incoherent_template_is_demoted():
    probe = near(TARGET_VEC, 0.9, 7)
    result = gfs.decide(INDEX, template(probe, samples=4, cohesion=0.3), [], quality=0.9, face_pixels=120)
    assert result.status == "uncertain" and result.reason == "cohesion"


def test_thresholds_are_strict():
    assert gfs.MATCH_THRESHOLD >= 0.60 and gfs.MATCH_MARGIN >= 0.10
    assert gfs.ADAPTIVE_LEARN_THRESHOLD >= 0.70


# ------------------------------------------------------- boshqa mehmon yuzi
class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)

    def scalars(self):
        return self

    def scalar(self):
        return None


class FakeSession:
    def __init__(self, rows=(), objects=None):
        self.rows = list(rows)
        self.objects = objects or {}
        self.deleted = []
        self.added = []

    async def execute(self, stmt):
        return FakeResult(self.rows)

    async def get(self, model, key):
        return self.objects.get(key)

    async def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


@pytest.fixture(autouse=True)
def fresh_index():
    gfs.invalidate_index()
    yield
    gfs.invalidate_index()


def test_conflicting_guest_finds_other_owner_of_same_face():
    other = uuid.uuid4()
    rows = [(uuid.uuid4(), other, gfs.pack_embedding(TARGET_VEC))]
    session = FakeSession(rows)
    hit = asyncio.run(gfs.conflicting_guest(session, near(TARGET_VEC, 0.9, 3), uuid.uuid4()))
    assert hit is not None and hit.guest_id == other
    # O'sha mehmonning o'zi — ziddiyat emas
    assert asyncio.run(gfs.conflicting_guest(session, near(TARGET_VEC, 0.9, 3), other)) is None


def test_enroll_sighting_refuses_face_of_another_guest(monkeypatch):
    other_id = uuid.uuid4()
    other = SimpleNamespace(id=other_id, first_name="Anvar", last_name="Qodirov", is_deleted=False)
    guest = SimpleNamespace(id=uuid.uuid4(), first_name="Dilnoza", last_name="X", is_deleted=False, face_consent_at=None)
    sighting = SimpleNamespace(
        id=uuid.uuid4(), hotel_id="h", embedding=gfs.pack_embedding(TARGET_VEC),
        quality_score=0.8, camera_id="cam", guest_id=None, status="unknown",
        acknowledged_at=None, acknowledged_by=None,
    )
    session = FakeSession(
        rows=[(uuid.uuid4(), other_id, gfs.pack_embedding(TARGET_VEC))],
        objects={sighting.id: sighting, guest.id: guest, other_id: other},
    )
    monkeypatch.setattr(vision_api, "_hotel_id", lambda user: "h")
    user = {"id": uuid.uuid4()}
    payload = SimpleNamespace(guest_id=guest.id, consent=True, sighting_ids=[], force=False)
    with pytest.raises(ConflictException) as err:
        asyncio.run(vision_api.enroll_sighting(sighting.id, payload, session, user))
    assert err.value.error_code == "FACE_BELONGS_TO_OTHER_GUEST" and "Anvar" in err.value.detail
    assert session.added == []


# ---------------------------------------------------------------- bu u emas
def test_reject_clears_match_and_removes_learned_profiles(monkeypatch):
    guest_id = uuid.uuid4()
    learned = SimpleNamespace(id=uuid.uuid4(), guest_id=guest_id, source="vision")
    matched_manual = SimpleNamespace(id=uuid.uuid4(), guest_id=guest_id, source="manual")
    now = datetime.now(timezone.utc)
    sighting = SimpleNamespace(
        id=uuid.uuid4(), hotel_id="h", guest_id=guest_id, status="recognized",
        camera_id="cam", seen_at=now, similarity=0.61, embedding=b"x" * 512,
        matched_profile_id=matched_manual.id, learned_profile_id=learned.id,
        acknowledged_at=now, acknowledged_by=uuid.uuid4(), rejected_at=None, rejected_by=None,
    )
    sibling = SimpleNamespace(
        id=uuid.uuid4(), hotel_id="h", guest_id=guest_id, status="recognized",
        camera_id="cam", seen_at=now - timedelta(minutes=2), similarity=0.6, embedding=None,
        matched_profile_id=None, learned_profile_id=None,
        acknowledged_at=None, acknowledged_by=None, rejected_at=None, rejected_by=None,
    )
    session = FakeSession(
        rows=[sibling],
        objects={sighting.id: sighting, learned.id: learned, matched_manual.id: matched_manual},
    )
    monkeypatch.setattr(vision_api, "_hotel_id", lambda user: "h")
    user = {"id": uuid.uuid4()}
    out = asyncio.run(vision_api.reject_sighting(sighting.id, session, user))

    assert out["rejected"] is True and out["can_enroll"] is True
    # o'rganilgan shablon o'chdi; qo'lda biriktirilgan (manual) qoldi
    assert session.deleted == [learned] and out["profiles_removed"] == 1
    for member in (sighting, sibling):
        assert member.guest_id is None and member.status == "unknown"
        assert member.rejected_by == user["id"] and member.acknowledged_at is None
    assert out["sightings_cleared"] == 2


def test_reject_requires_a_match(monkeypatch):
    sighting = SimpleNamespace(id=uuid.uuid4(), hotel_id="h", guest_id=None)
    session = FakeSession(objects={sighting.id: sighting})
    monkeypatch.setattr(vision_api, "_hotel_id", lambda user: "h")
    with pytest.raises(ValidationException) as err:
        asyncio.run(vision_api.reject_sighting(sighting.id, session, {"id": uuid.uuid4()}))
    assert err.value.error_code == "NOT_MATCHED"
