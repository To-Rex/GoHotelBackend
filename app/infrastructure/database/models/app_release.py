from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class AppRelease(UUIDPrimaryKeyMixin, Base):
    """Dasturlar do'konidagi bitta fayl (Android APK yoki Windows dastur).

    Boshqaruv paneli egasi yuklaydi, mehmonxona administratorlari esa
    o'z tizimidan yuklab oladi. Fayllar MinIO'da yotadi — bazada faqat
    tavsif va manzil.

    Jadval GLOBAL: mehmonxonaga bog'lanmagan, chunki dastur hamma
    mehmonxona uchun bitta.
    """

    __tablename__ = "app_releases"

    #: ANDROID yoki WINDOWS.
    platform: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    #: Nima o'zgargani — administratorga ko'rinadi.
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    minio_bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    minio_path: Mapped[str] = mapped_column(String(500), nullable=False)

    #: Necha marta yuklab olingan — egasiga qaysi versiya tarqalganini aytadi.
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("panel_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
