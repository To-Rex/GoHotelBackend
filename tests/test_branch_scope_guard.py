"""Filial himoyasi (ORM darajasi): yozuv boshqa filial yozuviga bog'lanmasin.

Bu — chuqurlashtirilgan himoya: xizmat qatlami otani baribir filial bilan
yuklaydi, lekin biror yo'l o'tkazib yuborilsa ham ORM (before_flush)
filiallararo bog'lanishni rad etadi. Sinov SQLite'da (sinxron sessiya) —
hodisalar bir xil ishlaydi va tez.
"""
import os
import uuid

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models import *  # noqa: F401,F403
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.room import Room
from app.infrastructure.tenant.branch_scope import (
    install_branch_scope,
    set_branch_scope,
)

HOTEL = uuid.uuid4()
BRANCH_A = uuid.uuid4()
BRANCH_B = uuid.uuid4()
USER = uuid.uuid4()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    # Faqat kerakli jadvallar (JSONB ustunlar SQLite'da yaratilmaydi)
    Room.__table__.create(engine)
    HousekeepingTask.__table__.create(engine)
    install_branch_scope()
    yield engine
    engine.dispose()


def _room(branch: uuid.UUID) -> Room:
    return Room(
        id=uuid.uuid4(),
        hotel_id=HOTEL,
        branch_id=branch,
        floor_id=uuid.uuid4(),
        room_type_id=uuid.uuid4(),
        room_number=f"R-{uuid.uuid4().hex[:6]}",
    )


def _seed(engine):
    """Ikki filialda xona va vazifa (filialsiz kontekstda, aniq qiymatlar)."""
    with Session(engine) as s:
        room_a, room_b = _room(BRANCH_A), _room(BRANCH_B)
        s.add_all([room_a, room_b])
        s.flush()
        s.add(
            HousekeepingTask(
                id=uuid.uuid4(), hotel_id=HOTEL, branch_id=BRANCH_A,
                room_id=room_a.id, task_type="CLEANING", created_by=USER,
            )
        )
        s.commit()
        return room_a.id, room_b.id


def test_same_branch_parent_is_allowed(db):
    room_a, _ = _seed(db)
    with Session(db) as s:
        set_branch_scope(s, HOTEL, BRANCH_A)
        task = HousekeepingTask(
            id=uuid.uuid4(), hotel_id=HOTEL, room_id=room_a,
            task_type="CLEANING", created_by=USER,
        )
        s.add(task)
        s.flush()  # xato bo'lmasligi kerak
        assert str(task.branch_id) == str(BRANCH_A)  # avtomatik to'ldirildi


def test_new_task_pointing_other_branch_room_is_rejected(db):
    _, room_b = _seed(db)
    with Session(db) as s:
        set_branch_scope(s, HOTEL, BRANCH_A)
        s.add(
            HousekeepingTask(
                id=uuid.uuid4(), hotel_id=HOTEL, room_id=room_b,
                task_type="CLEANING", created_by=USER,
            )
        )
        with pytest.raises(ForbiddenException) as exc:
            s.flush()
        assert exc.value.error_code == "BRANCH_SCOPE"


def test_repairing_existing_row_to_other_branch_parent_is_rejected(db):
    room_a, room_b = _seed(db)
    with Session(db) as s:
        set_branch_scope(s, HOTEL, BRANCH_A)
        task = HousekeepingTask(
            id=uuid.uuid4(), hotel_id=HOTEL, room_id=room_a,
            task_type="CLEANING", created_by=USER,
        )
        s.add(task)
        s.commit()  # o'z filial xonasi bilan yaratildi — ruxsat
        # Endi boshqa filial xonasiga o'tkazamiz — rad etilishi kerak
        task.room_id = room_b
        with pytest.raises(ForbiddenException) as exc:
            s.flush()
        assert exc.value.error_code == "BRANCH_SCOPE"


def test_without_scope_cross_branch_link_is_kept(db):
    """Filialsiz kontekst (fon, panel) avvalgidek ishlaydi."""
    _, room_b = _seed(db)
    with Session(db) as s:
        s.add(
            HousekeepingTask(
                id=uuid.uuid4(), hotel_id=HOTEL, branch_id=BRANCH_B,
                room_id=room_b, task_type="CLEANING", created_by=USER,
            )
        )
        s.flush()  # xato bo'lmasligi kerak


def test_explicit_other_branch_write_still_rejected(db):
    """Eski qoida hamon ishlaydi: yozuvga boshqa filialni yozib bo'lmaydi."""
    with Session(db) as s:
        set_branch_scope(s, HOTEL, BRANCH_A)
        s.add(
            HousekeepingTask(
                id=uuid.uuid4(), hotel_id=HOTEL, branch_id=BRANCH_B,
                room_id=uuid.uuid4(), task_type="CLEANING", created_by=USER,
            )
        )
        with pytest.raises(ForbiddenException) as exc:
            s.flush()
        assert exc.value.error_code == "BRANCH_SCOPE"
