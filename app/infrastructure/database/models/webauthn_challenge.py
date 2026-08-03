from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class WebAuthnChallenge(UUIDPrimaryKeyMixin, Base):
    """Ro'yxatdan o'tish/kirish seremoniyasi uchun bir martalik challenge.

    Tekshirilgach yoki muddati o'tgach o'chiriladi (register_verify/login_verify
    ichida). purpose: "register" yoki "login".
    """

    __tablename__ = "webauthn_challenges"

    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    challenge: Mapped[str] = mapped_column(String(255), nullable=False)
    # Login uchun username berilmasa (discoverable/passkey oqimi) bo'sh qoladi
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
