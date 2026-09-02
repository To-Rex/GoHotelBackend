from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class TrustedDevice(UUIDPrimaryKeyMixin, Base):
    """Tizimga kirishga ruxsat berilgan qurilma.

    Nega kerak: login va parol o'g'irlansa, ular istalgan kompyuterdan
    ishlayverardi. Endi xodim faqat administrator tasdiqlagan qurilmadan
    kira oladi — begona qurilmadan urinish ro'yxatga tushadi va kutib
    qoladi.

    Qurilma brauzer yaratgan tasodifiy ID bilan tanilади (`device_id`). Bu
    mukammal emas: ID nusxalanishi yoki brauzer ma'lumoti tozalanganda
    yo'qolishi mumkin. Lekin u parol bilan BIRGA ishlaydi — o'g'ri
    ikkalasini ham qo'lga kiritishi kerak bo'ladi. Qurilmani chindan
    bog'lash uchun passkey (WebAuthn) bor, u alohida.

    Administrator bu tekshiruvdan ozod: tasdiqlaydigan odamning o'zi
    tasdiqlangan qurilma kutib qolsa, tizimga hech kim kira olmay qolardi.
    """

    __tablename__ = "trusted_devices"
    __table_args__ = (
        # Bitta mehmonxonada bitta qurilma bir marta
        UniqueConstraint("hotel_id", "device_id", name="uq_trusted_devices_hotel_device"),
        Index("ix_trusted_devices_hotel_status", "hotel_id", "status"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    #: Brauzer saqlaydigan tasodifiy identifikator
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Xodim qo'yadigan nom ("Resepsiya kompyuteri")
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    #: PENDING — tasdiq kutmoqda, APPROVED — ruxsat, BLOCKED — taqiqlangan
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")

    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    #: Oxirgi marta shu qurilmadan kirishga uringan xodim — administrator
    #: ro'yxatda "kim so'rayapti" degan savolga javob ko'rishi uchun
    last_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    hotel: Mapped["Hotel"] = relationship("Hotel")
    last_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[last_user_id]
    )
    approver: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[approved_by]
    )
