"""Firebase kalitini panel orqali boshqarish.

Kalit (service-account JSON) ilgari faqat env-var yoki diskdagi fayldan
o'qilardi — almashtirish uchun redeploy kerak edi. Endi egasi uni panel
orqali yuklaydi: kalit bazada SHIFRLANGAN holda saqlanadi, yuklangan
zahoti Firebase qayta ishga tushadi (restartsiz) va server qayta
ko'tarilganda ham bazadagi kalit qaytadan qo'llanadi.

Ustuvorlik: panel orqali yuklangan kalit > env > fayl. Panel kaliti
o'chirilsa, tizim avvalgidek env/faylga qaytadi.

Shifrlash — Fernet (AES128-CBC + HMAC), kaliti `SECRET_KEY` dan
hosil qilinadi: bazaga kirgan odam kalitni o'qiy olmaydi, server esa
o'z sirini bilgani uchun ochaveradi.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationException
from app.infrastructure.push import firebase
from app.superadmin.models import PanelSetting

logger = logging.getLogger(__name__)

#: Kalit saqlanadigan yozuv nomi.
SETTING_NAME = "firebase_credentials"

#: Service-account JSON'da bo'lishi SHART bo'lgan maydonlar — yaroqsiz
#: fayl saqlangunga qadar rad etiladi.
REQUIRED_FIELDS = ("type", "project_id", "private_key", "client_email")


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        # SECRET_KEY almashgan bo'lsa eski yozuv ochilmaydi — kalitni
        # qayta yuklash kerak bo'ladi
        return None


def validate_credentials(raw: str) -> str:
    """Kiritilgan kalitni tekshirib, sof JSON matnini qaytaradi.

    Xom JSON ham, base64 ham qabul qilinadi (env-varlar odatda base64
    saqlaydi). Maydonlari yetishmasa saqlashga yo'l qo'yilmaydi —
    yaroqsiz kalit push'ni jimgina o'chirib qo'ygan bo'lardi.
    """
    text = (raw or "").strip()
    if not text:
        raise ValidationException("Kalit bo'sh", "EMPTY_CREDENTIALS")
    if not text.lstrip().startswith("{"):
        try:
            text = base64.b64decode(text).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise ValidationException(
                "Kalit JSON ham, base64 ham emas", "INVALID_CREDENTIALS"
            ) from None
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        raise ValidationException("Kalit yaroqsiz JSON", "INVALID_CREDENTIALS") from None
    if not isinstance(info, dict):
        raise ValidationException("Kalit yaroqsiz JSON", "INVALID_CREDENTIALS")

    missing = [f for f in REQUIRED_FIELDS if not info.get(f)]
    if missing:
        raise ValidationException(
            f"Kalitda maydonlar yetishmaydi: {', '.join(missing)}",
            "MISSING_FIELDS",
        )
    if info.get("type") != "service_account":
        raise ValidationException(
            'Bu service-account kaliti emas ("type" mos kelmadi)',
            "NOT_SERVICE_ACCOUNT",
        )
    return json.dumps(info)


class PushConfigService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _row(self) -> PanelSetting | None:
        return (
            await self.session.execute(
                select(PanelSetting).where(PanelSetting.name == SETTING_NAME)
            )
        ).scalar_one_or_none()

    async def save(self, raw: str, actor_id: UUID | None) -> dict:
        """Kalitni tekshiradi, shifrlab saqlaydi va Firebase'ni qayta yuklaydi."""
        normalized = validate_credentials(raw)
        row = await self._row()
        if row is None:
            row = PanelSetting(name=SETTING_NAME)
            self.session.add(row)
        row.value = encrypt(normalized)
        row.updated_by = actor_id
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

        firebase.set_credential_override(normalized)
        firebase.reload()
        return await self.status()

    async def delete(self) -> dict:
        """Panel kalitini o'chiradi — tizim env/fayl kalitiga qaytadi."""
        row = await self._row()
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()
        firebase.set_credential_override(None)
        firebase.reload()
        return await self.status()

    async def status(self) -> dict:
        """Push holati — panel kartasi shuni ko'rsatadi."""
        row = await self._row()
        project_id = None
        stored = row is not None
        readable = False
        if row is not None:
            text = decrypt(row.value)
            readable = text is not None
            if text:
                try:
                    project_id = json.loads(text).get("project_id")
                except (json.JSONDecodeError, AttributeError):
                    readable = False
        return {
            **firebase.diagnostics(),
            "panel_key_stored": stored,
            # SECRET_KEY almashgan bo'lsa yozuv turadi-yu, ochilmaydi —
            # panel buni alohida ko'rsatadi
            "panel_key_readable": readable,
            "project_id": project_id,
            "updated_at": (
                row.updated_at.isoformat() if row and row.updated_at else None
            ),
        }


async def apply_stored_credentials() -> None:
    """Server ko'tarilganda bazadagi panel kalitini qo'llaydi.

    Jadval hali yaratilmagan (migratsiya o'tmagan) yoki baza vaqtincha
    yo'q bo'lsa — jimgina o'tadi: push env/fayl kaliti bilan yoki
    o'chirilgan holda ishlayveradi.
    """
    try:
        from app.core.database import _get_session_factory

        factory = _get_session_factory()
        async with factory() as session:
            service = PushConfigService(session)
            row = await service._row()
            if row is None:
                return
            text = decrypt(row.value)
            if text:
                firebase.set_credential_override(text)
                logger.info("Firebase kaliti bazadan qo'llandi (panel)")
            else:
                logger.warning(
                    "Bazadagi Firebase kaliti ochilmadi — SECRET_KEY "
                    "almashgan bo'lishi mumkin; panel orqali qayta yuklang"
                )
    except Exception:  # noqa: BLE001
        logger.exception("Bazadagi Firebase kalitini qo'llab bo'lmadi")
