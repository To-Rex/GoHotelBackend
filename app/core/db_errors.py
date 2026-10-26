"""Ma'lumotlar bazasi bilan aloqa uzilganini aniqlash.

Baza serveri qayta ishga tushsa ("the database system is in recovery mode"),
tarmoq uzilsa yoki pool'dagi ulanishni server yopib qo'ysa, SQLAlchemy va
asyncpg turli sinfdagi istisnolar ko'taradi:

* `sqlalchemy.exc.DBAPIError` — `connection_invalidated=True` bilan
  (so'rov o'rtasida ulanish yopildi: "connection is closed");
* xom `asyncpg` xatolari — ulanish paytida SQLAlchemy ularni o'ramaydi:
  sqlstate 08xxx (connection exception), 57P01/57P02/57P03 (server
  o'chirilmoqda / quladi / hali tayyor emas), 53300 (too many connections),
  `InterfaceError("connection is closed")`;
* `OSError` / `TimeoutError` — server umuman javob bermasa (connection
  refused, timeout) — asyncpg ulanish yo'lidan chiqadi.

Hammasi bir narsani anglatadi: so'rov bajarilmadi, lekin bu dastur xatosi
emas — biroz kutib qayta urinish kerak. Shu sababli ular bitta
503 `DB_UNAVAILABLE` javobiga yig'iladi. Ilgari 500 `INTERNAL_ERROR` va
"connection is closed" matni chiqardi: klient uni oddiy dastur xatosidan
ajrata olmasdi, debug rejimda esa SQL matni foydalanuvchiga ko'rinardi.
"""
from __future__ import annotations

import asyncio
import re
import traceback
from typing import Iterator

from sqlalchemy.exc import DBAPIError

from app.core.exceptions import AppException

DB_UNAVAILABLE_CODE = "DB_UNAVAILABLE"
DB_UNAVAILABLE_DETAIL = (
    "Ma'lumotlar bazasi bilan aloqa uzildi — so'rov bajarilmadi. "
    "Bir necha soniyadan keyin qayta urinib ko'ring."
)

#: PostgreSQL sqlstate sinflari: 08 — connection exception; 57P — operator
#: intervention (admin shutdown, crash shutdown, cannot connect now);
#: 53300 — too many connections. 57014 (statement timeout) BU YERGA KIRMAYDI:
#: u sekin so'rov, ulanish emas.
_SQLSTATE_PREFIXES = ("08", "57P", "53300")

#: asyncpg xabarlari — ulanish yopilganini bildiradi (sqlstate'siz
#: InterfaceError uchun). "timeout" ATAYLAB yo'q: statement timeout ham
#: shu so'zni ishlatadi.
_CLOSED_MARKERS = (
    "connection is closed",
    "connection was closed",
    "closed in the middle of operation",
    "server closed the connection",
    "connection reset",
    "connection refused",
    "connect call failed",
)

#: Ulanish yo'lidagi modullar — OSError/TimeoutError shu yerdan chiqsa
#: bazaga tegishli (SMS yoki MinIO'ning OSError'i emas).
_DB_MODULES = ("asyncpg", "sqlalchemy")


class DatabaseUnavailableError(AppException):
    """Baza hozircha javob bermaydi — 503, klient qayta urinadi."""

    def __init__(
        self, detail: str = DB_UNAVAILABLE_DETAIL, error_code: str = DB_UNAVAILABLE_CODE
    ) -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 503


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """Istisno va uning sabablari (`__cause__` / `__context__` / SQLAlchemy
    `orig`), halqasiz. SQLAlchemy `StatementError` asl DBAPI xatoni `orig`
    atributida saqlaydi, sabab zanjiriga qo'shmaydi — shuning uchun u ham
    alohida ko'riladi."""
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        nxt = current.__cause__ or current.__context__
        orig = getattr(current, "orig", None)
        for item in (nxt, orig):
            if isinstance(item, BaseException) and item is not current:
                stack.append(item)


def _raised_in_db_layer(exc: BaseException) -> bool:
    for frame, _lineno in traceback.walk_tb(exc.__traceback__):
        module = frame.f_globals.get("__name__", "") or ""
        if module.startswith(_DB_MODULES):
            return True
    return False


def is_connection_lost(exc: BaseException) -> bool:
    """Istisno bazaga ulanish yo'qolganini bildiradimi.

    Dastur xatolari (IntegrityError, ProgrammingError, SMS/MinIO tarmoq
    xatolari) uchun False — ular avvalgidek 500 bo'lib qoladi va logda
    to'liq traceback bilan ko'rinadi. Vaqtinchalik ziddiyatlar (deadlock,
    statement timeout va h.k.) uchun ham False: ular ulanish emas,
    `is_db_conflict` alohida ko'radi.
    """
    for e in _chain(exc):
        if isinstance(e, DBAPIError) and e.connection_invalidated:
            return True
        sqlstate = getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)
        if isinstance(sqlstate, str) and sqlstate.startswith(_SQLSTATE_PREFIXES):
            return True
        module = type(e).__module__ or ""
        if module.startswith("asyncpg"):
            text = str(e).lower()
            if any(marker in text for marker in _CLOSED_MARKERS):
                return True
        if isinstance(e, (OSError, asyncio.TimeoutError, TimeoutError)) and _raised_in_db_layer(e):
            return True
    return False


#: Vaqtinchalik ziddiyatlar — so'rovning o'zi yaroqli, lekin boshqa so'rov
#: bilan to'qnashdi yoki baza o'sha lahzada o'ta band edi. Ma'lumot O'ZGARMADI
#: va qayta urinish odatda muvaffaqiyatli o'tadi: 40P01 (deadlock), 40001
#: (serialization failure), 55P03 (lock not available), 57014 (statement
#: timeout — sekin so'rov, ko'pincha yuklama ostidagi vaqtinchalik holat).
_CONFLICT_SQLSTATES = ("40P01", "40001", "55P03", "57014")

DB_CONFLICT_CODE = "DB_CONFLICT"
DB_CONFLICT_DETAIL = (
    "Bir vaqtda bajarilgan so'rovlar to'qnashdi — o'zgarishlar saqlanmadi. "
    "Iltimos, qaytadan urinib ko'ring."
)


def is_db_conflict(exc: BaseException) -> bool:
    """Istisno vaqtinchalik baza ziddiyatimi (deadlock/ketma-ketlik/blokirovka).

    Dastur xatosi EMAS: ma'lumot o'zgarmadi va qayta urinish odatda o'tadi.
    Bunday xatolar 500 "Internal server error" o'rniga 409 `DB_CONFLICT`
    bo'lib qaytadi — foydalanuvchi tushunarli "qaytadan urinib ko'ring"
    xabarini oladi, logda esa qisqa ogohlantirish qoladi.
    """
    for e in _chain(exc):
        sqlstate = getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)
        if isinstance(sqlstate, str) and sqlstate in _CONFLICT_SQLSTATES:
            return True
    return False


def db_conflict_payload() -> dict:
    return {"detail": DB_CONFLICT_DETAIL, "error_code": DB_CONFLICT_CODE}


#: SQLAlchemy o'rami qo'shadigan prefikslar: "(sqlalchemy...Error) " va
#: "<class 'asyncpg...Error'>: " — sabab ulardan keyin keladi
_WRAPPER_PREFIXES = (
    re.compile(r"^\([\w.]+\)\s*"),
    re.compile(r"^<class '[\w.]+'>:\s*"),
)


def short_error(exc: BaseException, limit: int = 200) -> str:
    """Log va /health uchun bir qatorli qisqa matn (SQL matnisiz)."""
    text = str(exc).strip() or type(exc).__name__
    first = text.splitlines()[0]
    # SQLAlchemy "[SQL: ...]" qismini ko'rsatmaymiz — sabab birinchi qismda
    first = first.split("[SQL:", 1)[0].strip()
    for pattern in _WRAPPER_PREFIXES:
        first = pattern.sub("", first, count=1)
    return (first or type(exc).__name__)[:limit]


def db_unavailable_payload() -> dict:
    return {"detail": DB_UNAVAILABLE_DETAIL, "error_code": DB_UNAVAILABLE_CODE}
