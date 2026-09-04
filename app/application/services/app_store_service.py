"""Dasturlar do'koni: panel yuklaydi, mehmonxona adminlari yuklab oladi.

Ikki mijoz bitta jadval ustida ishlaydi:

* **Boshqaruv paneli** — fayl yuklaydi, ro'yxatni boshqaradi, o'chiradi.
* **Mehmonxona administratori** — faqat ro'yxatni ko'radi va yuklab oladi.

Fayllar MinIO'da, `app-store/` prefiksi ostida yotadi. Yuklab olish
BACKEND ORQALI oqim bilan beriladi (presigned URL emas): MinIO tashqi
tarmoqdan ko'rinmasligi mumkin, backend esa har doim ko'rinadi va
avtorizatsiyani ham o'zi tekshiradi.
"""
from __future__ import annotations

import uuid as uuid_module
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.app_release import AppRelease
from app.infrastructure.storage import minio as storage

PLATFORMS = ("ANDROID", "WINDOWS")

#: O'rnatish fayli uchun oqilona chegara. Windows o'rnatuvchilari katta
#: bo'ladi — chegara shunga mos.
MAX_FILE_BYTES = 500 * 1024 * 1024

#: Kengaytmadan aniqlanadigan turlar — brauzer faylni to'g'ri nomlashi
#: va Android APK'ni o'rnatishga taklif qilishi uchun.
_MIME_BY_EXTENSION = {
    ".apk": "application/vnd.android.package-archive",
    ".aab": "application/octet-stream",
    ".exe": "application/vnd.microsoft.portable-executable",
    ".msi": "application/x-msi",
    ".msix": "application/msix",
    ".zip": "application/zip",
}


def normalize_platform(value: str | None) -> str:
    platform = (value or "").strip().upper()
    if platform not in PLATFORMS:
        raise ValidationException("Noma'lum platforma", "INVALID_PLATFORM")
    return platform


def mime_for(filename: str, provided: str | None) -> str:
    """Fayl turi: brauzer yuborganidan ko'ra kengaytma ishonchliroq.

    Ko'p brauzerlar APK uchun `application/octet-stream` yuboradi — unda
    yuklab olishda fayl noto'g'ri nomlanadi.
    """
    name = (filename or "").lower()
    for extension, mime in _MIME_BY_EXTENSION.items():
        if name.endswith(extension):
            return mime
    return provided or "application/octet-stream"


def storage_path(platform: str, filename: str) -> str:
    """MinIO'dagi manzil — har yuklash o'z papkasida.

    Tasodifiy bo'lak ataylab qo'shiladi: bir xil nomli fayl qayta
    yuklanganda eskisining ustiga yozilib, hali o'chirilmagan eski yozuv
    boshqa faylga ishora qilib qolmasligi kerak.
    """
    safe = "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in (filename or "app.bin")
    )
    return f"app-store/{platform.lower()}/{uuid_module.uuid4().hex}/{safe}"


class AppStoreService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -------------------------------------------------------- yozish --

    async def create(
        self,
        *,
        platform: str,
        name: str,
        version: str | None,
        notes: str | None,
        filename: str,
        content: bytes,
        content_type: str | None,
        uploaded_by: UUID | None = None,
    ) -> dict:
        platform = normalize_platform(platform)
        title = (name or "").strip()
        if not title:
            raise ValidationException("Dastur nomi kerak", "NAME_REQUIRED")
        if not content:
            raise ValidationException("Fayl bo'sh", "EMPTY_FILE")
        if len(content) > MAX_FILE_BYTES:
            raise ValidationException("Fayl juda katta", "FILE_TOO_LARGE")

        bucket = settings.MINIO_BUCKET_DOCUMENTS
        path = storage_path(platform, filename)
        mime = mime_for(filename, content_type)
        await storage.upload_file(bucket, path, content, mime)

        release = AppRelease(
            platform=platform,
            name=title,
            version=(version or "").strip() or None,
            notes=(notes or "").strip() or None,
            original_name=filename or "app.bin",
            mime_type=mime,
            file_size=len(content),
            minio_bucket=bucket,
            minio_path=path,
            uploaded_by=uploaded_by,
        )
        self.session.add(release)
        await self.session.flush()
        await self.session.refresh(release)
        return self._as_dict(release)

    async def delete(self, release_id: UUID) -> None:
        release = await self._get(release_id)
        # Avval fayl, keyin yozuv: yozuv o'chib fayl qolsa, uni topadigan
        # hech narsa qolmaydi — teskarisi xatoda ro'yxatda o'lik havola
        # qoldirardi
        await storage.delete_file(release.minio_bucket, release.minio_path)
        await self.session.delete(release)
        await self.session.flush()

    async def record_download(self, release_id: UUID) -> None:
        await self.session.execute(
            update(AppRelease)
            .where(AppRelease.id == release_id)
            .values(download_count=AppRelease.download_count + 1)
        )
        await self.session.flush()

    # -------------------------------------------------------- o'qish --

    async def list(self) -> list[dict]:
        rows = (
            (
                await self.session.execute(
                    select(AppRelease).order_by(
                        AppRelease.platform, desc(AppRelease.created_at)
                    )
                )
            )
            .scalars()
            .all()
        )
        return [self._as_dict(row) for row in rows]

    async def get(self, release_id: UUID) -> dict:
        return self._as_dict(await self._get(release_id))

    async def stream(self, release_id: UUID):
        """Yuklab olish: (tavsif, bo'laklar iteratori).

        Fayl backend orqali bo'lak-bo'lak uzatiladi — katta o'rnatuvchi
        server xotirasiga to'liq sig'dirilmaydi.
        """
        release = await self._get(release_id)
        response = await storage.open_file_stream(
            release.minio_bucket, release.minio_path
        )

        def chunks():
            try:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                response.close()
                response.release_conn()

        return self._as_dict(release), chunks()

    async def _get(self, release_id: UUID) -> AppRelease:
        release = await self.session.get(AppRelease, release_id)
        if release is None:
            raise NotFoundException("Dastur topilmadi", "APP_NOT_FOUND")
        return release

    @staticmethod
    def _as_dict(release: AppRelease) -> dict:
        return {
            "id": str(release.id),
            "platform": release.platform,
            "name": release.name,
            "version": release.version,
            "notes": release.notes,
            "original_name": release.original_name,
            "mime_type": release.mime_type,
            "file_size": int(release.file_size or 0),
            "download_count": int(release.download_count or 0),
            "created_at": (
                release.created_at.isoformat() if release.created_at else None
            ),
        }
