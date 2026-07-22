from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)


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
    last_login_at: datetime | None
