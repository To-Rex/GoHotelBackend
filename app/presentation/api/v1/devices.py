"""Qurilmalarni tasdiqlash — administrator uchun.

Xodim faqat shu ro'yxatda APPROVED holatida turgan qurilmadan kira oladi.
Batafsil izoh `device_service` da.
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.device_service import DeviceService
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.presentation.middleware.auth import get_current_user

router = APIRouter()


class DeviceResponse(BaseModel):
    id: UUID
    device_id: str
    label: str | None = None
    status: str
    user_agent: str | None = None
    ip_address: str | None = None
    last_user_id: UUID | None = None
    approved_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime | None = None

    model_config = {"from_attributes": True}


class DeviceStatusRequest(BaseModel):
    status: str
    label: str | None = None


def _admin_hotel_id(current_user: dict) -> UUID:
    """Faqat administrator qurilmalarni boshqaradi.

    Bu ro'yxat kirish huquqini beradi, ya'ni uni tahrirlash tizimga kirish
    huquqini tarqatish bilan barobar — oddiy xodimga berilmaydi.
    """
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException("Faqat administrator uchun", "ADMIN_ONLY")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


@router.get("/", response_model=list[DeviceResponse])
async def list_devices(
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _admin_hotel_id(current_user)
    return await DeviceService(session).list_devices(h_id, status=status)


@router.patch("/{device_pk}", response_model=DeviceResponse)
async def set_device_status(
    data: DeviceStatusRequest,
    device_pk: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Tasdiqlash, taqiqlash yoki nomini o'zgartirish."""
    h_id = _admin_hotel_id(current_user)
    return await DeviceService(session).set_status(
        device_pk, h_id, data.status, current_user["id"], label=data.label
    )


@router.delete("/{device_pk}")
async def delete_device(
    device_pk: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ro'yxatdan o'chirish — keyingi urinishda qurilma yangi sifatida qaytadi."""
    h_id = _admin_hotel_id(current_user)
    await DeviceService(session).delete_device(device_pk, h_id)
    return {"message": "Qurilma o'chirildi"}
