"""E-mehmon CRM API bilan gaplashadigan imzoli HTTP mijoz.

So'rovlar FAQAT serverdan ketadi (server ↔ server): maxfiy kalit brauzerga
chiqmaydi. Har so'rov yangi `X-User-Request-Id` bilan imzolanadi
(`emehmon_payload.auth_headers`).

Yozuvchi so'rovlarga `Idempotency-Key` qo'yiladi: tarmoq uzilib javob
yo'qolsa, o'sha kalit va aynan o'sha tana bilan qayta yuborilganda E-mehmon
saqlab qo'ygan javobini qaytaradi (`Idempotent-Replay: 1`) — mehmon ikki
marta ro'yxatga olinmaydi.

Xatolar bitta turga keltiriladi — `EmehmonError` (kod + o'zbekcha matn):
tarmoq yetib bormasa `NETWORK_ERROR`, JSON bo'lmasa `BAD_RESPONSE`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from app.application.services import emehmon_payload as ep

logger = logging.getLogger(__name__)

#: Ulanish 6 s, javob 20 s: E-mehmon tashqi bazalarga (IIV, Chegara xizmati)
#: murojaat qiladi, lekin xodim abadiy kutib qolmasligi kerak
TIMEOUT = httpx.Timeout(20.0, connect=6.0)

#: Sinovlar uchun: `httpx.MockTransport` qo'yilsa tarmoqqa chiqilmaydi
TRANSPORT: httpx.AsyncBaseTransport | None = None


@dataclass
class Reply:
    data: dict
    status: int = 200
    #: Javob avvalgi so'rovdan qaytarilgan (takror yuborish)
    replay: bool = False
    sandbox: bool = False
    headers: dict = field(default_factory=dict)


class EmehmonClient:
    def __init__(self, base_url: str, username: str, secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.secret = secret

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
        lang: str = "uz",
    ) -> Reply:
        headers = {
            **ep.auth_headers(self.username, self.secret),
            "Accept": "application/json",
            "Accept-Language": lang if lang in ep.LANGUAGES else "uz",
        }
        content = None
        if body is not None:
            content = ep.encode_body(body)
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{self.base_url}{ep.API_PREFIX}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT, follow_redirects=False, transport=TRANSPORT
            ) as client:
                response = await client.request(
                    method, url, content=content, params=params, headers=headers
                )
        except httpx.HTTPError as exc:
            logger.info("E-mehmon %s %s — tarmoq xatosi: %s", method, path, type(exc).__name__)
            raise ep.EmehmonError(ep.NETWORK_ERROR) from None
        try:
            payload = response.json()
        except ValueError:
            logger.info("E-mehmon %s %s — JSON emas (HTTP %s)", method, path, response.status_code)
            raise ep.EmehmonError(ep.BAD_RESPONSE, status=response.status_code) from None
        try:
            data = ep.parse_envelope(response.status_code, payload)
        except ep.EmehmonError as exc:
            retry_after = response.headers.get("Retry-After")
            if retry_after and "retry_after" not in exc.meta:
                exc.meta["retry_after"] = retry_after
            logger.info(
                "E-mehmon %s %s — %s (HTTP %s)", method, path, exc.error_code, response.status_code
            )
            raise
        return Reply(
            data=data,
            status=response.status_code,
            replay=response.headers.get("Idempotent-Replay") == "1",
            sandbox=response.headers.get("X-Sandbox") == "1",
        )
