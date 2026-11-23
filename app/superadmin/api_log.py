"""So'rovlar jurnali — xotiradagi halqa bufer (oxirgi 500 ta so'rov) va
xatolarning bazada saqlanishi (4xx/5xx, 3 kun).

Panel egasi backendga qanday so'rovlar kelayotganini (url, tana, javob,
holat, davomiylik, mehmonxona/foydalanuvchi) jonli ko'rishi uchun. Qoidalar:

* **Performansega ta'sir deyarli nol.** Yozish — `deque(maxlen=500)` ga
  O(1) qo'shish. So'rov tanasi faqat KICHIK JSON bo'lgandagina o'qiladi:
  fayl yuklash (multipart), oqimlar va katta tanalar o'tkazib yuboriladi —
  ular xotiraga ko'chirilmaydi. Javob tanasi ham xuddi shunday.
* **Jonli jurnal xotirada.** Diskka yozilmaydi; server qayta ko'tarilsa
  (har deploy'da) bo'shaydi, 500 tadan eskisi o'z-o'zidan chiqib ketadi.
* **Xatolar bazada qoladi.** 4xx/5xx yozuvlar `api_error_logs` jadvaliga
  ham yoziladi (so'rovdan KEYIN, fonda — mijoz kutmaydi) va
  `API_ERROR_LOG_RETENTION_DAYS` kun (standart 3) turadi, keyin
  o'z-o'zidan o'chadi. "Bron qilishda server xatosi chiqdi" degan muammo
  ertasiga ham, deploy'dan keyin ham o'rganiladigan bo'lsin. Ishlov
  berilmagan xatoning to'liq traceback'i ham yoziladi (`error`).
  Baza yotgan bo'lsa yozilmaydi — jurnal hech qachon so'rovni yiqitmaydi.
* **Sirlar niqoblanadi.** Parol, token, API kalit kabi maydonlarning
  qiymatlari jurnalga `***` bo'lib tushadi.
* **Kontekst.** Autentifikatsiya qilingan so'rovda mehmonxona, filial,
  foydalanuvchi va uning turi ham yoziladi — panel ular bo'yicha filtrlaydi.
* Jurnalni ko'rish endpointlarining o'zi yozilmaydi — panel ochiq turganda
  jurnal o'z so'rovlari bilan to'lib qolmasin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import traceback
import uuid
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from app.core.db_errors import (
    db_conflict_payload,
    db_unavailable_payload,
    is_connection_lost,
    is_db_conflict,
    short_error,
)

logger = logging.getLogger(__name__)

#: Jonli jurnal sig'imi — undan ortig'i kerak emas (egasining talabi).
MAX_ENTRIES = 500

#: Ko'rsatiladigan tana matni shu uzunlikda qirqiladi (belgi).
BODY_CAP = 4000

#: Traceback matni shu uzunlikda qirqiladi (belgi).
ERROR_CAP = 12000

#: Bundan katta tana umuman o'qilmaydi — xotira va tezlik uchun.
CAPTURE_MAX_BYTES = 64 * 1024

#: Shu holatdan boshlab yozuv "xato" — bazada saqlanadi.
ERROR_STATUS_FROM = 400

#: Bazaga yozilishini kutayotgan yozuvlar chegarasi — baza sekin bo'lsa
#: navbat cheksiz o'smasin (ortig'i tashlab yuboriladi, jonli jurnalda qoladi).
PERSIST_MAX_PENDING = 200

#: Eski xatolar har shuncha soniyada bir marta o'chiriladi (yozish paytida).
PURGE_INTERVAL_SECONDS = 600

#: Bitta so'rovda bazadan o'qiladigan saqlangan xatolar chegarasi.
STORED_MAX_ROWS = 2000

#: O'zi-o'zini yozmasin: jurnal endpointlari va hujjat sahifalari.
_SKIP_PREFIXES = (
    "/api/v1/superadmin/api-logs",
    "/docs",
    "/redoc",
    "/openapi.json",
)

#: Sir saqlanadigan JSON maydonlari — qiymati niqoblanadi.
_SENSITIVE = re.compile(
    r'("(?:password|current_password|new_password|old_password|token|'
    r'access_token|refresh_token|api_key|secret|authorization|fcm_token)"'
    r'\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)

_entries: deque[dict] = deque(maxlen=MAX_ENTRIES)
_captured_total = 0
_pending: set[asyncio.Task] = set()
_last_purge = 0.0

#: Bazaga yozish — testlarda o'chirib qo'yiladi.
persist_enabled = True


def retention_days() -> int:
    from app.core.config import settings  # aylanma importga yo'l qo'ymaslik uchun

    return max(0, int(settings.API_ERROR_LOG_RETENTION_DAYS))


def redact(text: str) -> str:
    return _SENSITIVE.sub(r"\1***\2", text)


def _clip(text: str, cap: int = BODY_CAP) -> str:
    if len(text) > cap:
        return text[:cap] + f"… (jami {len(text)} belgi)"
    return text


def _should_capture(method: str, path: str) -> bool:
    if method == "OPTIONS":
        return False
    return not any(path.startswith(p) for p in _SKIP_PREFIXES)


async def _capture_request_body(request: Request) -> str | None:
    """So'rov tanasi — faqat kichik JSON o'qiladi, qolgani belgilanadi.

    O'qilgan tana endpointga QAYTA yetib borishi uchun oqim tiklanadi —
    aks holda endpoint bo'sh tana olib qolardi.
    """
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        clen = int(request.headers.get("content-length") or 0)
    except ValueError:
        clen = 0

    if clen == 0 and "content-type" not in request.headers:
        return None
    if not ctype.startswith("application/json"):
        kind = ctype or "noma'lum tur"
        return f"<{kind}; {clen} bayt — tana yozilmaydi>"
    if clen > CAPTURE_MAX_BYTES:
        return f"<JSON {clen} bayt — jurnal uchun juda katta>"

    try:
        raw = await request.body()
    except Exception:
        return "<tana o'qilmadi>"

    # `app.middleware("http")` (BaseHTTPMiddleware) o'qilgan tanani o'zi
    # keshlaydi va endpointga qayta beradi (`wrapped_receive`). U holda
    # kanalni almashtirmaymiz: almashtirilsa, oqimli javob (SSE) mijoz
    # uzilishini kuzatganda yana "http.request" olib, xato bilan yiqilardi.
    if not hasattr(request, "wrapped_receive"):
        original_receive = request._receive  # noqa: SLF001
        replayed = False

        async def _replay() -> dict:
            # Tana BIR MARTA; keyin — asl kanal ("http.disconnect")
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await original_receive()

        request._receive = _replay  # noqa: SLF001 — oqimni tiklashning yagona yo'li

    if not raw:
        return None
    return _clip(redact(raw.decode("utf-8", errors="replace")))


async def _capture_response_body(response: Response) -> tuple[str | None, Response]:
    """Javob tanasi — faqat kichik JSON; oqimli javoblar tegilmaydi.

    JSON javob iteratordan yig'ib olinadi va xuddi shu tarkib bilan yangi
    javob qaytariladi — mijoz hech qanday farq sezmaydi.
    """
    ctype = (response.headers.get("content-type") or "").lower()
    if not ctype.startswith("application/json"):
        return (f"<{ctype}>" if ctype else None), response

    clen_header = response.headers.get("content-length")
    try:
        if clen_header and int(clen_header) > CAPTURE_MAX_BYTES:
            return f"<JSON {clen_header} bayt — jurnal uchun juda katta>", response
    except ValueError:
        pass

    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        raw = getattr(response, "body", b"") or b""
        return _clip(redact(raw.decode("utf-8", errors="replace"))), response

    chunks: list[bytes] = []
    async for chunk in body_iterator:
        chunks.append(chunk)
    raw = b"".join(chunks)

    rebuilt = Response(
        content=raw,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )
    rebuilt.background = response.background
    return _clip(redact(raw.decode("utf-8", errors="replace"))), rebuilt


def _record(
    *,
    method: str,
    path: str,
    status: int,
    duration_ms: float,
    ip: str | None,
    request_body: str | None,
    response_body: str | None,
    hotel_id: str | None = None,
    branch_id: str | None = None,
    user_id: str | None = None,
    user_type: str | None = None,
    user_agent: str | None = None,
    error: str | None = None,
) -> dict:
    """Jonli jurnalga yozuv qo'shadi va uni qaytaradi (bazaga yozish uchun)."""
    global _captured_total
    _captured_total += 1
    entry = {
        # uuid — xotiradagi va bazadagi nusxa bir xil kalit bilan, panel
        # ikkalasini birlashtirganda takrorlanmaydi
        "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 1),
        "ip": ip,
        "request_body": request_body,
        "response_body": response_body,
        "error": error,
        "hotel_id": hotel_id,
        "branch_id": branch_id,
        "user_id": user_id,
        "user_type": user_type,
        "user_agent": (user_agent or "")[:300] or None,
        # bazadan o'qilgan yozuvda True — panel belgilab ko'rsatadi
        "stored": False,
    }
    _entries.append(entry)
    return entry


def _context(request: Request) -> dict:
    """Auth dependency qo'ygan kontekst (mehmonxona/filial/foydalanuvchi)."""
    ctx = getattr(request.state, "api_log_context", None) or {}
    return {
        "hotel_id": ctx.get("hotel_id"),
        "branch_id": ctx.get("branch_id"),
        "user_id": ctx.get("user_id"),
        "user_type": ctx.get("user_type"),
    }


def _failure(exc: BaseException) -> tuple[int, dict]:
    """Ishlov berilmagan istisno mijozga qanday javob bo'lib boradi —
    main.py dagi umumiy ishlovchi bilan bir xil qoida (503 / 409 / 500)."""
    if is_connection_lost(exc):
        return 503, db_unavailable_payload()
    if is_db_conflict(exc):
        return 409, db_conflict_payload()
    return 500, {"detail": "Internal server error", "error_code": "INTERNAL_ERROR"}


async def middleware(request: Request, call_next):
    """HTTP middleware — `app.middleware("http")` bilan ulanadi."""
    if not _should_capture(request.method, request.url.path):
        return await call_next(request)

    path = request.url.path + (
        f"?{request.url.query}" if request.url.query else ""
    )
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    try:
        request_body = await _capture_request_body(request)
    except Exception:
        # Jurnal hech qachon so'rovni yiqitmasin
        request_body = "<jurnal tanani o'qiy olmadi>"

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # Ishlov berilmagan xato: mijoz qaysi javobni olishi (503/409/500)
        # va to'liq traceback — muammo keyin ham aniq o'rganilsin
        status, payload = _failure(exc)
        entry = _record(
            method=request.method,
            path=path,
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
            ip=ip,
            request_body=request_body,
            response_body=json.dumps(payload, ensure_ascii=False),
            error=_clip(
                f"{type(exc).__name__}: {short_error(exc)}\n\n{traceback.format_exc()}",
                ERROR_CAP,
            ),
            user_agent=user_agent,
            **_context(request),
        )
        _schedule_persist(entry)
        raise

    duration_ms = (time.perf_counter() - started) * 1000
    try:
        response_body, response = await _capture_response_body(response)
    except Exception:
        logger.debug("Javob tanasi jurnalga olinmadi", exc_info=True)
        response_body = "<javob o'qilmadi>"

    entry = _record(
        method=request.method,
        path=path,
        status=response.status_code,
        duration_ms=duration_ms,
        ip=ip,
        request_body=request_body,
        response_body=response_body,
        user_agent=user_agent,
        **_context(request),
    )
    _schedule_persist(entry)
    return response


# ------------------------------------------------------------ bazada saqlash


def _should_persist(entry: dict) -> bool:
    return (
        persist_enabled
        and entry["status"] >= ERROR_STATUS_FROM
        and retention_days() > 0
    )


def _schedule_persist(entry: dict) -> None:
    """Xato yozuvini fonda bazaga yozishni rejalashtiradi — so'rov kutmaydi."""
    if not _should_persist(entry):
        return
    if len(_pending) >= PERSIST_MAX_PENDING:
        logger.debug("Xato jurnali: navbat to'la, yozuv faqat xotirada qoldi")
        return
    try:
        task = asyncio.get_running_loop().create_task(_persist_quietly(entry))
    except RuntimeError:
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _persist_quietly(entry: dict) -> None:
    try:
        from app.core.database import _get_session_factory

        await persist(entry, _get_session_factory())
    except Exception as exc:  # jurnal hech narsani yiqitmaydi
        logger.debug("Xato jurnali bazaga yozilmadi: %s", short_error(exc))


def _cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=retention_days())


async def purge(session: AsyncSession) -> None:
    """Saqlash muddatidan o'tgan xatolarni o'chiradi (commit chaqiruvchida)."""
    from app.infrastructure.database.models.api_error_log import ApiErrorLog

    await session.execute(delete(ApiErrorLog).where(ApiErrorLog.ts < _cutoff()))


async def persist(entry: dict, session_factory: Any) -> None:
    """Bitta xato yozuvini bazaga yozadi; vaqti-vaqti bilan eskilarini o'chiradi."""
    from app.infrastructure.database.models.api_error_log import ApiErrorLog

    global _last_purge
    async with session_factory() as session:
        session.add(
            ApiErrorLog(
                id=entry["id"],
                ts=datetime.fromisoformat(entry["ts"]),
                method=entry["method"],
                path=entry["path"],
                status=int(entry["status"]),
                duration_ms=float(entry.get("duration_ms") or 0),
                ip=entry.get("ip"),
                request_body=entry.get("request_body"),
                response_body=entry.get("response_body"),
                error=entry.get("error"),
                hotel_id=entry.get("hotel_id"),
                branch_id=entry.get("branch_id"),
                user_id=entry.get("user_id"),
                user_type=entry.get("user_type"),
                user_agent=entry.get("user_agent"),
            )
        )
        now = time.monotonic()
        if now - _last_purge > PURGE_INTERVAL_SECONDS:
            _last_purge = now
            await purge(session)
        await session.commit()


# ---------------------------------------------------------------- filtrlar

#: Bitta holat ifodasidagi bo'laklar chegarasi (URL cheksiz o'smasin).
STATUS_TOKENS_MAX = 40

_STATUS_RANGE = re.compile(r"(\d{3})\s*-\s*(\d{3})")


def _status_token(token: str) -> tuple[int, int] | None:
    """Bitta bo'lak → [low, high) oralig'i: "404", "4xx" (sinf), "500-504"."""
    s = token.strip().lower()
    if len(s) == 3 and s.endswith("xx") and s[0] in "12345":
        low = int(s[0]) * 100
        return low, low + 100
    if len(s) == 3 and s.isdigit():
        code = int(s)
        return code, code + 1
    m = _STATUS_RANGE.fullmatch(s)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return a, b + 1
    return None


@dataclass(frozen=True)
class StatusFilter:
    """Holat filtri: `include` dan biriga tushadi (bo'sh — hammasi) VA
    `exclude` ning hech biriga tushmaydi."""

    include: tuple[tuple[int, int], ...] = ()
    exclude: tuple[tuple[int, int], ...] = ()

    def matches(self, code: int) -> bool:
        if self.include and not any(lo <= code < hi for lo, hi in self.include):
            return False
        return not any(lo <= code < hi for lo, hi in self.exclude)

    def clause(self, column: Any) -> Any:
        conds = []
        if self.include:
            conds.append(or_(*(and_(column >= lo, column < hi) for lo, hi in self.include)))
        conds.extend(not_(and_(column >= lo, column < hi)) for lo, hi in self.exclude)
        return and_(*conds)


def parse_status(expr: str | None) -> StatusFilter | None:
    """Holat ifodasi — vergul bilan ajratilgan bo'laklar:

    "404" (aniq kod), "4xx" (sinf), "500-504" (oraliq); oldida "!" bo'lsa —
    chiqarib tashlanadi. Masalan "400,401,422" — shu uchtasi; "4xx,!401" —
    401 siz mijoz xatolari; "!200,!304" — 200 va 304 dan boshqa hammasi.
    Tushunilmagan bo'lak e'tiborsiz qoladi; hech narsa tushunilmasa — filtr yo'q.
    """
    if not expr:
        return None
    include: list[tuple[int, int]] = []
    exclude: list[tuple[int, int]] = []
    for raw in expr.split(",")[:STATUS_TOKENS_MAX]:
        token = raw.strip()
        negate = token.startswith(("!", "-")) and not _STATUS_RANGE.fullmatch(token)
        bounds = _status_token(token[1:] if negate else token)
        if bounds:
            (exclude if negate else include).append(bounds)
    if not include and not exclude:
        return None
    return StatusFilter(tuple(include), tuple(exclude))


def parse_methods(expr: str | None) -> frozenset[str]:
    """"GET" yoki "GET,POST" → {"GET", "POST"}; bo'sh — hammasi."""
    if not expr:
        return frozenset()
    return frozenset(m.strip().upper() for m in expr.split(",") if m.strip())


def since_cutoff(minutes: int | None) -> datetime | None:
    """"Oxirgi N daqiqa" filtri uchun chegara (UTC)."""
    if not minutes or minutes <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _ts(entry: dict) -> datetime:
    ts = datetime.fromisoformat(entry["ts"])
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _matches_base(
    row: dict,
    methods: frozenset[str],
    q: str | None,
    hotel_id: str | None,
    since: datetime | None,
) -> bool:
    """Holatdan boshqa filtrlar (holat soni shu to'plam bo'yicha sanaladi)."""
    if methods and row["method"] not in methods:
        return False
    if q and q.lower() not in row["path"].lower():
        return False
    if hotel_id and row.get("hotel_id") != hotel_id:
        return False
    return not (since and _ts(row) < since)


def live_error_ids() -> list[str]:
    """Xotiradagi xato yozuvlari — bazadagi nusxasi sanoqda takrorlanmasin."""
    return [r["id"] for r in list(_entries) if r["status"] >= ERROR_STATUS_FROM]


def _row_to_entry(row: Any) -> dict:
    ts = row.ts
    if ts.tzinfo is None:  # SQLite (testlar) zonasiz qaytaradi — UTC
        ts = ts.replace(tzinfo=timezone.utc)
    return {
        "id": row.id,
        "ts": ts.isoformat(),
        "method": row.method,
        "path": row.path,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "ip": row.ip,
        "request_body": row.request_body,
        "response_body": row.response_body,
        "error": row.error,
        "hotel_id": row.hotel_id,
        "branch_id": row.branch_id,
        "user_id": row.user_id,
        "user_type": row.user_type,
        "user_agent": row.user_agent,
        "stored": True,
    }


def _stored_base(
    stmt: Any,
    model: Any,
    *,
    method: str | None,
    q: str | None,
    hotel_id: str | None,
    since_minutes: int | None,
) -> Any:
    stmt = stmt.where(model.ts >= _cutoff())
    methods = parse_methods(method)
    if methods:
        stmt = stmt.where(model.method.in_(sorted(methods)))
    if q:
        stmt = stmt.where(model.path.ilike(f"%{q}%"))
    if hotel_id:
        stmt = stmt.where(model.hotel_id == hotel_id)
    since = since_cutoff(since_minutes)
    if since:
        stmt = stmt.where(model.ts >= since)
    return stmt


async def stored_errors(
    session: AsyncSession,
    *,
    method: str | None = None,
    status: str | None = None,
    q: str | None = None,
    hotel_id: str | None = None,
    since_minutes: int | None = None,
    limit: int = STORED_MAX_ROWS,
) -> list[dict]:
    """Bazadagi xatolar (saqlash muddati ichida) — jonli jurnal bilan bir xil
    filtrlar; eng yangisi birinchi. Holat filtri SQL'da — 2000 talik chegara
    tanlangan holatlar ichida qo'llanadi (ko'p 401 lar kerakli 422 ni siqib
    chiqarmaydi)."""
    from app.infrastructure.database.models.api_error_log import ApiErrorLog

    stmt = _stored_base(
        select(ApiErrorLog), ApiErrorLog,
        method=method, q=q, hotel_id=hotel_id, since_minutes=since_minutes,
    )
    sf = parse_status(status)
    if sf:
        stmt = stmt.where(sf.clause(ApiErrorLog.status))
    stmt = stmt.order_by(ApiErrorLog.ts.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_entry(r) for r in rows]


async def stored_status_counts(
    session: AsyncSession,
    *,
    method: str | None = None,
    q: str | None = None,
    hotel_id: str | None = None,
    since_minutes: int | None = None,
    exclude_ids: list[str] | None = None,
) -> dict[int, int]:
    """Bazadagi xatolar soni holat kodi bo'yicha (holat filtrisiz — panel
    har bir kod yonida nechta yozuv borligini ko'rsatadi). Xotirada ham
    turgan yozuvlar (`exclude_ids`) sanalmaydi — ular jonli qismda sanaladi."""
    from app.infrastructure.database.models.api_error_log import ApiErrorLog

    stmt = _stored_base(
        select(ApiErrorLog.status, func.count()), ApiErrorLog,
        method=method, q=q, hotel_id=hotel_id, since_minutes=since_minutes,
    )
    if exclude_ids:
        stmt = stmt.where(ApiErrorLog.id.notin_(exclude_ids))
    stmt = stmt.group_by(ApiErrorLog.status)
    return {int(code): int(n) for code, n in (await session.execute(stmt)).all()}


async def load_stored_errors(**filters: Any) -> list[dict]:
    """Panel uchun: o'z sessiyasi bilan o'qiydi; baza yotgan bo'lsa — bo'sh
    ro'yxat (jonli jurnal baribir ko'rinadi)."""
    if retention_days() <= 0:
        return []
    try:
        from app.core.database import _get_session_factory

        async with _get_session_factory()() as session:
            return await stored_errors(session, **filters)
    except Exception as exc:
        logger.debug("Saqlangan xatolar o'qilmadi: %s", short_error(exc))
        return []


async def load_stored_status_counts(**filters: Any) -> dict[int, int]:
    """`stored_status_counts` — o'z sessiyasi bilan; baza yotgan bo'lsa bo'sh."""
    if retention_days() <= 0:
        return {}
    try:
        from app.core.database import _get_session_factory

        async with _get_session_factory()() as session:
            return await stored_status_counts(session, **filters)
    except Exception as exc:
        logger.debug("Saqlangan xatolar soni o'qilmadi: %s", short_error(exc))
        return {}


# ------------------------------------------------------------------ nusxa


def snapshot(
    limit: int = MAX_ENTRIES,
    method: str | None = None,
    status: str | None = None,
    q: str | None = None,
    hotel_id: str | None = None,
    sort: str = "ts",
    order: str = "desc",
    extra: list[dict] | None = None,
    since_minutes: int | None = None,
    extra_counts: dict[int, int] | None = None,
) -> dict:
    """Jurnal nusxasi — filtrlar, saralash va eng yangi birinchi.

    `method`: "GET" yoki bir nechtasi ("GET,POST").
    `status`: holat ifodasi — `parse_status` (aniq kod, sinf, oraliq, "!").
    `since_minutes`: faqat oxirgi N daqiqa.
    `sort`: ts | duration_ms | status | method | path; `order`: asc | desc.
    `extra`: bazadan o'qilgan saqlangan xatolar (`stored_errors`) — jonli
    jurnalga qo'shiladi; xotirada hali turgan nusxasi takrorlanmaydi.
    `extra_counts`: bazadagi xatolar soni kod bo'yicha (`stored_status_counts`).

    `status_counts` — holatdan BOSHQA filtrlarga mos yozuvlar soni kod
    bo'yicha: panel kodlarni sonlari bilan ko'rsatadi, tanlov o'zgarsa ham
    qolgan kodlar yo'qolib qolmaydi. `matched_total` — chegara (`limit`)
    qo'llanishidan oldingi mos yozuvlar soni.
    Avval nusxa olinadi: middleware yozayotgan paytda iteratsiya
    yiqilmasligi uchun.
    """
    live = list(_entries)
    live.reverse()
    live_ids = {r["id"] for r in live}

    methods = parse_methods(method)
    since = since_cutoff(since_minutes)
    sf = parse_status(status)

    rows = [r for r in live if _matches_base(r, methods, q, hotel_id, since)]
    counts: Counter[int] = Counter(r["status"] for r in rows)
    if extra_counts is not None:
        for code, n in extra_counts.items():
            counts[int(code)] += int(n)
    elif extra:
        # Soni berilmagan — qo'lda berilgan saqlangan yozuvlardan sanaladi
        counts.update(
            r["status"] for r in extra
            if r["id"] not in live_ids and _matches_base(r, methods, q, hotel_id, since)
        )

    if sf:
        rows = [r for r in rows if sf.matches(r["status"])]
    if extra:
        rows.extend(
            r for r in extra
            if r["id"] not in live_ids
            and _matches_base(r, methods, q, hotel_id, since)
            and (sf is None or sf.matches(r["status"]))
        )

    sort_key = {
        "ts": "ts",
        "duration": "duration_ms",
        "duration_ms": "duration_ms",
        "status": "status",
        "method": "method",
        "path": "path",
    }.get(sort, "ts")
    reverse = (order or "desc").lower() != "asc"
    rows.sort(
        key=lambda r: (r.get(sort_key) is None, r.get(sort_key)), reverse=reverse
    )

    return {
        "items": rows[:limit],
        "matched_total": len(rows),
        "status_counts": {str(code): n for code, n in sorted(counts.items())},
        "captured_total": _captured_total,
        "max_entries": MAX_ENTRIES,
        "stored_errors": len(extra or []),
        "retention_days": retention_days(),
    }


def clear() -> None:
    """Jonli (xotiradagi) jurnalni tozalaydi — bazadagi xatolar qoladi."""
    _entries.clear()
