"""Smena va kassa endpointlari.

Kassali rejim mehmonxona sozlamalarida yoqiladi (faqat ADMIN). Xodimlar
smenani ochadi, kassani topshiradi (ko'r sanash), smenani tugallaydi;
keyingi xodim parol bilan qabul qiladi; admin/menejer majburiy yopadi.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.shift_service import ShiftService
from app.presentation.middleware.auth import get_current_user
from app.presentation.api.v1._deps import require_active_hotel

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _hotel_id(current_user: dict) -> UUID:
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return UUID(str(hotel_id))


class ShiftSettingsRequest(BaseModel):
    mode: str = Field(..., pattern=r"^(simple|cash)$")
    day_close: str = Field(..., pattern=r"^\d{2}:\d{2}$")


class OpenShiftRequest(BaseModel):
    opening_cash: float = Field(default=0, ge=0)


class CashCountRequest(BaseModel):
    counted_cash: float = Field(..., ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class AcceptShiftRequest(BaseModel):
    password: str = Field(..., min_length=1)


class ForceCloseRequest(BaseModel):
    session_id: UUID
    counted_cash: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class CorrectShiftRequest(BaseModel):
    counted_cash: float = Field(..., ge=0)
    # Izoh majburiy — tuzatish sababi auditda qolishi shart
    note: str = Field(..., min_length=3, max_length=1000)


@router.get("/settings")
async def get_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).get_settings(_hotel_id(current_user))


@router.put("/settings")
async def save_settings(
    data: ShiftSettingsRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException("Faqat administrator rejimni o'zgartira oladi")
    return await ShiftService(session).save_settings(
        _hotel_id(current_user), data.mode, data.day_close
    )


@router.get("/state")
async def get_state(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).get_state(_hotel_id(current_user), current_user)


@router.get("/expected-cash")
async def expected_cash(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Faol sessiya kassasida bo'lishi kerak bo'lgan summa (tarkibi bilan)."""
    return await ShiftService(session).my_expected_cash(
        _hotel_id(current_user), current_user
    )


@router.post("/open")
async def open_shift(
    data: OpenShiftRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).open_session(
        _hotel_id(current_user), current_user, data.opening_cash
    )


@router.post("/continue")
async def continue_shift(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).continue_shift(_hotel_id(current_user), current_user)


@router.post("/close-cash")
async def close_cash(
    data: CashCountRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).close_cash(
        _hotel_id(current_user), current_user, data.counted_cash, data.notes
    )


@router.post("/end")
async def end_shift(
    data: CashCountRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).end_shift(
        _hotel_id(current_user), current_user, data.counted_cash, data.notes
    )


@router.post("/accept")
async def accept_shift(
    data: AcceptShiftRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).accept_shift(
        _hotel_id(current_user), current_user, data.password
    )


@router.post("/force-close")
async def force_close(
    data: ForceCloseRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).force_close(
        _hotel_id(current_user),
        current_user,
        data.session_id,
        data.counted_cash,
        data.notes,
    )


@router.put("/{session_id}/correct")
async def correct_shift(
    session_id: UUID,
    data: CorrectShiftRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Yopilgan sessiya sanalgan summasini tuzatish (admin/menejer, izoh shart)."""
    return await ShiftService(session).correct_session(
        _hotel_id(current_user),
        current_user,
        session_id,
        data.counted_cash,
        data.note,
    )


@router.get("/history")
async def get_history(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await ShiftService(session).get_history(
        _hotel_id(current_user), current_user, limit
    )
