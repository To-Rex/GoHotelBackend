from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin, BranchScoped


class Notification(BranchScoped, UUIDPrimaryKeyMixin, Base):
    # Eslatma: o'qilmaganlar uchun indeks bazada mavjud
    # (idx_notifications_user_unread) — bu yerda takrorlanmaydi.
    __tablename__ = "notifications"
    __table_args__ = (
        # Xodimning o'z bildirishnomalari (o'qilgan/o'qilmagani aralash)
        Index("ix_notifications_user_created", "user_id", "created_at"),
        # Mehmonxona bo'yicha umumiy ro'yxat (admin)
        Index("ix_notifications_hotel_created", "hotel_id", "created_at"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    hotel: Mapped["Hotel"] = relationship("Hotel")
    user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[user_id])
