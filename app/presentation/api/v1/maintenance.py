"""Texnik xizmat endpointlari — ma'lumotlarni tozalash (reset).

Faqat ADMIN va SUPER_ADMIN uchun. Barcha amallar bitta mehmonxona doirasida
bajariladi (boshqa mehmonxonalarga ta'sir yo'q). Tasodifiy chaqiruvdan
himoya: so'rovda confirm="RESET" bo'lishi shart.
"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.configurator_access import assert_can_manage_settings
from app.core.database import get_db
from app.core.exceptions import ForbiddenException, ValidationException
from app.application.services.maintenance_service import MaintenanceService
from app.presentation.middleware.auth import get_current_user

router = APIRouter()


class ResetDataRequest(BaseModel):
    # operational — bronlar/moliya/mehmonlar/vazifalar tozalanadi, xodimlar qoladi
    # full — qo'shimcha ravishda barcha EMPLOYEE xodimlar ham o'chiriladi
    scope: Literal["operational", "full"] = Field(default="operational")
    confirm: str


@router.post("/reset-data")
async def reset_data(
    data: ResetDataRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Bu huquq faqat sozlovchi va tizim ma'murida (Sozlamalar → Xavfli hudud)
    assert_can_manage_settings(current_user)

    # Tasodifiy chaqiruvdan himoya
    if data.confirm != "RESET":
        raise ValidationException(
            "Tasdiqlash uchun confirm maydonida 'RESET' yuborilishi shart",
            "CONFIRM_REQUIRED",
        )

    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = current_user.get("hotel_id")
    if not h_id:
        raise ForbiddenException("Hotel context required", "HOTEL_REQUIRED")

    service = MaintenanceService(session)
    # Faqat joriy filial (sozlovchi tanlagani) — boshqa filiallarga tegilmaydi
    branch_id = current_user.get("branch_id") if str(current_user.get("hotel_id")) == str(h_id) else None
    deleted = await service.reset_data(
        h_id, include_employees=(data.scope == "full"), branch_id=branch_id
    )

    return {
        "message": "Ma'lumotlar muvaffaqiyatli tozalandi",
        "scope": data.scope,
        "deleted": deleted,
        "total_deleted": sum(v for k, v in deleted.items() if k != "rooms_reset"),
    }
