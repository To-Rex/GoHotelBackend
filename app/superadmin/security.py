"""Boshqaruv paneli kirishi — asosiy tizimdan ALOHIDA.

Bu panel butun tizim ustidan to'liq nazorat beradi: barcha
mehmonxonalar, filiallar va xodimlar. Shuning uchun uning kirishi
mehmonxona xodimlarining kirishidan butunlay ajratilgan:

- BOSHQA TOKEN. Panel tokenida `aud: superadmin` turadi. Xodim tokeni
  bilan panelga kirib bo'lmaydi va panel tokeni bilan mehmonxona
  endpointlariga o'tib bo'lmaydi — ikkalasi bir-birini tanimaydi.
- BOSHQA JADVAL. Panel foydalanuvchilari `panel_users` da, mehmonxona
  xodimlari esa `users` da. Ular aralashmaydi.
- ILDIZ HISOB KODDA. Birinchi kirish uchun hisob shu faylda turadi:
  bazasi bo'sh tizimga ham egasi kira olishi kerak.

Ildiz hisob ochiq matnda SAQLANMAYDI:

- pochta manzili SHA-256 yig'indisi sifatida — kodni o'qigan odam
  manzilni bilib ololmaydi, lekin kiritilgan manzilni tekshirsa
  bo'ladi;
- parol bcrypt bilan hashlangan — undan parolni tiklab bo'lmaydi.

Ikkalasini ham muhit o'zgaruvchisi bilan almashtirish mumkin
(`PANEL_ROOT_EMAIL_SHA256`, `PANEL_ROOT_PASSWORD_HASH`) — parolni
almashtirish uchun kodga tegish shart emas. Panel ichidan parol
o'zgartirilsa esa yangi hash BAZAGA yoziladi va shundan keyin
ildiz hash ishlatilmaydi.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

#: Panel tokenining "auditoriyasi". Xodim tokenida bu maydon yo'q,
#: shuning uchun u panelga o'tolmaydi.
TOKEN_AUDIENCE = "superadmin"

#: Token amal qilish muddati. Panel kuchli vosita — sessiya uzoq
#: turmasligi kerak.
TOKEN_TTL_HOURS = 12

#: Ildiz hisob. Ochiq matn yo'q: pochta yig'indisi va parol hashi.
ROOT_EMAIL_SHA256 = os.getenv(
    "PANEL_ROOT_EMAIL_SHA256",
    "eafbc50143af7bcad2703b28647681b3fb875ef8d896ad98b29caa07ca51f2b7",
)
ROOT_PASSWORD_HASH = os.getenv(
    "PANEL_ROOT_PASSWORD_HASH",
    "$2b$12$vqjwt/8xx29cn7ETBAQ2f.x3FWsn0PFFkMNyla5o6PTxl1Jj/wQle",
)

#: Ildiz hisobning ko'rinadigan nomi — pochtasi hashlangani uchun
#: ro'yxatda shu ko'rinadi.
ROOT_LABEL = "Tizim egasi"

MIN_PASSWORD_LENGTH = 8


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def email_fingerprint(email: str) -> str:
    """Pochta manzilining SHA-256 yig'indisi."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def is_root_email(email: str) -> bool:
    """Kiritilgan manzil ildiz hisobnikimi.

    Taqqoslash doimiy vaqtda: manzilni belgima-belgi topishga urinish
    imkoniyati qolmasligi kerak.
    """
    import hmac

    return hmac.compare_digest(email_fingerprint(email), ROOT_EMAIL_SHA256)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Buzuq hash — kirishga ruxsat berilmaydi
        return False


def create_token(subject: str, email: str, is_root: bool) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "email": email,
        "root": is_root,
        "aud": TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_token(token: str) -> dict | None:
    """Panel tokenini ochadi. Xodim tokeni bu yerdan o'tmaydi."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=TOKEN_AUDIENCE,
        )
    except jwt.PyJWTError:
        return None
