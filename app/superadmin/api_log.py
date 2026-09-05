"""So'rovlar jurnali — xotiradagi halqa bufer (oxirgi 500 ta so'rov).

Panel egasi backendga qanday so'rovlar kelayotganini (url, tana, javob,
holat, davomiylik) jonli ko'rishi uchun. Qoidalar:

* **Performansega ta'sir deyarli nol.** Yozish — `deque(maxlen=500)` ga
  O(1) qo'shish. So'rov tanasi faqat KICHIK JSON bo'lgandagina o'qiladi:
  fayl yuklash (multipart), oqimlar va katta tanalar o'tkazib yuboriladi —
  ular xotiraga ko'chirilmaydi. Javob tanasi ham xuddi shunday.
* **Hech narsa saqlanmaydi.** Jurnal faqat xotirada — diskka ham, bazaga
  ham yozilmaydi; server qayta ko'tarilsa bo'shaydi. 500 tadan eskisi
  o'z-o'zidan chiqib ketadi.
* **Sirlar niqoblanadi.** Parol, token, API kalit kabi maydonlarning
  qiymatlari jurnalga `***` bo'lib tushadi.
* Jurnalni ko'rish endpointlarining o'zi yozilmaydi — panel ochiq turganda
  jurnal o'z so'rovlari bilan to'lib qolmasin.
"""
from __future__ import annotations

import itertools
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

#: Jurnal sig'imi — undan ortig'i kerak emas (egasining talabi).
MAX_ENTRIES = 500

#: Ko'rsatiladigan tana matni shu uzunlikda qirqiladi (belgi).
BODY_CAP = 4000

#: Bundan katta tana umuman o'qilmaydi — xotira va tezlik uchun.
CAPTURE_MAX_BYTES = 64 * 1024

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
_seq = itertools.count(1)
_captured_total = 0


def redact(text: str) -> str:
    return _SENSITIVE.sub(r"\1***\2", text)


def _clip(text: str) -> str:
    if len(text) > BODY_CAP:
        return text[:BODY_CAP] + f"… (jami {len(text)} belgi)"
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

    async def _replay() -> dict:
        return {"type": "http.request", "body": raw, "more_body": False}

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
) -> None:
    global _captured_total
    _captured_total += 1
    _entries.append(
        {
            "id": next(_seq),
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "ip": ip,
            "request_body": request_body,
            "response_body": response_body,
        }
    )


async def middleware(request: Request, call_next):
    """HTTP middleware — `app.middleware("http")` bilan ulanadi."""
    if not _should_capture(request.method, request.url.path):
        return await call_next(request)

    path = request.url.path + (
        f"?{request.url.query}" if request.url.query else ""
    )
    ip = request.client.host if request.client else None

    try:
        request_body = await _capture_request_body(request)
    except Exception:
        # Jurnal hech qachon so'rovni yiqitmasin
        request_body = "<jurnal tanani o'qiy olmadi>"

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _record(
            method=request.method,
            path=path,
            status=500,
            duration_ms=(time.perf_counter() - started) * 1000,
            ip=ip,
            request_body=request_body,
            response_body="<ishlov berilmagan xato>",
        )
        raise

    duration_ms = (time.perf_counter() - started) * 1000
    try:
        response_body, response = await _capture_response_body(response)
    except Exception:
        logger.debug("Javob tanasi jurnalga olinmadi", exc_info=True)
        response_body = "<javob o'qilmadi>"

    _record(
        method=request.method,
        path=path,
        status=response.status_code,
        duration_ms=duration_ms,
        ip=ip,
        request_body=request_body,
        response_body=response_body,
    )
    return response


def snapshot(
    limit: int = MAX_ENTRIES,
    method: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict:
    """Jurnal nusxasi — eng yangi birinchi, ixtiyoriy filtrlar bilan.

    `status`: "2xx".."5xx" (sinf) yoki aniq kod ("404").
    Avval nusxa olinadi: middleware yozayotgan paytda iteratsiya
    yiqilmasligi uchun.
    """
    rows = list(_entries)
    rows.reverse()

    if method:
        m = method.upper()
        rows = [r for r in rows if r["method"] == m]
    if status:
        s = status.strip().lower()
        if len(s) == 3 and s.endswith("xx") and s[0].isdigit():
            low = int(s[0]) * 100
            rows = [r for r in rows if low <= r["status"] < low + 100]
        elif s.isdigit():
            rows = [r for r in rows if r["status"] == int(s)]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r["path"].lower()]

    return {
        "items": rows[:limit],
        "captured_total": _captured_total,
        "max_entries": MAX_ENTRIES,
    }


def clear() -> None:
    _entries.clear()
