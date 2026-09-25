"""Baza bilan aloqa uzilganda: aniqlash, 503 ga aylantirish, pool sozlamalari.

Baza serveri qayta ishga tushganda ("in recovery mode") yoki pool'dagi
ulanishni server yopganda so'rovlar 500 INTERNAL_ERROR emas, 503
DB_UNAVAILABLE bo'lishi kerak; dastur xatolari esa avvalgidek 500 qoladi.
"""
import asyncio
from contextlib import asynccontextmanager

import asyncpg.exceptions as pg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import database
from app.core.db_errors import (
    DB_UNAVAILABLE_CODE,
    DatabaseUnavailableError,
    db_unavailable_payload,
    is_connection_lost,
    short_error,
)


def _raise_from_module(module_name: str, exc: BaseException) -> BaseException:
    """Istisnoni `__name__` berilgan "modul" ichidan ko'tarib, ushlab qaytaradi —
    traceback'da o'sha modul kadri bo'ladi (asyncpg/sqlalchemy'ni taqlid)."""
    scope = {"__name__": module_name, "exc": exc}
    exec("def raiser():\n    raise exc\n", scope)  # noqa: S102 — sinov uchun
    try:
        scope["raiser"]()
    except BaseException as caught:  # noqa: BLE001
        return caught
    raise AssertionError("ko'tarilmadi")


class TestIsConnectionLost:
    def test_sqlalchemy_invalidated_connection(self):
        """Foydalanuvchi ko'rgan holat: so'rov o'rtasida ulanish yopildi."""
        exc = DBAPIError("SELECT 1", {}, Exception("connection is closed"), connection_invalidated=True)
        assert is_connection_lost(exc) is True

    def test_sqlalchemy_error_with_live_connection_is_a_program_error(self):
        exc = DBAPIError("SELECT 1", {}, Exception("syntax error"), connection_invalidated=False)
        assert is_connection_lost(exc) is False

    def test_raw_asyncpg_closed_connection(self):
        assert is_connection_lost(pg.InterfaceError("connection is closed")) is True
        assert is_connection_lost(
            pg.ConnectionDoesNotExistError("connection was closed in the middle of operation")
        ) is True

    def test_asyncpg_interface_error_about_concurrency_is_not_a_disconnect(self):
        exc = pg.InterfaceError("cannot perform operation: another operation is in progress")
        assert is_connection_lost(exc) is False

    def test_server_restarting_or_full(self):
        """Ulanishda server "recovery mode" / o'chirilmoqda / joy yo'q desa."""
        assert is_connection_lost(pg.CannotConnectNowError("the database system is in recovery mode")) is True
        assert is_connection_lost(pg.AdminShutdownError("terminating connection")) is True
        assert is_connection_lost(pg.CrashShutdownError("crash")) is True
        assert is_connection_lost(pg.TooManyConnectionsError("sorry, too many clients already")) is True

    def test_statement_timeout_and_constraint_errors_stay_program_errors(self):
        assert is_connection_lost(pg.QueryCanceledError("canceling statement due to statement timeout")) is False
        assert is_connection_lost(pg.UniqueViolationError("duplicate key")) is False

    def test_wrapped_chain_is_inspected(self):
        """SQLAlchemy o'ramining ichidagi asyncpg sababi ham ko'riladi."""
        inner = pg.CannotConnectNowError("the database system is starting up")
        outer = RuntimeError("wrapped")
        outer.__cause__ = inner
        assert is_connection_lost(outer) is True

    def test_os_error_from_the_db_layer(self):
        """Server umuman javob bermasa asyncpg xom OSError ko'taradi."""
        caught = _raise_from_module("sqlalchemy.pool.base", ConnectionRefusedError("refused"))
        assert is_connection_lost(caught) is True
        caught = _raise_from_module("asyncpg.connect_utils", asyncio.TimeoutError())
        assert is_connection_lost(caught) is True

    def test_os_error_from_elsewhere_is_not_blamed_on_the_db(self):
        """SMS yoki MinIO tarmoq xatosi bazaga yozilmaydi."""
        caught = _raise_from_module("app.application.services.sms_service", ConnectionRefusedError("sms"))
        assert is_connection_lost(caught) is False
        assert is_connection_lost(ValueError("x")) is False

    def test_cyclic_cause_chain_terminates(self):
        a = RuntimeError("a")
        b = RuntimeError("b")
        a.__cause__ = b
        b.__cause__ = a
        assert is_connection_lost(a) is False


class TestPayload:
    def test_error_is_503_with_the_code(self):
        err = DatabaseUnavailableError()
        assert err.status_code == 503
        assert err.error_code == DB_UNAVAILABLE_CODE
        assert "qayta urinib" in err.detail
        assert db_unavailable_payload()["error_code"] == DB_UNAVAILABLE_CODE

    def test_short_error_drops_sql_and_extra_lines(self):
        exc = DBAPIError(
            "SELECT hotels.name FROM hotels", {}, Exception("connection is closed"),
        )
        text = short_error(exc)
        assert "connection is closed" in text
        assert "SELECT hotels" not in text
        assert "\n" not in text
        assert short_error(RuntimeError("")) == "RuntimeError"

    def test_short_error_strips_wrapper_prefixes(self):
        """/health'da sabab ko'rinsin, o'ram sinflari emas."""
        raw = (
            "(sqlalchemy.dialects.postgresql.asyncpg.Error) "
            "<class 'asyncpg.exceptions.ConnectionDoesNotExistError'>: "
            "connection was closed in the middle of operation"
        )
        assert short_error(RuntimeError(raw)) == "connection was closed in the middle of operation"


class TestEngineOptions:
    def test_defaults_keep_dead_connections_out_of_requests(self):
        opts = database.engine_options()
        assert opts["pool_pre_ping"] is True
        assert opts["pool_use_lifo"] is True
        assert opts["connect_args"]["timeout"] == 10
        assert opts["connect_args"]["server_settings"]["application_name"]
        assert opts["pool_size"] > 0 and opts["pool_timeout"] > 0

    def test_options_are_accepted_by_the_asyncpg_engine(self):
        """Engine ulanmasdan quriladi — sozlamalar pool'ga yetib boradi."""
        engine = create_async_engine(
            "postgresql+asyncpg://u:p@127.0.0.1:1/x", **database.engine_options()
        )
        try:
            assert engine.pool._pre_ping is True
            assert engine.pool._pool.use_lifo is True
            assert engine.pool._recycle == database.settings.DB_POOL_RECYCLE
        finally:
            asyncio.run(engine.dispose())


# --------------------------------------------------------------- get_db ----


class _Session:
    def __init__(self, commit_error: BaseException | None = None):
        self.commit_error = commit_error
        self.rolled_back = 0

    async def commit(self):
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.rolled_back += 1


def _factory(session: _Session):
    @asynccontextmanager
    async def _open():
        yield session

    return _open


class TestGetDb:
    def _run(self, session: _Session, endpoint_error: BaseException | None, monkeypatch):
        monkeypatch.setattr(database, "_get_session_factory", lambda: _factory(session))

        async def flow():
            gen = database.get_db()
            got = await gen.__anext__()
            assert got is session
            if endpoint_error is None:
                await gen.__anext__()  # commit
            else:
                await gen.athrow(endpoint_error)

        return asyncio.run(flow())

    def test_disconnect_in_the_endpoint_becomes_503(self, monkeypatch):
        session = _Session()
        with pytest.raises(DatabaseUnavailableError):
            self._run(session, pg.InterfaceError("connection is closed"), monkeypatch)
        assert session.rolled_back == 1

    def test_disconnect_at_commit_becomes_503(self, monkeypatch):
        session = _Session(commit_error=pg.ConnectionDoesNotExistError("closed in the middle of operation"))
        with pytest.raises(DatabaseUnavailableError):
            self._run(session, None, monkeypatch)

    def test_program_errors_pass_through_unchanged(self, monkeypatch):
        session = _Session()
        with pytest.raises(ValueError):
            self._run(session, ValueError("bug"), monkeypatch)
        assert session.rolled_back == 1

    def test_rollback_failure_does_not_hide_the_cause(self, monkeypatch):
        class _Broken(_Session):
            async def rollback(self):
                raise pg.InterfaceError("connection is closed")

        with pytest.raises(DatabaseUnavailableError):
            self._run(_Broken(), pg.InterfaceError("connection is closed"), monkeypatch)

    def test_happy_path_commits(self, monkeypatch):
        session = _Session()
        with pytest.raises(StopAsyncIteration):
            self._run(session, None, monkeypatch)
        assert session.rolled_back == 0
