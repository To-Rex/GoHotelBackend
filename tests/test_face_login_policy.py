"""Yuz bilan kirish siyosati — bazasiz.

Yuz biriktirgan hisobga faqat O'SHA yuz bilan kiriladi:
* verify-login: mos kelmagan yuz 401 FACE_MISMATCH, mos kelgani token oladi;
* "kamerasiz kirish" kamerasiz qurilmada parol bilan o'tkazadi (sabab yoziladi);
* yuz biriktirish: avvalgi profilga mos kelmagan (boshqa odam) namuna rad.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

import app.infrastructure.database.models  # noqa: F401
from app.application.services import face_service
from app.core.exceptions import UnauthorizedException, ValidationException
from app.presentation.api.v1 import auth as auth_api
from app.presentation.api.v1 import face as face_api

SAME = [1.0, 0.0, 0.0, 0.0]
NEAR = [0.95, 0.3, 0.0, 0.0]  # o'sha odam (kosinus ≈ 0.95)
OTHER = [0.0, 1.0, 0.0, 0.0]  # boshqa odam (kosinus 0)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar(self):
        return len(self.rows)


class FakeSession:
    def __init__(self, profiles=()):
        self.profiles = list(profiles)
        self.added = []
        self.deleted = []

    async def execute(self, stmt):
        return FakeResult(self.profiles)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass


class FakeUpload:
    def __init__(self, marker: bytes):
        self.marker = marker
        self.filename = "face.jpg"

    async def read(self):
        return self.marker


def profile(vector):
    return SimpleNamespace(
        id=uuid.uuid4(),
        embedding=json.dumps(vector),
        created_at=None,
        last_used_at=None,
    )


@pytest.fixture
def engine(monkeypatch):
    """Dvigatel bor; rasm "marker"iga qarab vektor qaytadi."""
    vectors = {b"same": SAME, b"near": NEAR, b"other": OTHER}
    monkeypatch.setattr(face_service, "engine_importable", lambda: True)
    monkeypatch.setattr(face_service, "compute_embedding", lambda content: vectors[content])
    return vectors


@pytest.fixture
def auth(monkeypatch):
    """Challenge → xodim; token berish qayd etiladi."""
    user = SimpleNamespace(id=uuid.uuid4(), username="dilnoza")
    issued = []

    class FakeAuth:
        def __init__(self, session):
            pass

        async def resolve_face_challenge(self, token):
            assert token == "tok"
            return user

        async def issue_tokens(self, u, **kw):
            issued.append(u.username)
            return {"access_token": "a", "refresh_token": "r"}

        async def complete_login_without_face(self, token, **kw):
            issued.append("no-camera")
            return {"access_token": "a", "refresh_token": "r"}

    monkeypatch.setattr(face_api, "AuthService", FakeAuth)
    monkeypatch.setattr(auth_api, "AuthService", FakeAuth)
    return user, issued


REQ = SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"), headers={})


def test_verify_login_accepts_only_the_enrolled_face(engine, auth):
    _, issued = auth
    session = FakeSession([profile(SAME)])
    with pytest.raises(UnauthorizedException) as err:
        asyncio.run(face_api.verify_login(REQ, "tok", FakeUpload(b"other"), session))
    assert err.value.error_code == "FACE_MISMATCH" and issued == []

    tokens = asyncio.run(face_api.verify_login(REQ, "tok", FakeUpload(b"near"), session))
    assert tokens["access_token"] == "a" and issued == ["dilnoza"]
    assert session.profiles[0].last_used_at is not None


def test_no_camera_login_lets_a_camera_less_device_in(engine, auth):
    """Kamerasiz kompyuter: parol yetarli (dvigatel bor bo'lsa ham)."""
    _, issued = auth
    data = SimpleNamespace(face_token="tok", reason="qurilmada kamera topilmadi")
    tokens = asyncio.run(auth_api.login_without_camera(data, REQ, FakeSession()))
    assert tokens["access_token"] == "a" and issued == ["no-camera"]


def test_verify_login_without_engine_is_503_with_code(monkeypatch, auth):
    from app.core.exceptions import ServiceUnavailableException

    monkeypatch.setattr(face_service, "engine_importable", lambda: False)
    with pytest.raises(ServiceUnavailableException) as err:
        asyncio.run(face_api.verify_login(REQ, "tok", FakeUpload(b"same"), FakeSession()))
    assert err.value.error_code == "FACE_ENGINE_UNAVAILABLE" and err.value.status_code == 503


def test_enroll_rejects_another_persons_face(engine):
    user = {"id": uuid.uuid4()}
    session = FakeSession([profile(SAME)])
    with pytest.raises(ValidationException) as err:
        asyncio.run(face_api.face_enroll(FakeUpload(b"other"), session, user))
    assert err.value.error_code == "FACE_NOT_SAME_PERSON"
    assert session.added == []


def test_enroll_accepts_same_person_and_first_sample(engine):
    user = {"id": uuid.uuid4()}
    first = FakeSession([])
    result = asyncio.run(face_api.face_enroll(FakeUpload(b"other"), first, user))
    assert result["enrolled"] is True and len(first.added) == 1

    again = FakeSession([profile(SAME)])
    result = asyncio.run(face_api.face_enroll(FakeUpload(b"near"), again, user))
    assert result["enrolled"] is True and len(again.added) == 1
    assert json.loads(again.added[0].embedding) == NEAR
