from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.application.services.auth_service import AuthService
from app.application.dto.auth import LoginRequest, RefreshRequest, TokenResponse, UserProfileResponse
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    service = AuthService(session)
    result = await service.login(
        username=data.username,
        password=data.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return result


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    data: RefreshRequest,
    session: AsyncSession = Depends(get_db),
):
    service = AuthService(session)
    result = await service.refresh_token(data.refresh_token)
    return result


@router.post("/logout", response_model=MessageResponse)
async def logout(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AuthService(session)
    await service.logout(current_user["jti"])
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AuthService(session)
    profile = await service.get_me(current_user["id"])
    return profile
