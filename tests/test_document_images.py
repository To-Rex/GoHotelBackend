"""Skanerlangan pasport / ID karta SURATINI MinIO'da saqlash.

Foydalanuvchi talabi: mobil qurilmadan yoki boshqa yo'l bilan pasport yoki
ID karta skanerlanganda surati MinIO'ga saqlansin. Mavjud funksiyalar 100%
saqlansin: skaner natijasi, mehmonni topish, takror skan, qabulxona oynasi
avvalgidek ishlaydi; fayl ombori ishlamasa ham skaner to'xtamaydi.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList, False_, Null, True_

from app.application.services import document_images as di
from app.core.config import settings
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.infrastructure.database.models.document_scan import DocumentScan
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User
from app.infrastructure.storage import minio as storage
from app.presentation.api.v1 import guests as guests_api
from app.presentation.api.v1 import reception as reception_api

HOTEL = uuid.uuid4()
OTHER_HOTEL = uuid.uuid4()
DILNOZA = uuid.uuid4()
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ADMIN = {"id": DILNOZA, "user_type": "ADMIN", "hotel_id": HOTEL}
RECEPTION = {"id": DILNOZA, "user_type": "EMPLOYEE", "hotel_id": HOTEL, "permissions": ["reservation.create"]}
CLEANER = {"id": DILNOZA, "user_type": "EMPLOYEE", "hotel_id": HOTEL, "permissions": ["housekeeping.view"]}


# ------------------------------------------------------------- soxta baza --
def _value(node):
    if isinstance(node, BindParameter):
        return node.value
    if isinstance(node, True_):
        return True
    if isinstance(node, False_):
        return False
    if isinstance(node, Null):
        return None
    raise AssertionError(f"kutilmagan ifoda: {node!r}")


def _matches(obj, crit) -> bool:
    if isinstance(crit, BooleanClauseList):
        return all(_matches(obj, c) for c in crit.clauses)
    assert isinstance(crit, BinaryExpression), crit
    have = getattr(obj, crit.left.key)
    want = _value(crit.right)
    op = crit.operator
    if op is operators.eq:
        return have == want
    if op is operators.in_op:
        return have in want
    if op is operators.is_:
        return have is want or have == want
    if op is operators.ge:
        return have >= want
    raise AssertionError(f"kutilmagan operator: {op}")


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._items), first=lambda: (self._items or [None])[0])

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _Nested:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.snapshot = {k: list(v) for k, v in self.db.rows.items()}
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:  # SAVEPOINT bekor — faqat ichidagilar
            self.db.rows = self.snapshot
            self.db.rolled_back += 1
        return False


class FakeDb:
    def __init__(self, *, store_images=None, guests=(), scans=(), users=()):
        scan_settings = {} if store_images is None else {"store_images": store_images}
        self.hotel = SimpleNamespace(id=HOTEL, settings={"document_scan": scan_settings})
        self.rows = {FileAttachment: [], DocumentScan: list(scans), User: list(users)}
        self.guests = {g.id: g for g in guests}
        self.fail_flush = False
        self.rolled_back = 0
        self._clock = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)

    def add(self, obj):
        self.rows.setdefault(type(obj), []).append(obj)

    async def flush(self):
        if self.fail_flush:
            raise RuntimeError("baza javob bermadi")
        for objs in self.rows.values():
            for obj in objs:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()
                if isinstance(obj, FileAttachment):
                    if obj.is_deleted is None:
                        obj.is_deleted = False
                    if obj.created_at is None:
                        self._clock += timedelta(seconds=1)
                        obj.created_at = self._clock

    async def get(self, model, key, **kwargs):
        if model is Hotel:
            return self.hotel if key == HOTEL else None
        if model is Guest:
            return self.guests.get(key)
        return None

    def begin_nested(self):
        return _Nested(self)

    async def execute(self, stmt):
        desc = stmt.column_descriptions[0]
        model, expr = desc["entity"], desc["expr"]
        items = [o for o in self.rows.get(model, []) if stmt.whereclause is None or _matches(o, stmt.whereclause)]
        if model is FileAttachment:
            items.sort(key=lambda o: o.created_at, reverse=True)
        if expr is not model:
            items = [getattr(o, desc["name"]) for o in items]
        return _Result(items)

    def files(self, entity_type=None):
        return [f for f in self.rows[FileAttachment] if entity_type is None or f.entity_type == entity_type]


class Minio:
    """MinIO o'rnida: yozilgan obyektlar xotirada."""

    def __init__(self, monkeypatch, fail=False):
        self.objects = {}
        self.fail = fail

        async def upload(bucket, path, data, content_type):
            if self.fail:
                raise ConnectionError("MinIO o'chiq")
            self.objects[(bucket, path)] = (data, content_type)
            return path

        async def download(bucket, path):
            return self.objects[(bucket, path)][0]

        monkeypatch.setattr(storage, "upload_file", upload)
        monkeypatch.setattr(storage, "download_file", download)


class Upload:
    def __init__(self, data):
        self.data = data

    async def read(self):
        return self.data


def guest(**kw):
    return SimpleNamespace(id=uuid.uuid4(), first_name="Jasur", last_name="Toshmatov", is_deleted=False, **kw)


def scan_row(guest_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(), hotel_id=HOTEL, guest_id=guest_id, guest_name=None, document_number="AA1234567",
    )


# ------------------------------------------------------------- qoidalar --
def test_store_flag_defaults_to_on():
    assert di.store_enabled(None) is True
    assert di.store_enabled({}) is True
    assert di.store_enabled({"document_scan": {"mode": "mrz"}}) is True
    assert di.store_enabled({"document_scan": {"store_images": False}}) is False
    assert di.store_enabled({"document_scan": {"store_images": "no"}}) is True


def test_only_real_images_are_accepted():
    assert di.sniff_image(JPEG) == ("image/jpeg", "jpg")
    assert di.sniff_image(PNG) == ("image/png", "png")
    assert di.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("image/webp", "webp")
    assert di.sniff_image(b"%PDF-1.7") is None
    assert di.sniff_image(b"") is None


# ------------------------------------------------ qabulxona telefoni --
def submit(db, monkeypatch, record, *, document_type="ID_CARD", front=JPEG, back=JPEG):
    async def fake_run_scan(images, doc_type, mode):
        return {"documentType": doc_type, "documentNumber": "AA1234567", "firstName": "JASUR"}

    async def fake_record(self, hotel_id, document, *, scanned_by=None, device_id=None):
        return dict(record)

    monkeypatch.setattr(reception_api.intake, "require_server_ocr", lambda: None)
    monkeypatch.setattr(reception_api.intake, "run_scan", fake_run_scan)
    monkeypatch.setattr(reception_api.DocumentScanService, "record", fake_record)
    return asyncio.run(reception_api.submit_document_scan(
        document_type=document_type, front=Upload(front) if front else None,
        back=Upload(back) if back else None, device_id="phone-1", session=db, current_user=RECEPTION,
    ))


def test_phone_scan_saves_both_sides_and_attaches_to_found_guest(monkeypatch):
    minio = Minio(monkeypatch)
    g = guest()
    db = FakeDb(guests=[g])
    scan_id = uuid.uuid4()
    out = submit(db, monkeypatch, {"id": str(scan_id), "guest_id": str(g.id), "matched": True, "duplicate": False})
    # Skaner javobi avvalgidek, ustiga nechta surat saqlangani
    assert out["matched"] is True and out["images_saved"] == 2
    paths = sorted(p for (_, p) in minio.objects)
    assert len(paths) == 2 and all(p.startswith(f"{HOTEL}/document_scan/{scan_id}/document-") for p in paths)
    assert {b for (b, _) in minio.objects} == {settings.MINIO_BUCKET_GUESTS}
    on_scan = db.files("document_scan")
    assert sorted(f.category for f in on_scan) == ["document_back", "document_front"]
    assert all(f.entity_id == scan_id and f.uploaded_by == DILNOZA and f.mime_type == "image/jpeg" for f in on_scan)
    # Mehmon kartasiga ham — obyekt bitta (nusxa olinmaydi)
    on_guest = db.files("guest")
    assert sorted(f.minio_path for f in on_guest) == paths
    assert all(f.entity_id == g.id for f in on_guest)


def test_phone_scan_of_new_guest_keeps_image_on_the_scan(monkeypatch):
    Minio(monkeypatch)
    db = FakeDb()
    out = submit(db, monkeypatch, {"id": str(uuid.uuid4()), "guest_id": None, "duplicate": False},
                 document_type="PASSPORT", back=None)
    assert out["images_saved"] == 1
    assert [f.category for f in db.files("document_scan")] == ["document_passport"]
    assert db.files("guest") == []


def test_repeat_scan_does_not_store_a_second_copy(monkeypatch):
    minio = Minio(monkeypatch)
    db = FakeDb()
    scan_id = str(uuid.uuid4())
    submit(db, monkeypatch, {"id": scan_id, "guest_id": None, "duplicate": False})
    out = submit(db, monkeypatch, {"id": scan_id, "guest_id": None, "duplicate": True})
    assert out["duplicate"] is True and out["images_saved"] == 2
    assert len(minio.objects) == 2 and len(db.files()) == 2


def test_disabled_setting_stores_nothing(monkeypatch):
    minio = Minio(monkeypatch)
    db = FakeDb(store_images=False)
    out = submit(db, monkeypatch, {"id": str(uuid.uuid4()), "guest_id": None, "duplicate": False})
    assert out["images_saved"] == 0 and minio.objects == {} and db.files() == []


def test_storage_outage_never_breaks_the_scan(monkeypatch):
    Minio(monkeypatch, fail=True)
    db = FakeDb()
    out = submit(db, monkeypatch, {"id": str(uuid.uuid4()), "guest_id": None, "duplicate": False, "matched": False})
    assert out["images_saved"] == 0 and out["matched"] is False
    assert db.files() == []


def test_slow_storage_is_not_waited_for(monkeypatch):
    calls = []

    async def hanging_upload(bucket, path, data, content_type):
        calls.append(path)
        await asyncio.sleep(5)

    monkeypatch.setattr(storage, "upload_file", hanging_upload)
    monkeypatch.setattr(di, "UPLOAD_TIMEOUT_SECONDS", 0.05)
    db = FakeDb()
    started = datetime.now()
    out = submit(db, monkeypatch, {"id": str(uuid.uuid4()), "guest_id": None, "duplicate": False})
    assert out["images_saved"] == 0 and db.files() == []
    # Ikkinchi tomon urinilmaydi — skaner bir necha soniya ichida javob beradi
    assert len(calls) == 1 and (datetime.now() - started).total_seconds() < 2


def test_database_error_rolls_back_only_the_images(monkeypatch):
    Minio(monkeypatch)
    db = FakeDb()
    db.fail_flush = True
    out = submit(db, monkeypatch, {"id": str(uuid.uuid4()), "guest_id": None, "duplicate": False})
    assert out["images_saved"] == 0 and db.rolled_back == 1 and db.files() == []


# ------------------------------------------------- skan → yangi mehmon --
def link(db, scan_id, guest_id):
    return asyncio.run(reception_api.link_document_scan_to_guest(
        data=reception_api.ScanLinkGuestRequest(guest_id=guest_id), scan_id=scan_id, session=db,
        current_user=RECEPTION,
    ))


def test_new_guest_created_from_phone_scan_gets_the_image(monkeypatch):
    Minio(monkeypatch)
    scan = scan_row()
    g = guest()
    db = FakeDb(guests=[g], scans=[scan])
    submit(db, monkeypatch, {"id": str(scan.id), "guest_id": None, "duplicate": False})
    out = link(db, scan.id, g.id)
    assert out == {"scan_id": str(scan.id), "guest_id": str(g.id), "images": 2, "linked": 2}
    assert scan.guest_id == g.id and scan.guest_name == "Jasur Toshmatov"
    assert len(db.files("guest")) == 2
    # Qayta chaqirilsa nusxa ko'paymaydi
    assert link(db, scan.id, g.id)["linked"] == 0
    assert len(db.files("guest")) == 2


def test_link_keeps_the_guest_already_on_the_scan(monkeypatch):
    Minio(monkeypatch)
    first, chosen = guest(), guest()
    scan = scan_row(guest_id=first.id)
    db = FakeDb(guests=[first, chosen], scans=[scan])
    link(db, scan.id, chosen.id)
    assert scan.guest_id == first.id


def test_link_checks_scan_and_guest(monkeypatch):
    scan = scan_row()
    db = FakeDb(scans=[scan])
    with pytest.raises(NotFoundException):
        link(db, uuid.uuid4(), uuid.uuid4())
    with pytest.raises(NotFoundException):
        link(db, scan.id, uuid.uuid4())
    with pytest.raises(ForbiddenException):
        asyncio.run(reception_api.link_document_scan_to_guest(
            data=reception_api.ScanLinkGuestRequest(guest_id=uuid.uuid4()), scan_id=scan.id, session=db,
            current_user=CLEANER,
        ))


# ------------------------------------------------------- veb skaneri --
def upload(db, guest_id, user=ADMIN, **files):
    return asyncio.run(guests_api.upload_document_images(
        guest_id=guest_id, document_type=files.pop("document_type", "ID_CARD"),
        passport=files.get("passport"), front=files.get("front"), back=files.get("back"),
        session=db, current_user=user,
    ))


def test_web_scan_image_is_saved_on_the_guest(monkeypatch):
    minio = Minio(monkeypatch)
    g = guest()
    db = FakeDb(guests=[g])
    out = upload(db, g.id, user=RECEPTION, front=Upload(JPEG), back=Upload(PNG))
    assert out["disabled"] is False
    assert sorted(s["side"] for s in out["stored"]) == ["back", "front"]
    files = db.files("guest")
    assert {f.mime_type for f in files} == {"image/jpeg", "image/png"}
    assert all(p.startswith(f"{HOTEL}/guest/{g.id}/document-") for (_, p) in minio.objects)


def test_web_upload_rules(monkeypatch):
    Minio(monkeypatch)
    g = guest()
    db = FakeDb(guests=[g])
    with pytest.raises(ValidationException) as err:
        upload(db, g.id, front=Upload(b"%PDF-1.7 not an image"))
    assert err.value.error_code == "DOCUMENT_IMAGE_INVALID"
    with pytest.raises(ValidationException) as err:
        upload(db, g.id)
    assert err.value.error_code == "DOCUMENT_IMAGE_REQUIRED"
    with pytest.raises(NotFoundException):
        upload(db, uuid.uuid4(), front=Upload(JPEG))
    with pytest.raises(ForbiddenException):
        upload(db, g.id, user=CLEANER, front=Upload(JPEG))
    assert db.files() == []


def test_web_upload_respects_the_setting_and_reports_outage(monkeypatch):
    g = guest()
    Minio(monkeypatch)
    db = FakeDb(guests=[g], store_images=False)
    assert upload(db, g.id, passport=Upload(JPEG)) == {"stored": [], "disabled": True}
    Minio(monkeypatch, fail=True)
    db = FakeDb(guests=[g])
    with pytest.raises(di.DocumentStorageUnavailable) as err:
        upload(db, g.id, passport=Upload(JPEG))
    assert err.value.status_code == 503


def test_images_are_listed_and_served_only_inside_the_hotel(monkeypatch):
    Minio(monkeypatch)
    g = guest()
    db = FakeDb(guests=[g], users=[SimpleNamespace(id=DILNOZA, first_name="Dilnoza", last_name="R")])
    upload(db, g.id, passport=Upload(JPEG))
    # Boshqa mehmonxona yuklagan surat bu yerda ko'rinmaydi
    db.add(FileAttachment(
        hotel_id=OTHER_HOTEL, entity_type="guest", entity_id=g.id, file_name="x.jpg", original_name="x.jpg",
        mime_type="image/jpeg", file_size=3, minio_bucket="hotel-guests", minio_path="other/x.jpg",
        category="document_passport", uploaded_by=DILNOZA,
    ))
    # Mehmonning boshqa fayllari (masalan shartnoma) bu ro'yxatga kirmaydi
    db.add(FileAttachment(
        hotel_id=HOTEL, entity_type="guest", entity_id=g.id, file_name="c.pdf", original_name="c.pdf",
        mime_type="application/pdf", file_size=3, minio_bucket="hotel-documents", minio_path="c.pdf",
        category="contract", uploaded_by=DILNOZA,
    ))
    asyncio.run(db.flush())
    listed = asyncio.run(guests_api.list_document_images(guest_id=g.id, session=db, current_user=RECEPTION))
    assert [(i["side"], i["uploaded_by_name"]) for i in listed] == [("passport", "Dilnoza R")]
    resp = asyncio.run(guests_api.get_document_image(
        guest_id=g.id, file_id=uuid.UUID(listed[0]["id"]), session=db, current_user=RECEPTION,
    ))
    assert isinstance(resp, Response) and resp.body == JPEG and resp.media_type == "image/jpeg"
    assert "no-store" in resp.headers["cache-control"]
    foreign = next(f for f in db.files() if f.hotel_id == OTHER_HOTEL)
    with pytest.raises(NotFoundException):
        asyncio.run(guests_api.get_document_image(
            guest_id=g.id, file_id=foreign.id, session=db, current_user=RECEPTION,
        ))
    with pytest.raises(ForbiddenException):
        asyncio.run(guests_api.list_document_images(guest_id=g.id, session=db, current_user=CLEANER))


def test_old_photo_upload_is_shown_with_scan_images():
    assert "photo" in di.GUEST_IMAGE_CATEGORIES
    assert {"document_passport", "document_front", "document_back"} <= set(di.GUEST_IMAGE_CATEGORIES)


# ------------------------------------------------------------ sozlama --
CONFIGURATOR = {"id": DILNOZA, "user_type": "ADMIN", "actual_user_type": "CONFIGURATOR", "hotel_id": HOTEL}


def save(db, **body):
    return asyncio.run(guests_api.save_scan_settings(
        data=guests_api.ScanSettingsRequest(**body), session=db, current_user=CONFIGURATOR,
    ))


def test_scan_settings_flag():
    assert guests_api._resolve_scan(None)["store_images"] is True
    db = FakeDb()
    assert save(db, mode="mrz", engine="server", store_images=False)["store_images"] is False
    # Eski ilova bayroqni yubormaydi — saqlangani o'zgarmaydi
    out = save(db, mode="auto", engine="device")
    assert out["store_images"] is False and out["mode"] == "auto" and out["engine"] == "device"
    assert db.hotel.settings["document_scan"] == {"mode": "auto", "engine": "device", "store_images": False}
    assert save(db, mode="auto", engine="device", store_images=True)["store_images"] is True


def test_reset_drops_scan_rows_but_keeps_guest_images():
    from app.application.services.maintenance_service import FILE_ENTITY_TYPES

    assert "document_scan" in FILE_ENTITY_TYPES and "guest" not in FILE_ENTITY_TYPES
