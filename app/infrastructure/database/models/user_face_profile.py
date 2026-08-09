from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class UserFaceProfile(Base):
    """Xodimning yuz profili — yuz bilan kirish uchun.

    Rasm SAQLANMAYDI: faqat yuzdan hisoblangan embedding (raqamli vektor,
    JSON ro'yxat) saqlanadi. Kirishda kameradan olingan kadr embeddingi shu
    vektorlar bilan solishtiriladi (kosinus o'xshashligi).
    """

    __tablename__ = "user_face_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding: Mapped[str] = mapped_column(Text, nullable=False)
    device_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
