"""API ni JARAYON ICHIDA, xodimning O'Z tokeni bilan chaqirish.

AI hech qachon bazaga to'g'ridan-to'g'ri tegmaydi: har o'qish va har
tasdiqlangan amal — oddiy API so'rovi, xuddi xodim saytda bosgandek.
Shuning uchun ruxsatlar, filial chegarasi, ish vaqti, qurilma tekshiruvi,
smena talabi va barcha biznes qoidalari aynan o'sha joyda tekshiriladi;
AI uchun alohida "yo'l" yo'q.

So'rov tarmoqqa chiqmaydi (httpx ASGITransport) — ilovaning o'ziga.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.application.services.ai.catalog import API_PREFIX, normalize_path

AI_HEADER = "X-AI-Assistant"
USER_AGENT = "GoHotel-AI/1.0"


@dataclass
class Auth:
    token: str
    device_id: str | None = None


@dataclass
class ApiResult:
    status: int
    data: Any
    headers: dict

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def error_code(self) -> str | None:
        return self.data.get("error_code") if isinstance(self.data, dict) else None

    @property
    def detail(self) -> str:
        if isinstance(self.data, dict):
            d = self.data.get("detail") or self.data.get("message")
            if isinstance(d, list):
                # Pydantic xatolari: "body.amount: ..." ko'rinishida
                parts = []
                for item in d[:5]:
                    if isinstance(item, dict):
                        loc = ".".join(str(x) for x in item.get("loc", []) if x not in ("body",))
                        parts.append(f"{loc}: {item.get('msg')}")
                return "; ".join(parts) or "Validation error"
            if d:
                return str(d)
        return f"HTTP {self.status}"


def _clean_params(params: dict | None) -> dict:
    out: dict = {}
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            out[key] = ",".join(str(v) for v in value)
        else:
            out[key] = value
    return out


async def call_api(
    method: str,
    path: str,
    auth: Auth,
    *,
    params: dict | None = None,
    body: Any = None,
    timeout: float = 60.0,
) -> ApiResult:
    from app.main import app  # aylanma importdan qochish uchun shu yerda

    headers = {
        "Authorization": f"Bearer {auth.token}",
        "User-Agent": USER_AGENT,
        AI_HEADER: "1",
    }
    if auth.device_id:
        headers["X-Device-Id"] = auth.device_id
    url = API_PREFIX + normalize_path(path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ai.internal", timeout=timeout) as client:
        response = await client.request(
            method.upper(),
            url,
            params=_clean_params(params),
            json=body if method.upper() != "GET" and body is not None else None,
            headers=headers,
        )
    try:
        data = response.json()
    except ValueError:
        data = {"detail": response.text[:500]}
    return ApiResult(response.status_code, data, dict(response.headers))


__all__ = ["AI_HEADER", "ApiResult", "Auth", "call_api"]
