"""Dasturlar do'koni — mehmonxona tomoni.

Boshqaruv paneli yuklagan Android/Windows dasturlarini mehmonxona
ADMINISTRATORI shu yerdan ko'radi va yuklab oladi. Oddiy xodimga
ko'rinmaydi: o'rnatish fayllari — tizimni boshqarish vositasi, ularni
tarqatish administratorning ishi.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.app_store_service import AppStoreService
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.presentation.api.v1._deps import require_active_hotel
from app.presentation.middleware.auth import get_current_user

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _require_admin(current_user: dict) -> None:
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException(
            "Dasturlar do'koni administrator uchun", "ADMIN_ONLY"
        )


def _ascii_filename(name: str) -> str:
    """Content-Disposition uchun xavfsiz nom."""
    cleaned = "".join(
        ch if ch.isalnum() or ch in "._- " else "_" for ch in (name or "")
    ).strip()
    return cleaned or "app.bin"


def download_headers(meta: dict) -> dict:
    return {
        "Content-Disposition": (
            f'attachment; filename="{_ascii_filename(meta["original_name"])}"'
        ),
        "Content-Length": str(meta["file_size"]),
    }


@router.get("/")
async def list_apps(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Yuklab olish mumkin bo'lgan dasturlar ro'yxati."""
    _require_admin(current_user)
    return await AppStoreService(session).list()


@router.get("/{app_id}/download")
async def download_app(
    app_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Faylni oqim bilan beradi va yuklab olishlar sonini oshiradi."""
    _require_admin(current_user)
    service = AppStoreService(session)
    meta, chunks = await service.stream(app_id)
    # Hisob oqim boshlanishidan OLDIN yoziladi: javob uzilib qolsa ham
    # egasi "yuklashga urinishlar bo'lgan"ini ko'radi
    await service.record_download(app_id)
    return StreamingResponse(
        chunks, media_type=meta["mime_type"], headers=download_headers(meta)
    )
