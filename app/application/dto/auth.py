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


class LoginResponse(BaseModel):
    """Parol tekshiruvi natijasi.

    Ikki xil bo'lishi mumkin va bu ataylab bitta javobda: xodim yuz
    biriktirgan bo'lsa tokenlar BERILMAYDI, o'rniga ikkinchi bosqich uchun
    qisqa muddatli `face_token` qaytadi. Yuzi yo'q xodim uchun javob
    avvalgidek — tokenlar bilan, ya'ni eski klientlar ishlayveradi.
    """

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None

    #: True bo'lsa kirish hali tugallanmagan
    face_required: bool = False
    face_token: str | None = None
    face_expires_in: int | None = None


class FaceSkipRequest(BaseModel):
    """Qurilmada kamera yo'q bo'lganda ikkinchi bosqichni o'tkazib yuborish."""

    face_token: str
    reason: str | None = None


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
