from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class WebAuthnOptionsResponse(BaseModel):
    challenge_id: UUID
    options: dict[str, Any]


class WebAuthnRegisterVerifyRequest(BaseModel):
    challenge_id: UUID
    credential: dict[str, Any]


class WebAuthnLoginOptionsRequest(BaseModel):
    # Berilsa, mos passkey'lar allowCredentials sifatida ko'rsatiladi;
    # bo'sh bo'lsa — brauzer discoverable credential (passkey) tanlagichini ko'rsatadi.
    username: str | None = None


class WebAuthnLoginVerifyRequest(BaseModel):
    challenge_id: UUID
    credential: dict[str, Any]


class PasskeyResponse(BaseModel):
    id: UUID
    device_label: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
