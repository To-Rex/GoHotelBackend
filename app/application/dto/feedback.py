"""Talab, taklif va shikoyatlar — so'rov sxemalari."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

FeedbackTypeLiteral = Literal["REQUEST", "SUGGESTION", "COMPLAINT"]
FeedbackStatusLiteral = Literal["NEW", "IN_PROGRESS", "RESOLVED", "REJECTED"]
FeedbackPriorityLiteral = Literal["LOW", "MEDIUM", "HIGH"]


class FeedbackCreateRequest(BaseModel):
    feedback_type: FeedbackTypeLiteral
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)
    priority: FeedbackPriorityLiteral = "MEDIUM"
    # Bog'lamalar ixtiyoriy — bron berilsa mehmon va xona undan olinadi
    guest_id: UUID | None = None
    reservation_id: UUID | None = None
    room_id: UUID | None = None
    # Ro'yxatdan o'tmagan (yoki telefon qilgan) mehmon uchun matn
    guest_name: str | None = Field(default=None, max_length=200)
    guest_phone: str | None = Field(default=None, max_length=50)
    branch_id: UUID | None = None
    assigned_to: UUID | None = None


class FeedbackUpdateRequest(BaseModel):
    """PATCH — faqat yuborilgan maydonlar o'zgaradi (exclude_unset).

    `assigned_to: null` yuborilsa mas'ul olib tashlanadi; umuman
    yuborilmasa tegilmaydi — ikkalasi farqli.
    """

    feedback_type: FeedbackTypeLiteral | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    priority: FeedbackPriorityLiteral | None = None
    guest_id: UUID | None = None
    reservation_id: UUID | None = None
    room_id: UUID | None = None
    guest_name: str | None = Field(default=None, max_length=200)
    guest_phone: str | None = Field(default=None, max_length=50)
    assigned_to: UUID | None = None
    resolution: str | None = Field(default=None, max_length=5000)


class FeedbackStatusRequest(BaseModel):
    status: FeedbackStatusLiteral
    # Yopishda (RESOLVED/REJECTED) shart — servis tekshiradi
    resolution: str | None = Field(default=None, max_length=5000)
