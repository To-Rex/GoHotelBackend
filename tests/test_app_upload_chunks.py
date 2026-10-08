"""Dasturlar do'koniga bo'laklab yuklash (app_upload_service) — bazasiz.

Fayl bo'laklarga bo'linib alohida so'rovlarda keladi, server ularni yig'ib
MinIO'ga oqim bilan yozadi. Tekshiriladi: tekshiruvlar, bo'lakni qayta
yuborish, to'liq bo'lmagan yuklash, yig'ilgan fayl asl fayl bilan baytma-bayt
bir xilligi, eski yuklashlarni tozalash va HTTP oqimi (eski bir martalik
yuklash ham ishlayveradi).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from types import SimpleNamespace

import pytest

import app.infrastructure.database.models  # noqa: F401 — modellar ro'yxati
from app.application.services import app_store_service, app_upload_service
from app.application.services.app_upload_service import AppUploadService
from app.core.exceptions import ConflictException, NotFoundException, ValidationException


class FakeStorage:
    """MinIO o'rniga: nima yozilganini eslab qoladi."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    async def upload_file_from_path(self, bucket, path, file_path, content_type):
        with open(file_path, "rb") as f:
            self.objects[(bucket, path)] = f.read()
        return path

    async def upload_file(self, bucket, path, data, content_type):
        self.objects[(bucket, path)] = bytes(data)
        return path


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(app_upload_service, "CHUNK_SIZE", 1024)
    monkeypatch.setattr(app_upload_service, "MAX_CHUNK_BYTES", 4096)


@pytest.fixture
def storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(app_store_service.storage, "upload_file_from_path", fake.upload_file_from_path)
    monkeypatch.setattr(app_store_service.storage, "upload_file", fake.upload_file)
    return fake


def start(svc, default_size, **over):
    args = dict(platform="ANDROID", name="GoHotels Staff", version="1.0.3", notes=None,
                filename="staff.apk", size=default_size, content_type="application/octet-stream")
    args.update(over)
    return svc.start(**args)


def chunks_of(data: bytes, size: int) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


# --------------------------------------------------------------- xizmat --
def test_start_validates_input(tmp_path, small_chunks):
    svc = AppUploadService(root=tmp_path)
    for over, code in (
        ({"platform": "IOS"}, "INVALID_PLATFORM"),
        ({"name": "  "}, "NAME_REQUIRED"),
        ({"size": 0}, "EMPTY_FILE"),
        ({"size": app_store_service.MAX_FILE_BYTES + 1}, "FILE_TOO_LARGE"),
    ):
        with pytest.raises(ValidationException) as err:
            start(svc, 5000, **over)
        assert err.value.error_code == code
    session = start(svc, 2500)
    assert session["chunk_size"] == 1024 and session["total_chunks"] == 3
    assert (tmp_path / session["upload_id"] / "meta.json").is_file()


def test_chunks_are_validated_and_resendable(tmp_path, small_chunks):
    svc = AppUploadService(root=tmp_path)
    data = os.urandom(2500)
    uid = start(svc, len(data))["upload_id"]
    parts = chunks_of(data, 1024)
    with pytest.raises(ValidationException) as err:
        svc.write_chunk(uid, 3, parts[0])
    assert err.value.error_code == "CHUNK_INDEX"
    with pytest.raises(ValidationException) as err:
        svc.write_chunk(uid, 0, parts[0][:-1])
    assert err.value.error_code == "CHUNK_SIZE"
    with pytest.raises(ValidationException) as err:
        svc.write_chunk(uid, 2, parts[0])  # oxirgi bo'lak 452 bayt bo'lishi kerak
    assert err.value.error_code == "CHUNK_SIZE"
    assert svc.write_chunk(uid, 0, parts[0])["received"] == 1
    assert svc.write_chunk(uid, 0, parts[0])["received"] == 1  # takror — xavfsiz
    assert svc.status(uid)["missing"] == [1, 2]
    with pytest.raises(NotFoundException):
        svc.status("deadbeef")
    with pytest.raises(NotFoundException):
        svc.status("../etc")


def test_complete_requires_all_chunks_then_assembles_exactly(tmp_path, small_chunks, storage):
    svc = AppUploadService(root=tmp_path)
    data = os.urandom(10_000)  # 9 to'liq bo'lak + 784 bayt
    uid = start(svc, len(data), version=" 2.0.0 ", notes="  Yangi  ")["upload_id"]
    parts = chunks_of(data, 1024)
    session = FakeSession()
    for i, part in enumerate(parts):
        if i == 4:
            continue
        svc.write_chunk(uid, i, part)
    with pytest.raises(ConflictException) as err:
        asyncio.run(svc.complete(session, uid))
    assert err.value.error_code == "UPLOAD_INCOMPLETE" and "4" in err.value.detail
    svc.write_chunk(uid, 4, parts[4])

    release = asyncio.run(svc.complete(session, uid))
    assert release["platform"] == "ANDROID" and release["name"] == "GoHotels Staff"
    assert release["version"] == "2.0.0" and release["notes"] == "Yangi"
    assert release["file_size"] == len(data) and release["original_name"] == "staff.apk"
    assert release["mime_type"] == "application/vnd.android.package-archive"
    assert len(storage.objects) == 1
    (bucket, path), stored = next(iter(storage.objects.items()))
    assert stored == data  # baytma-bayt
    assert path.startswith("app-store/android/") and path.endswith("/staff.apk")
    assert session.added[0].uploaded_by is None
    # Vaqtinchalik papka tozalandi; sessiya endi yo'q
    assert not (tmp_path / uid).exists()
    with pytest.raises(NotFoundException):
        svc.status(uid)


def test_single_chunk_file_and_uploaded_by(tmp_path, small_chunks, storage):
    svc = AppUploadService(root=tmp_path)
    data = b"x" * 300
    actor = uuid.uuid4()
    uid = start(svc, len(data), uploaded_by=actor)["upload_id"]
    svc.write_chunk(uid, 0, data)
    session = FakeSession()
    release = asyncio.run(svc.complete(session, uid))
    assert release["file_size"] == 300 and session.added[0].uploaded_by == actor


def test_abort_and_stale_cleanup(tmp_path, small_chunks):
    svc = AppUploadService(root=tmp_path)
    uid = start(svc, 2000)["upload_id"]
    svc.abort(uid)
    assert not (tmp_path / uid).exists()
    svc.abort(uid)  # ikkinchi marta — xato emas

    old = start(svc, 2000)["upload_id"]
    fresh = start(svc, 2000)["upload_id"]
    past = time.time() - app_upload_service.STALE_AFTER_SECONDS - 60
    os.utime(tmp_path / old, (past, past))
    assert svc.cleanup_stale() == 1
    assert not (tmp_path / old).exists() and (tmp_path / fresh).exists()


# ------------------------------------------------------------------ HTTP --
@pytest.fixture
def client(tmp_path, small_chunks, storage, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.core.exceptions import AppException
    from app.superadmin import router as panel

    monkeypatch.setattr(app_upload_service, "UPLOAD_ROOT", tmp_path)
    session = FakeSession()
    api = FastAPI()
    api.include_router(panel.router)
    api.dependency_overrides[get_db] = lambda: session
    api.dependency_overrides[panel.current_panel_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), is_root=True, label="egasi"
    )

    @api.exception_handler(AppException)
    async def handler(request: Request, exc: AppException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "error_code": exc.error_code})

    with TestClient(api) as c:
        yield c


def test_http_chunked_flow(client, storage):
    data = os.urandom(3000)
    r = client.post("/apps/uploads", json={
        "platform": "WINDOWS", "name": "GoHotelsVision", "version": "1.1.0",
        "filename": "GoHotelsVision-Setup.exe", "size": len(data), "content_type": "application/x-msdownload",
    })
    assert r.status_code == 200, r.text
    uid, total = r.json()["upload_id"], r.json()["total_chunks"]
    assert total == 3
    for i, part in enumerate(chunks_of(data, 1024)):
        rr = client.put(f"/apps/uploads/{uid}/chunks/{i}", content=part,
                        headers={"Content-Type": "application/octet-stream"})
        assert rr.status_code == 200, rr.text
    assert client.get(f"/apps/uploads/{uid}").json()["missing"] == []
    done = client.post(f"/apps/uploads/{uid}/complete")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["platform"] == "WINDOWS" and body["file_size"] == 3000
    assert body["mime_type"] == "application/vnd.microsoft.portable-executable"
    assert next(iter(storage.objects.values())) == data


def test_http_rejects_oversized_chunk_and_unknown_upload(client):
    r = client.post("/apps/uploads", json={"platform": "ANDROID", "name": "x", "filename": "a.apk", "size": 10})
    uid = r.json()["upload_id"]
    big = client.put(f"/apps/uploads/{uid}/chunks/0", content=b"z" * 5000,
                     headers={"Content-Type": "application/octet-stream"})
    assert big.status_code == 422 and big.json()["error_code"] == "CHUNK_TOO_LARGE"
    assert client.post("/apps/uploads/0123456789abcdef0123456789abcdef/complete").status_code == 404
    assert client.delete(f"/apps/uploads/{uid}").json() == {"aborted": True}
    assert client.get(f"/apps/uploads/{uid}").status_code == 404


def test_http_legacy_single_request_upload_still_works(client, storage):
    data = os.urandom(1500)
    r = client.post(
        "/apps",
        data={"platform": "ANDROID", "name": "Eski yo'l", "version": "0.9"},
        files={"file": ("old.apk", data, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["file_size"] == 1500 and next(iter(storage.objects.values())) == data
