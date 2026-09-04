"""Yuklangan hujjat rasmlarini o'qishning umumiy yo'li.

Ikki mijoz bir xil ishni so'raydi: veb (`/guests/scan-document`) va
qabulxona telefoni (`/reception/scans`). Rasmni o'qish, hajm chegarasi,
bir vaqtda ishlaydigan skanerlar soni va xato matnlari ikkalasida ham
bir xil bo'lishi kerak — shuning uchun ular shu yerda, bitta joyda.

Rasm SAQLANMAYDI: faqat xotirada o'qiladi va javob qaytgach yo'qoladi.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import anyio
from fastapi import HTTPException

from app.core.exceptions import ValidationException

logger = logging.getLogger(__name__)

#: Bir vaqtda nechta OCR ishlaydi. Model kichik, lekin VPS yadrolari kam —
#: cheklovsiz qo'yilsa bir nechta parallel skan bir-birini sekinlashtiradi.
MAX_CONCURRENT_SCANS = 2
_scan_limiter = anyio.Semaphore(MAX_CONCURRENT_SCANS)

#: Yuklangan rasm uchun oqilona chegara (klientlar ~200-400 KB yuboradi).
MAX_SCAN_IMAGE_BYTES = 12 * 1024 * 1024

#: Dvigatel qaytaradigan xato kodlari uchun foydalanuvchi matni.
ERROR_MESSAGES = {
    "BAD_IMAGE": "Rasm o'qilmadi",
    "IMAGE_TOO_SMALL": "Rasm juda kichik — hujjatni yaqinroqdan oling",
    "NO_TEXT": "Rasmda yozuv topilmadi — hujjatni ramkaga to'liq joylang",
}


@lru_cache(maxsize=1)
def server_ocr_available() -> bool:
    """Serverda dvigatel va modellar bormi.

    Natija keshlanadi: modul importi va model fayllarini tekshirish
    jarayon ishlagan davomida o'zgarmaydi, bu funksiya esa skaner
    sozlamasi har so'ralganda chaqiriladi.
    """
    try:
        from app.application.services.document_ocr import engine as ocr_engine

        return ocr_engine.engine_importable()
    except Exception:  # noqa: BLE001
        return False


def require_server_ocr() -> None:
    """Dvigatel yo'q bo'lsa 503 — klient buni ko'rib o'z yo'lini tanlaydi."""
    if not server_ocr_available():
        raise HTTPException(
            status_code=503, detail="Server hujjat skaneri bu serverda mavjud emas"
        )


async def read_image(file, label: str) -> bytes | None:
    """Yuklangan faylni o'qiydi va hajmini tekshiradi."""
    if file is None:
        return None
    content = await file.read()
    if not content:
        raise ValidationException(f"{label}: rasm bo'sh", "BAD_IMAGE")
    if len(content) > MAX_SCAN_IMAGE_BYTES:
        raise ValidationException(f"{label}: rasm juda katta", "IMAGE_TOO_LARGE")
    return content


async def run_scan(images: dict[str, bytes], document_type: str) -> dict:
    """OCR'ni ishga tushiradi va xatolarni foydalanuvchi tiliga o'giradi.

    OCR og'ir va sinxron — u alohida oqimda bajariladi, aks holda bitta
    skan butun serverni ushlab turardi.
    """
    if not images:
        raise ValidationException("Hujjat rasmi yuborilmadi", "BAD_IMAGE")

    from app.application.services.document_ocr import service as ocr_service

    async with _scan_limiter:
        try:
            return await anyio.to_thread.run_sync(
                ocr_service.scan_document, images, document_type
            )
        except ValueError as exc:
            code = str(exc)
            raise ValidationException(
                ERROR_MESSAGES.get(code, "Hujjat o'qilmadi"), code
            )
        except Exception:  # noqa: BLE001
            logger.exception("Hujjat skanerlashda kutilmagan xato")
            raise HTTPException(status_code=500, detail="Hujjatni o'qishda xatolik")
