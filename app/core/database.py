from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings
from app.core.db_errors import DatabaseUnavailableError, is_connection_lost, short_error

logger = logging.getLogger(__name__)

Base = declarative_base()

_engine = None
_session_factory = None


def engine_options() -> dict[str, Any]:
    """`create_async_engine` sozlamalari — bir joyda, sinovdan o'tadi.

    * `pool_pre_ping` — pool'dan olinayotgan ulanish avval yengil "ping"
      bilan tekshiriladi; server tomonidan yopilgan ulanish (baza qayta
      ishga tushgan, tarmoq uzilgan, bo'sh turib uzilgan) jimgina yangisiga
      almashtiriladi. Ilgari bunday ulanish so'rovga berilib, so'rov
      "connection is closed" bilan yiqilardi — baza qisqa vaqt yotgandan
      keyin ham ko'p so'rovlar 500 qaytarardi.
    * `pool_use_lifo` — oxirgi qaytarilgan ulanish birinchi olinadi:
      ortiqchalari bo'sh turib `pool_recycle` bilan yangilanadi (SQLAlchemy
      tavsiyasi: server tomonidagi timeoutlar uchun pre_ping bilan birga).
    * `connect_args.timeout` — baza javob bermasa ulanish 60 s (asyncpg
      standarti) emas, `DB_CONNECT_TIMEOUT` s ichida yiqiladi.
    * `application_name` — pg_stat_activity'da bu ulanishlar ko'rinadi
      (bitta bazaga bir nechta xizmat ulanadi).
    """
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "pool_pre_ping": settings.DB_POOL_PRE_PING,
        "pool_use_lifo": True,
        "echo": settings.APP_DEBUG,
        "connect_args": {
            "timeout": settings.DB_CONNECT_TIMEOUT,
            "server_settings": {"application_name": settings.DB_APPLICATION_NAME},
        },
    }


def _get_engine():
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(str(settings.DATABASE_URL), **engine_options())
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _get_engine()
    return _session_factory


async def _safe_rollback(session: AsyncSession) -> None:
    # Ulanish allaqachon o'lgan bo'lsa rollback'ning o'zi ham yiqiladi —
    # asl xato yo'qolib qolmasin
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001
        logger.debug("Rollback bajarilmadi (ulanish yopilgan bo'lsa kerak)", exc_info=True)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Har so'rov uchun bitta sessiya: oxirida commit, xatoda rollback.

    Baza bilan aloqa uzilgani (server qayta ishga tushmoqda, tarmoq yo'q,
    ulanish yopilgan) 503 `DB_UNAVAILABLE` bo'lib chiqadi — 500 emas:
    klient buni "qayta urinib ko'ring" deb ko'rsatadi, logda esa bir qator
    qoladi, to'liq traceback emas (app/core/db_errors.py).
    """
    try:
        async with _get_session_factory()() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await _safe_rollback(session)
                raise
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        if is_connection_lost(exc):
            logger.warning("Ma'lumotlar bazasi bilan aloqa uzildi: %s", short_error(exc))
            raise DatabaseUnavailableError() from exc
        raise


async def check_database(timeout: float = 3.0) -> tuple[bool, str | None]:
    """Baza javob beradimi — /health va ishga tushishda.

    Hech qachon istisno ko'tarmaydi: (True, None) yoki (False, qisqa sabab).
    """
    async def _ping() -> None:
        async with _get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_ping(), timeout=timeout)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, short_error(exc)


async def init_db() -> None:
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
