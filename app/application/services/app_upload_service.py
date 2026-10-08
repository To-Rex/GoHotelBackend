"""Dasturlar do'koniga BO'LAKLAB yuklash.

Nega: o'rnatish fayllari katta (APK ~100 MB, Windows o'rnatuvchisi undan
ham katta). Bitta so'rovda yuborilsa, yo'ldagi proksi (Traefik) tana 60
soniyadan uzoq kelayotgan so'rovni uzadi — sekin tarmoqdan yangi versiya
umuman qo'shib bo'lmas edi. Bo'laklab yuborilganda har so'rov bir necha
soniya oladi, xato bo'lgan bo'lak qayta yuboriladi, jarayon foizda ko'rinadi.

Oqim:
    POST   /superadmin/apps/uploads                 -> {upload_id, chunk_size, total_chunks}
    PUT    /superadmin/apps/uploads/{id}/chunks/{n} <- xom baytlar (application/octet-stream)
    POST   /superadmin/apps/uploads/{id}/complete   -> tayyor dastur yozuvi
    DELETE /superadmin/apps/uploads/{id}            -> bekor qilish

Bo'laklar server diskida vaqtinchalik papkada turadi (`UPLOAD_ROOT`), bitta
faylga yig'ilgach MinIO'ga OQIM bilan yoziladi — butun fayl xotiraga
sig'dirilmaydi. Tugallanmagan yuklashlar `STALE_AFTER` dan keyin o'chiriladi.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.app_store_service import (
    MAX_FILE_BYTES,
    AppStoreService,
    normalize_platform,
)
from app.core.exceptions import ConflictException, NotFoundException, ValidationException

logger = logging.getLogger(__name__)

#: Bitta bo'lak — proksi vaqt chegarasidan ancha kichik so'rov (sekin
#: tarmoqda ham bir necha soniya)
CHUNK_SIZE = 4 * 1024 * 1024
#: Bo'lak so'rovi tanasining mumkin bo'lgan eng katta hajmi
MAX_CHUNK_BYTES = 8 * 1024 * 1024
#: Shuncha vaqt tugallanmagan yuklash o'chiriladi
STALE_AFTER_SECONDS = 6 * 3600

UPLOAD_ROOT = Path(tempfile.gettempdir()) / "gohotel-app-uploads"
META_FILE = "meta.json"
ASSEMBLED_FILE = "file.bin"


def _part_name(index: int) -> str:
    return f"{index:06d}.part"


class AppUploadService:
    """Yuklash sessiyalari — disk ustida, bazaga tegmaydi (faqat `complete`)."""

    def __init__(self, root: Path | None = None):
        self.root = root or UPLOAD_ROOT

    # --------------------------------------------------------- yordamchi --
    def _dir(self, upload_id: str) -> Path:
        if not upload_id or any(ch not in "0123456789abcdef" for ch in upload_id):
            raise NotFoundException("Yuklash topilmadi", "UPLOAD_NOT_FOUND")
        return self.root / upload_id

    def _meta(self, upload_id: str) -> tuple[Path, dict]:
        folder = self._dir(upload_id)
        path = folder / META_FILE
        if not path.is_file():
            raise NotFoundException("Yuklash topilmadi yoki muddati o'tgan", "UPLOAD_NOT_FOUND")
        return folder, json.loads(path.read_text(encoding="utf-8"))

    def _received(self, folder: Path, total: int) -> list[int]:
        return [i for i in range(total) if (folder / _part_name(i)).is_file()]

    def cleanup_stale(self, now: float | None = None) -> int:
        """Eski tugallanmagan yuklashlarni o'chiradi (har boshlashda chaqiriladi)."""
        if not self.root.is_dir():
            return 0
        now = now or time.time()
        removed = 0
        for folder in self.root.iterdir():
            try:
                if folder.is_dir() and now - folder.stat().st_mtime > STALE_AFTER_SECONDS:
                    shutil.rmtree(folder, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------ oqim --
    def start(
        self,
        *,
        platform: str,
        name: str,
        version: str | None,
        notes: str | None,
        filename: str,
        size: int,
        content_type: str | None,
        uploaded_by: UUID | None = None,
    ) -> dict:
        platform = normalize_platform(platform)
        title = (name or "").strip()
        if not title:
            raise ValidationException("Dastur nomi kerak", "NAME_REQUIRED")
        if not isinstance(size, int) or size <= 0:
            raise ValidationException("Fayl bo'sh", "EMPTY_FILE")
        if size > MAX_FILE_BYTES:
            raise ValidationException("Fayl juda katta", "FILE_TOO_LARGE")

        self.cleanup_stale()
        upload_id = uuid.uuid4().hex
        folder = self.root / upload_id
        folder.mkdir(parents=True, exist_ok=False)
        meta = {
            "platform": platform,
            "name": title,
            "version": (version or "").strip() or None,
            "notes": (notes or "").strip() or None,
            "filename": (filename or "").strip() or "app.bin",
            "content_type": content_type,
            "size": size,
            "chunk_size": CHUNK_SIZE,
            "total_chunks": math.ceil(size / CHUNK_SIZE),
            "uploaded_by": str(uploaded_by) if uploaded_by else None,
            "created_at": time.time(),
        }
        (folder / META_FILE).write_text(json.dumps(meta), encoding="utf-8")
        return {
            "upload_id": upload_id,
            "chunk_size": CHUNK_SIZE,
            "total_chunks": meta["total_chunks"],
        }

    def write_chunk(self, upload_id: str, index: int, data: bytes) -> dict:
        """Bitta bo'lakni saqlaydi. Qayta yuborilsa — ustiga yoziladi (xato
        bo'lgan bo'lakni takror yuborish xavfsiz)."""
        if len(data) > MAX_CHUNK_BYTES:
            raise ValidationException("Bo'lak juda katta", "CHUNK_TOO_LARGE")
        folder, meta = self._meta(upload_id)
        total = int(meta["total_chunks"])
        if index < 0 or index >= total:
            raise ValidationException(f"Bo'lak raqami noto'g'ri: {index}", "CHUNK_INDEX")
        chunk_size = int(meta["chunk_size"])
        expected = chunk_size if index < total - 1 else int(meta["size"]) - chunk_size * (total - 1)
        if len(data) != expected:
            raise ValidationException(
                f"Bo'lak hajmi noto'g'ri: {len(data)} (kutilgan {expected})", "CHUNK_SIZE"
            )
        target = folder / _part_name(index)
        tmp = folder / (_part_name(index) + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        os.utime(folder, None)
        received = self._received(folder, total)
        return {"index": index, "received": len(received), "total_chunks": total}

    def status(self, upload_id: str) -> dict:
        folder, meta = self._meta(upload_id)
        total = int(meta["total_chunks"])
        received = self._received(folder, total)
        return {
            "upload_id": upload_id,
            "received": len(received),
            "total_chunks": total,
            "missing": [i for i in range(total) if i not in set(received)],
        }

    async def complete(self, session: AsyncSession, upload_id: str) -> dict:
        """Bo'laklarni yig'ib MinIO'ga yozadi va do'kon yozuvini yaratadi."""
        folder, meta = self._meta(upload_id)
        total = int(meta["total_chunks"])
        missing = [i for i in range(total) if not (folder / _part_name(i)).is_file()]
        if missing:
            raise ConflictException(
                f"Hali kelmagan bo'laklar: {len(missing)} ta (masalan {missing[0]})",
                "UPLOAD_INCOMPLETE",
            )
        assembled = folder / ASSEMBLED_FILE
        with assembled.open("wb") as out:
            for i in range(total):
                with (folder / _part_name(i)).open("rb") as part:
                    shutil.copyfileobj(part, out, 1024 * 1024)
        actual = assembled.stat().st_size
        if actual != int(meta["size"]):
            raise ConflictException(
                f"Fayl hajmi mos kelmadi: {actual} (kutilgan {meta['size']})", "UPLOAD_SIZE_MISMATCH"
            )
        uploaded_by = meta.get("uploaded_by")
        try:
            release = await AppStoreService(session).create_from_path(
                platform=meta["platform"],
                name=meta["name"],
                version=meta.get("version"),
                notes=meta.get("notes"),
                filename=meta["filename"],
                file_path=assembled,
                content_type=meta.get("content_type"),
                uploaded_by=UUID(uploaded_by) if uploaded_by else None,
            )
        finally:
            # Yig'ilgan fayl va bo'laklar MinIO'ga yozilgach (yoki xatoda) kerak emas
            shutil.rmtree(folder, ignore_errors=True)
        logger.info(
            "Do'konga bo'laklab yuklandi: %s %s (%s bayt, %s bo'lak)",
            meta["platform"], meta["name"], actual, total,
        )
        return release

    def abort(self, upload_id: str) -> None:
        folder = self._dir(upload_id)
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
