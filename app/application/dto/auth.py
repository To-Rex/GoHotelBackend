from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)
    # Firebase Cloud Messaging device token (ixtiyoriy). Push notification uchun saqlanadi.
    fcm_token: str | None = Field(default=None, max_length=4096)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserProfileResponse(BaseModel):
    id: UUID
    user_type: str
    hotel_id: UUID | None
    # Mehmonxona nomi — frontend brauzer tab sarlavhasida ko'rsatiladi
    hotel_name: str | None = None
    branch_id: UUID | None
    username: str
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    status: str
    permissions: list[str] = []
    # Ish jadvali — frontend navbarda ish tugashiga qancha qolganini ko'rsatadi
    work_hours_per_day: int = 8
    work_start: str = "09:00"
    work_end: str = "18:00"
    last_login_at: datetime | None
