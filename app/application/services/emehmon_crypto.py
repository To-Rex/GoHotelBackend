"""E-mehmon maxfiy kalitini shifrlash (Fernet, AES128-CBC + HMAC).

Maxfiy kalit (secret) har mehmonxona/filialniki va E-mehmon uni FAQAT BIR
MARTA ko'rsatadi. Bazada (`branches.settings["emehmon"]`) u faqat
SHIFRLANGAN holda turadi va hech qaysi javobda qaytmaydi — so'rovlar
serverning o'zida imzolanadi, brauzerga kalit umuman chiqmaydi.

Shifr kaliti `SECRET_KEY` dan, alohida kontekst bilan hosil qilinadi — AI,
fiskal modul va boshqa kalitlar bilan bir xil shifr kaliti ishlatilmaydi.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(("gohotel-emehmon:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Ochilmasa (`SECRET_KEY` almashgan yoki yozuv buzilgan) — `None`:
    kalit qayta kiritilishi kerak bo'ladi."""
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def hint(secret: str) -> str:
    """Kalitning oxirgi 4 belgisi — "qaysi kalit saqlangan"ni tanish uchun."""
    return (secret or "").strip()[-4:]
