from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models.hotel import Hotel
from app.presentation.middleware.auth import get_current_user


async def require_active_hotel(
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    if current_user.get("user_type") == "SUPER_ADMIN":
        return
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    hotel = await session.get(Hotel, hotel_id)
    if not hotel:
        raise ForbiddenException("Hotel not found")
    if hotel.status != "ACTIVE":
        raise ForbiddenException(
            f"Hotel is {hotel.status}. Operations are blocked for non-active hotels."
        )
