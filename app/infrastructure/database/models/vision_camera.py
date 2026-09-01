from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class VisionCamera(Base):
    """Bitta kamera va u qaysi filialda turishi.

    Nega qurilma (kompyuter) darajasida yetmaydi: bitta agent bir nechta
    kamerani boqishi mumkin, va ular turli filiallarda bo'lishi mumkin. Agar
    filial faqat qurilmada saqlansa, o'sha agentning barcha kameralari bitta
    filialga yozilardi — qabulxona xodimi esa yonidagi filialning odamlarini
    ko'rib qolardi.

    Yozuvlar **avtomatik paydo bo'ladi**: noma'lum ``camera_id`` bilan hodisa
    kelsa, u shu yerga qurilmaning filiali bilan qo'shiladi. Rad etish
    xavfsizroq tuyuladi, lekin amalda yangi kamera ulanganda hodisalar
    jimgina yo'qolishiga olib kelardi; ro'yxatda paydo bo'lishi esa
    administratorga uni ko'rish va to'g'ri filialga biriktirish imkonini
    beradi.

    ``is_active`` — o'chirilgan kamera hodisalari qabul qilinmaydi. Bu
    tokenni bekor qilmasdan bitta kamerani vaqtincha to'xtatish yo'li.
    """

    __tablename__ = "vision_cameras"
    __table_args__ = (
        # Kamera identifikatori qurilma ichida noyob (agent konfiguratsiyasida
        # `cameras[].id`), lekin turli qurilmalarda takrorlanishi mumkin —
        # masalan ikki filialda ham "lobby" nomli kamera bo'lishi tabiiy.
        UniqueConstraint("device_id", "camera_id", name="uq_vision_cameras_device_camera"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Kamera qaysi filialda turibdi. Bo'sh bo'lsa — administrator hali
    #: biriktirmagan; bunday kameraning suratlari filial bo'yicha filtrlangan
    #: ro'yxatlarda ko'rinmaydi.
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vision_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Agent konfiguratsiyasidagi `cameras[].id`.
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sightings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def is_assigned(self) -> bool:
        return self.branch_id is not None
