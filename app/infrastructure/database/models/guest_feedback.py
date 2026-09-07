from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import FeedbackPriority, FeedbackStatus
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, SoftDeleteMixin


class GuestFeedback(FullMixin, SoftDeleteMixin, Base):
    """Mehmon murojaati — talab (REQUEST), taklif (SUGGESTION) yoki shikoyat
    (COMPLAINT). Xizmat ko'rsatish joyidagi "Taklif va shikoyatlar kitobi"ning
    elektron ko'rinishi: qabulxona mehmon aytganini yozadi, menejer ko'rib
    chiqib javob beradi va yopadi.

    Mehmon, bron va xona bog'lamalari ixtiyoriy — murojaat telefon orqali yoki
    ro'yxatdan o'tmagan odamdan kelishi mumkin, shunda ism/telefon matn
    sifatida yoziladi. Bog'langan bo'lsa ham ism va xona raqami ALOHIDA
    nusxalanadi (guest_name, room_number): mehmon o'chirilsa yoki xona
    qayta nomlansa kitobdagi yozuv o'z holicha qoladi — bu hujjat, jonli
    ko'rinish emas.
    """

    __tablename__ = "guest_feedback"
    __table_args__ = (
        # Sahifa doim "shu mehmonxona + holat" bo'yicha so'raydi
        Index("ix_guest_feedback_hotel_status", "hotel_id", "status"),
        Index("ix_guest_feedback_created_at", "created_at"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), nullable=True
    )
    guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("guests.id", ondelete="SET NULL"), nullable=True
    )
    reservation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True
    )
    room_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True
    )

    feedback_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FeedbackStatus.NEW.value
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FeedbackPriority.MEDIUM.value
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Nusxa maydonlar — bog'lama bo'lmasa qo'lda yoziladi
    guest_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    room_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Mas'ul xodim va yakuniy javob
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    guest: Mapped[Optional["Guest"]] = relationship("Guest")
    room: Mapped[Optional["Room"]] = relationship("Room")
    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation")
    assigned_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[assigned_to]
    )
    resolved_by_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[resolved_by]
    )
    created_by_user: Mapped["User"] = relationship("User", foreign_keys=[created_by])
