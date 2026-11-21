"""AI provider API kalitini shifrlash (Fernet, AES128-CBC + HMAC).

Kalit SECRET_KEY dan, alohida kontekst bilan hosil qilinadi — SMS va
push kalitlari bilan bir xil shifr kalitidan foydalanilmaydi.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(("gohotel-ai:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def hint(key: str) -> str:
    """Kalitning oxirgi 4 belgisi (ko'rsatish uchun)."""
    tail = (key or "").strip()[-4:]
    return tail
