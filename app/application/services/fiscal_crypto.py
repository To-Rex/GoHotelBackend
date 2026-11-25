"""E-POS tokenini shifrlash (Fernet, AES128-CBC + HMAC).

Token har filialda o'ziniki va bazada (`branches.settings["fiscal"]`) faqat
SHIFRLANGAN holda turadi: bazaga kirgan odam uni o'qiy olmaydi. Shifr kaliti
`SECRET_KEY` dan, alohida kontekst bilan hosil qilinadi — AI, SMS va push
kalitlari bilan bir xil shifr kalitidan foydalanilmaydi.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(("gohotel-fiscal:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Ochilmasa (`SECRET_KEY` almashgan yoki yozuv buzilgan) — `None`:
    token qayta kiritilishi kerak bo'ladi."""
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def hint(token: str) -> str:
    """Tokenning oxirgi 4 belgisi — sozlamada "qaysi token saqlangan"ni tanish uchun."""
    return (token or "").strip()[-4:]
