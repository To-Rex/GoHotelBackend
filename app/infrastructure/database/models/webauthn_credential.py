from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class WebAuthnCredential(FullMixin, Base):
    """Foydalanuvchi ro'yxatdan o'tkazgan passkey (Face ID/Windows Hello/Touch ID)."""

    __tablename__ = "webauthn_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Autentifikator qaytargan kredensial ID (base64url) — WebAuthn javobidagi
    # credential.id bilan bir xil, login vaqtida foydalanuvchini aniqlash uchun.
    credential_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    device_type: Mapped[str] = mapped_column(String(20), nullable=False)
    backed_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Ro'yxatdan o'tish paytidagi User-Agent asosida chiqarilgan qurilma nomi
    device_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship("User")
