"""Skanerlangan hujjat (pasport, ID karta) suratlarini MinIO'da saqlash.

Hujjat qayerda skanerlansa ham surati saqlanadi:

* Qabulxona telefoni (`POST /reception/scans`) — rasm serverga baribir
  keladi, shu yerda MinIO'ga yoziladi va skan yozuviga biriktiriladi
  (`entity_type="document_scan"`). Hujjat raqami bo'yicha mehmon topilsa
  — darhol mehmonga ham. Yangi mehmon vebda yaratilganda skan unga
  bog'lanadi (`/reception/scans/{id}/link-guest`).
* Veb skaneri (server yoki brauzer OCR) — mehmon yaratilgach/tanlangach
  rasm `POST /guests/{id}/document-images` ga yuklanadi.

Har surat — `file_attachments` qatori (`category="document_<tomon>"`,
tomon: passport | front | back), obyekt `MINIO_BUCKET_GUESTS` bucketida.
Bitta rasm skan va mehmonga ikki qator bilan bog'lanishi mumkin — obyekt
bitta (nusxa olinmaydi).

Saqlash ixtiyoriy emas, lekin o'chirib qo'yish mumkin: skaner sozlamasida
`store_images` (standart — yoqiq). MinIO ishlamay qolsa skaner to'xtamaydi:
saqlash "iloji boricha" bajariladi va xato faqat logga yoziladi.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import AppException
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.infrastructure.storage import minio as storage

logger = logging.getLogger(__name__)

ENTITY_GUEST = "guest"
ENTITY_SCAN = "document_scan"
CATEGORY_PREFIX = "document_"
SIDES = ("passport", "front", "back")

#: Mehmon sahifasida hujjat suratlari qatorida ko'rsatiladigan toifalar:
#: skaner suratlari va avvaldan qo'lda yuklanadigan "pasport surati /
#: mehmon fotosi" (`photo`)
GUEST_IMAGE_CATEGORIES = tuple(f"{CATEGORY_PREFIX}{side}" for side in SIDES) + ("photo",)

SCAN_SETTINGS_KEY = "document_scan"

#: Bitta suratni yozishga ajratilgan vaqt. MinIO javob bermay qolsa skaner
#: shundan ortiq kutmaydi (qolgan tomonlar ham urinilmaydi).
UPLOAD_TIMEOUT_SECONDS = 15

#: Ruxsat etilgan rasm turlari: (sarlavha baytlari, MIME, kengaytma)
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)


class DocumentStorageUnavailable(AppException):
    """Surat ataylab yuklanganda MinIO javob bermadi (skaner oqimida
    bu xato chiqmaydi — u yerda saqlash "iloji boricha")."""

    def __init__(self) -> None:
        super().__init__(
            "Hujjat suratini saqlab bo'lmadi — fayl ombori javob bermayapti",
            "DOCUMENT_IMAGE_STORAGE_FAILED",
        )
        self.status_code = 503


def store_enabled(hotel_settings: dict | None) -> bool:
    """Skaner sozlamasidagi bayroq; yozilmagan bo'lsa — yoqiq."""
    saved = (hotel_settings or {}).get(SCAN_SETTINGS_KEY) or {}
    value = saved.get("store_images")
    return value if isinstance(value, bool) else True


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """Baytlardan rasm turini aniqlaydi (fayl nomi/MIMEga ishonilmaydi)."""
    if not data:
        return None
    for magic, mime, ext in _SIGNATURES:
        if data.startswith(magic):
            return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def side_of(category: str | None) -> str | None:
    if category and category.startswith(CATEGORY_PREFIX):
        side = category[len(CATEGORY_PREFIX):]
        return side if side in SIDES else None
    return None


async def store_images(
    session,
    *,
    hotel_id: UUID,
    images: dict[str, bytes],
    entity_type: str,
    entity_id: UUID,
    uploaded_by: UUID,
    document_type: str | None = None,
) -> list[FileAttachment]:
    """Suratlarni MinIO'ga yozadi va biriktiradi. Rasm bo'lmagan yoki
    yozib bo'lmagan tomon tashlab ketiladi (skaner oqimi to'xtamasin)."""
    stored: list[FileAttachment] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    for side, data in images.items():
        if side not in SIDES or not data:
            continue
        kind = sniff_image(data)
        if kind is None:
            logger.warning("Hujjat surati rasm emas (%s/%s, %s)", entity_type, entity_id, side)
            continue
        mime, ext = kind
        path = f"{hotel_id}/{entity_type}/{entity_id}/document-{side}-{stamp}.{ext}"
        try:
            await asyncio.wait_for(
                storage.upload_file(settings.MINIO_BUCKET_GUESTS, path, data, mime),
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — MinIO o'chiq bo'lsa ham skaner ishlasin
            logger.exception("Hujjat suratini MinIO'ga yozib bo'lmadi: %s", path)
            # Ombor ishlamayapti — keyingi tomon ham shu yerda kutib qolardi
            break
        label = (document_type or "document").lower()
        attachment = FileAttachment(
            hotel_id=hotel_id,
            entity_type=entity_type,
            entity_id=entity_id,
            file_name=path.rsplit("/", 1)[-1],
            original_name=f"{label}-{side}.{ext}",
            mime_type=mime,
            file_size=len(data),
            minio_bucket=settings.MINIO_BUCKET_GUESTS,
            minio_path=path,
            category=f"{CATEGORY_PREFIX}{side}",
            uploaded_by=uploaded_by,
        )
        session.add(attachment)
        stored.append(attachment)
    if stored:
        await session.flush()
    return stored


async def link_to_guest(
    session,
    *,
    hotel_id: UUID,
    attachments: list[FileAttachment],
    guest_id: UUID,
) -> list[FileAttachment]:
    """Skan suratlarini mehmonga ham biriktiradi (obyekt bitta, qator yangi).
    Allaqachon bog'langan obyekt qayta qo'shilmaydi."""
    if not attachments:
        return []
    existing = set(
        (
            await session.execute(
                select(FileAttachment.minio_path).where(
                    FileAttachment.hotel_id == hotel_id,
                    FileAttachment.entity_type == ENTITY_GUEST,
                    FileAttachment.entity_id == guest_id,
                    FileAttachment.is_deleted.is_(False),
                )
            )
        ).scalars().all()
    )
    linked: list[FileAttachment] = []
    for src in attachments:
        if src.minio_path in existing:
            continue
        copy = FileAttachment(
            hotel_id=hotel_id,
            entity_type=ENTITY_GUEST,
            entity_id=guest_id,
            file_name=src.file_name,
            original_name=src.original_name,
            mime_type=src.mime_type,
            file_size=src.file_size,
            minio_bucket=src.minio_bucket,
            minio_path=src.minio_path,
            category=src.category,
            uploaded_by=src.uploaded_by,
        )
        session.add(copy)
        linked.append(copy)
        existing.add(src.minio_path)
    if linked:
        await session.flush()
    return linked


async def attachments_of(session, *, hotel_id: UUID, entity_type: str, entity_id: UUID, categories=None):
    stmt = select(FileAttachment).where(
        FileAttachment.hotel_id == hotel_id,
        FileAttachment.entity_type == entity_type,
        FileAttachment.entity_id == entity_id,
        FileAttachment.is_deleted.is_(False),
    )
    if categories:
        stmt = stmt.where(FileAttachment.category.in_(list(categories)))
    return (await session.execute(stmt.order_by(FileAttachment.created_at.desc()))).scalars().all()


def attachment_dict(att: FileAttachment, uploader_name: str | None = None) -> dict:
    return {
        "id": str(att.id),
        "category": att.category,
        "side": side_of(att.category),
        "mime_type": att.mime_type,
        "file_size": att.file_size,
        "created_at": att.created_at.isoformat() if att.created_at else None,
        "uploaded_by_name": uploader_name,
    }
