"""Kamera tanigan mehmonlar paneli: bir odam — bir qator.

Foydalanuvchi talabi: "Kamera tanidi" panelida bir kishi bir necha marta
ko'rinib qolmasin. Odam kamera oldida turgan yoki qayta o'tgan har safar
yangi epizod yoziladi — panel ularni mehmon bo'yicha yig'adi, "olib
tashlash" esa shu mehmonning barcha ko'rinishlarini yopadi. Eski so'rov
shakli (guruhlashsiz) o'zgarmaydi.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.presentation.api.v1 import vision

HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()
ALI = uuid.uuid4()
VALI = uuid.uuid4()
USER = {"id": uuid.uuid4(), "user_type": "ADMIN", "hotel_id": HOTEL, "permissions": []}
T0 = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


def row(guest_id, minutes_ago, quality=0.5, thumb=True, camera="Kirish"):
    return SimpleNamespace(
        id=uuid.uuid4(), status="recognized" if guest_id else "unknown", camera_id="cam-1",
        camera_name=camera, location=None, seen_at=T0 - timedelta(minutes=minutes_ago),
        similarity=0.8, margin=0.2, quality_score=quality, guest_id=guest_id, branch_id=BRANCH,
        acknowledged_at=None, has_embedding=True, has_thumbnail=thumb,
        first_name="Ali" if guest_id == ALI else "Vali", last_name="Karimov", phone=None,
    )


# ------------------------------------------------------------ guruhlash --
def test_one_row_per_guest_latest_time_best_photo():
    a1 = row(ALI, 1, quality=0.4)
    v1 = row(VALI, 2)
    a2 = row(ALI, 3, quality=0.9)          # eng aniq surat
    a3 = row(ALI, 5, quality=0.95, thumb=False)  # suratsiz — tanlanmaydi
    stranger1, stranger2 = row(None, 4), row(None, 6)
    out = vision.distinct_by_guest([a1, v1, a2, stranger1, a3, stranger2], limit=10)
    assert [(r.id, n) for r, n, _ in out] == [(a1.id, 3), (v1.id, 1), (stranger1.id, 1), (stranger2.id, 1)]
    # Vaqt — eng so'nggi ko'rinishniki, surat — eng sifatlisi
    assert out[0][2] is a2
    assert out[1][2] is v1


def test_limit_counts_people_not_rows():
    rows = [row(ALI, i) for i in range(8)] + [row(VALI, 9)]
    out = vision.distinct_by_guest(rows, limit=2)
    assert [r.guest_id for r, _, _ in out] == [ALI, VALI]
    assert vision.distinct_by_guest(rows, limit=1)[0][1] == 8


def test_group_without_any_photo():
    out = vision.distinct_by_guest([row(ALI, 1, thumb=False), row(ALI, 2, thumb=False)], limit=5)
    assert out[0][1] == 2 and out[0][2] is None


# ------------------------------------------------------------ endpoint --
class Result:
    def __init__(self, rows=None, scalar=None, rowcount=0):
        self._rows, self._scalar, self.rowcount = rows or [], scalar, rowcount

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class ListDb:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        n = len(self.statements)
        if n == 1:
            return Result(self.rows)
        if n == 2 and any(r.guest_id for r in self.rows):
            # (guest_id, tashriflar, oxirgisi, ochiq bronlar)
            return Result([(ALI, 3, T0, 1)])
        return Result(scalar=len(self.rows))


def listing(rows, **kw):
    db = ListDb(rows)
    params = dict(minutes=30, limit=10, include_acknowledged=False, only_matched=True,
                  only_unmatched=False, branch_id=BRANCH, distinct_guests=False)
    params.update(kw)
    out = asyncio.run(vision.list_sightings(**params, session=db, current_user=USER))
    return out, db


def test_panel_mode_returns_each_guest_once():
    a1, a2, v1 = row(ALI, 1, quality=0.3), row(ALI, 2, quality=0.9), row(VALI, 3, thumb=False)
    out, db = listing([a1, a2, v1], distinct_guests=True)
    assert [i.guest_id for i in out.items] == [ALI, VALI]
    ali = out.items[0]
    assert ali.id == a1.id and ali.sighting_count == 2 and ali.image_sighting_id == a2.id
    assert ali.has_thumbnail and ali.has_active_reservation and ali.visits == 3
    assert out.items[1].has_thumbnail is False and out.items[1].image_sighting_id is None
    # Guruhlash uchun oyna kengroq o'qiladi; `limit` odamlarga qo'llanadi
    assert db.statements[0]._limit_clause.value == vision.DISTINCT_SCAN_ROWS


def test_old_mode_is_unchanged():
    a1, a2 = row(ALI, 1), row(ALI, 2, thumb=False)
    out, db = listing([a1, a2])
    assert [i.id for i in out.items] == [a1.id, a2.id]
    assert [i.sighting_count for i in out.items] == [1, 1]
    assert out.items[0].image_sighting_id == a1.id and out.items[1].image_sighting_id is None
    assert db.statements[0]._limit_clause.value == 10


# --------------------------------------------------------- olib tashlash --
class AckDb:
    def __init__(self, sighting, rowcount=0):
        self.sighting = sighting
        self.rowcount = rowcount
        self.updates = []

    async def get(self, model, key):
        return self.sighting if key == self.sighting.id else None

    async def execute(self, stmt):
        self.updates.append(stmt)
        return Result(rowcount=self.rowcount)

    async def flush(self):
        return None


def ack(db, **kw):
    return asyncio.run(vision.acknowledge_sighting(
        sighting_id=db.sighting.id, session=db, current_user=USER, all_for_guest=kw.get("all_for_guest", False),
    ))


def test_dismiss_closes_every_sighting_of_that_guest_in_the_branch():
    s = SimpleNamespace(id=uuid.uuid4(), hotel_id=HOTEL, guest_id=ALI, branch_id=BRANCH,
                        acknowledged_at=None, acknowledged_by=None)
    db = AckDb(s, rowcount=4)
    out = ack(db, all_for_guest=True)
    assert out == {"acknowledged": True, "closed": 5}
    assert s.acknowledged_at is not None and s.acknowledged_by == USER["id"]
    sql = str(db.updates[0].compile(compile_kwargs={"literal_binds": False}))
    assert "face_sightings.guest_id" in sql and "face_sightings.branch_id" in sql
    assert "face_sightings.acknowledged_at IS NULL" in sql


def test_plain_dismiss_is_unchanged():
    s = SimpleNamespace(id=uuid.uuid4(), hotel_id=HOTEL, guest_id=ALI, branch_id=BRANCH,
                        acknowledged_at=None, acknowledged_by=None)
    db = AckDb(s)
    assert ack(db) == {"acknowledged": True, "closed": 1}
    assert db.updates == []
    # Tanilmagan ko'rinishda guruh yo'q
    s2 = SimpleNamespace(id=uuid.uuid4(), hotel_id=HOTEL, guest_id=None, branch_id=BRANCH,
                         acknowledged_at=None, acknowledged_by=None)
    db2 = AckDb(s2)
    assert ack(db2, all_for_guest=True)["closed"] == 1 and db2.updates == []
