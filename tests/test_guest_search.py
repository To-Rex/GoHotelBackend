"""Mehmon qidiruvi: telefon formati va JSHSHIR ham topiladi."""
import asyncio
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.infrastructure.database.repositories.guest_repo import GuestRepository


class _Result:
    def scalars(self):
        return SimpleNamespace(all=lambda: [])


class _Session:
    def __init__(self):
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        return _Result()


def _sql(query: str) -> str:
    session = _Session()
    asyncio.run(GuestRepository(session).search(None, query))
    return str(
        session.stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_phone_is_matched_without_format():
    sql = _sql("+998 99 534 03 13")
    assert "regexp_replace" in sql
    assert "995340313" in sql


def test_id_document_number_is_searchable():
    assert "id_document_number" in _sql("12345678901234")
