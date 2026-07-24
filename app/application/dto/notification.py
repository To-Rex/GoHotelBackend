from uuid import UUID

from pydantic import BaseModel, Field


class NotificationSendRequest(BaseModel):
    """Bitta foydalanuvchiga push notification yuborish so'rovi."""

    user_id: UUID
    title: str = Field(..., min_length=1, max_length=255)
    body: str | None = Field(default=None, max_length=2000)
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: UUID | None = None


class NotificationBroadcastRequest(BaseModel):
    """Mehmonxonadagi barcha foydalanuvchilarga push notification yuborish so'rovi."""

    title: str = Field(..., min_length=1, max_length=255)
    body: str | None = Field(default=None, max_length=2000)
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: UUID | None = None


class NotificationSendResponse(BaseModel):
    success: bool
    notification_id: UUID
    # Nechta qurilmaga (FCM token) push muvaffaqiyatli yuborildi
    push_sent: int = 0


class TestPushRequest(BaseModel):
    """Sinov uchun: FCM tokenga to'g'ridan-to'g'ri push yuborish (avtorizatsiyasiz)."""

    fcm_token: str = Field(..., min_length=10, max_length=4096)
    title: str = Field(default="Test", min_length=1, max_length=255)
    body: str | None = Field(default="Bu sinov xabari", max_length=2000)
    data: dict[str, str] | None = None


class TestPushResponse(BaseModel):
    success: bool
    # 1 = yuborildi, 0 = yuborilmadi (token yaroqsiz yoki Firebase o'chiq)
    push_sent: int = 0
