"""Mehmon yuzini tanish — butun tizim uchun bitta indeks (bazasiz).

Grand'da biriktirilgan yuz Anna Hostel filialidagi kameradan kelganda ham
tanilishi kerak: indeks mehmonxona/filialga bo'linmaydi, so'rovda
`hotel_id` sharti yo'q, o'chirish hamma joydan.
"""
from __future__ import annotations

import asyncio
import uuid

import numpy as np
import pytest

import app.infrastructure.database.models  # noqa: F401
from app.application.services import guest_face_service as gfs


def vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return gfs.l2_normalize(rng.normal(size=gfs.EMBEDDING_DIM).astype(np.float32))


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class FakeSession:
    """`execute` indeks qatorlarini qaytaradi va so'rovni eslab qoladi."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.rows)


@pytest.fixture(autouse=True)
def fresh_index():
    gfs.invalidate_index()
    yield
    gfs.invalidate_index()


GRAND_GUEST = uuid.uuid4()
ANNA_GUEST = uuid.uuid4()
# Ikki mehmonxonada biriktirilgan ikki mehmon — indeks ikkalasini ham oladi
ROWS = [
    (uuid.uuid4(), GRAND_GUEST, gfs.pack_embedding(vec(1))),
    (uuid.uuid4(), ANNA_GUEST, gfs.pack_embedding(vec(2))),
]


def test_index_statement_has_no_hotel_or_branch_filter():
    sql = str(gfs.index_statement().compile(compile_kwargs={"literal_binds": True}))
    where = sql.split("WHERE", 1)[1]
    assert "hotel_id" not in where and "branch_id" not in where
    assert "is_deleted" in where  # o'chirilgan mehmon indeksga kirmaydi


def test_guest_enrolled_at_one_hotel_is_recognized_anywhere():
    session = FakeSession(ROWS)
    # Anna Hostel kamerasi Grand mehmonini ko'rdi — mehmonxona so'ralmaydi
    result = asyncio.run(gfs.identify(session, vec(1)))
    assert result.status == "recognized" and result.guest_id == GRAND_GUEST
    assert result.candidates == 2

    result = asyncio.run(gfs.identify(session, vec(2)))
    assert result.guest_id == ANNA_GUEST

    # Begona yuz — hech kim
    assert asyncio.run(gfs.identify(session, vec(99))).status == "unknown"


def test_index_is_cached_until_invalidated():
    session = FakeSession(ROWS)
    asyncio.run(gfs.identify(session, vec(1)))
    asyncio.run(gfs.identify(session, vec(2)))
    assert len(session.statements) == 1  # ikkinchi qidiruv keshdan

    stats = gfs.index_stats()
    assert stats["loaded"] is True and stats["profiles"] == 2 and stats["stale"] is False

    gfs.invalidate_index()
    assert gfs.index_stats()["stale"] is True
    asyncio.run(gfs.identify(session, vec(1)))
    assert len(session.statements) == 2  # qayta qurildi


def test_forget_guest_removes_everywhere_without_hotel():
    """Signaturada `hotel_id` yo'q — o'chirish mehmonxonaga bog'lanmaydi."""
    import inspect

    params = inspect.signature(gfs.forget_guest).parameters
    assert "hotel_id" not in params and "guest_id" in params
    assert "hotel_id" not in inspect.signature(gfs.identify).parameters
