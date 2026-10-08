"""Mehmonxonani butunlay o'chirish — panel endpointi to'siqlari.

Bazasiz: tizim egasi, parol va mehmonxona kodi tekshiruvlari hamda
to'siqdan o'tmagan so'rov xizmatga umuman yetib bormasligi.
O'chirish algoritmining o'zi: tests/test_hotel_purge.py (PostgreSQL).
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.infrastructure.database.models  # noqa: E402,F401
from app.core.exceptions import ForbiddenException, ValidationException  # noqa: E402
from app.superadmin import router as panel  # noqa: E402
from app.superadmin import security  # noqa: E402

PASSWORD = "Egasi-parol-1"
HASH = security.hash_password(PASSWORD)


def actor(is_root=True):
    return SimpleNamespace(id=uuid4(), is_root=is_root, password_hash=HASH, label=None)


def request(code="GRAND", password=PASSWORD):
    return panel.HotelPurgeRequest(confirm_code=code, password=password)


def test_gates_pass_for_root_with_password_and_code():
    panel.check_purge_gates(actor(), "GRAND", request())
    # Atrofdagi bo'shliq kechiriladi
    panel.check_purge_gates(actor(), "GRAND", request(code="  GRAND "))


def test_only_root_can_purge():
    with pytest.raises(ForbiddenException) as err:
        panel.check_purge_gates(actor(is_root=False), "GRAND", request())
    assert err.value.error_code == "ROOT_ONLY"


def test_wrong_password_is_403_not_401():
    with pytest.raises(ForbiddenException) as err:
        panel.check_purge_gates(actor(), "GRAND", request(password="xato"))
    assert err.value.error_code == "WRONG_PASSWORD"
    assert err.value.status_code == 403  # panel sessiyasi yopilmasin


@pytest.mark.parametrize("code", ["grand", "GRAN", "GRAND2", "OTHER"])
def test_code_must_match_exactly(code):
    with pytest.raises(ValidationException) as err:
        panel.check_purge_gates(actor(), "GRAND", request(code=code))
    assert err.value.error_code == "CONFIRM_CODE_MISMATCH"


def test_empty_hotel_code_never_matches():
    with pytest.raises(ValidationException):
        panel.check_purge_gates(actor(), "", request(code=" "))


class FakeEstate:
    def __init__(self, session):
        pass

    async def get_hotel(self, hotel_id):
        return {"id": str(hotel_id), "code": "GRAND", "status": "INACTIVE"}


def install_fakes(monkeypatch):
    calls = []

    class FakePurge:
        def __init__(self, session):
            pass

        async def purge(self, hotel_id, *, actor=None):
            calls.append((hotel_id, actor))
            return {"total_rows": 7}

    monkeypatch.setattr(panel, "EstateService", FakeEstate)
    monkeypatch.setattr(panel, "HotelPurgeService", FakePurge)
    return calls


def test_endpoint_calls_service_after_gates(monkeypatch):
    calls = install_fakes(monkeypatch)
    hotel_id = uuid4()
    who = actor()
    result = asyncio.run(panel.purge_hotel(request(), hotel_id, None, who))
    assert result == {"total_rows": 7}
    assert calls == [(hotel_id, f"{security.ROOT_LABEL} ({who.id})")]


@pytest.mark.parametrize(
    "who, data",
    [
        (actor(is_root=False), request()),
        (actor(), request(password="xato")),
        (actor(), request(code="BOSHQA")),
    ],
)
def test_endpoint_refused_request_never_reaches_service(monkeypatch, who, data):
    calls = install_fakes(monkeypatch)
    with pytest.raises((ForbiddenException, ValidationException)):
        asyncio.run(panel.purge_hotel(data, uuid4(), None, who))
    assert calls == []


def test_routes_registered_and_deactivate_kept():
    routes = {(r.path, tuple(sorted(r.methods))) for r in panel.router.routes}
    assert ("/hotels/{hotel_id}/purge-preview", ("GET",)) in routes
    assert ("/hotels/{hotel_id}/purge", ("POST",)) in routes
    # Eski "to'xtatish" (yozuvni saqlaydi) o'zgarmagan
    assert ("/hotels/{hotel_id}", ("DELETE",)) in routes
