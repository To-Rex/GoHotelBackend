from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class FaceSighting(Base):
    """Kamera ko'rgan bitta odam — qabulxona paneli uchun hodisa.

    Bir yozuv = bir track = bir odamning kamera oldida turgan bir epizodi,
    o'nlab kadr emas: agent kadrlarni o'zida guruhlab, faqat yakuniy shablonni
    yuboradi.

    ``embedding`` ataylab saqlanadi: tanilmagan odam keyin bronga biriktirilsa,
    o'sha vektordan darhol shablon yasaladi — mehmonni qayta suratga olish
    shart emas. ``thumbnail`` panelda yuzni ko'rsatish uchun; ikkalasi ham
    ``expires_at`` bilan avtomatik tozalanadi, ya'ni biometrik iz cheksiz
    yotmaydi.
    """

    __tablename__ = "face_sightings"
    __table_args__ = (
        # Panel so'rovi: "shu mehmonxonada, oxirgi N daqiqada, yangidan eskiga".
        Index("ix_face_sightings_hotel_seen", "hotel_id", "seen_at"),
        # Yangi mehmonga yuz biriktirishda so'rov filial bo'yicha toraytiriladi
        # — bu eng ko'p ishlatiladigan yo'l, va yuqoridagi indeks filialni
        # qamramaydi.
        Index("ix_face_sightings_branch_seen", "branch_id", "seen_at"),
        Index("ix_face_sightings_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), nullable=True
    )

    # -- kamera tomonidan berilgan kontekst ----------------------------
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False)
    camera_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    #: Agentdagi track UUID — bitta epizodning global yagona identifikatori.
    #: Takroriy yetkazib berish (offline navbat qayta yuborishi) shu bo'yicha
    #: aniqlanadi, shuning uchun u UNIQUE.
    track_uid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    capture_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # -- tanish natijasi ------------------------------------------------
    #: "recognized" | "uncertain" | "unknown" | "low_quality"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("guests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    similarity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Eng yaxshi ball bilan BOSHQA mehmonning eng yaxshi bali orasidagi farq.
    #: Kichik margin — "ikki odam bir xil darajada o'xshash", ya'ni ishonchsiz.
    margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cohesion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    #: Tanilmagan odamni keyin biriktirish uchun saqlanadigan shablon.
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    #: Panelda ko'rsatiladigan kichik JPEG (odatda 4-25 KB).
    thumbnail: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    # -- qabulxona bilan o'zaro ta'sir -----------------------------------
    #: Xodim ko'rib chiqdi (panelda "o'qilgan" bo'ldi).
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Shu ko'rinishdan boshlangan bron (panel -> bron dialogi).
    reservation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True
    )

    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_match(self) -> bool:
        return self.status == "recognized" and self.guest_id is not None


class VisionDevice(Base):
    """Kamera agenti o'rnatilgan kompyuter — foydalanuvchi emas, qurilma.

    Agent xodim tokeni bilan ishlay olmaydi: JWT ikki soatda tugaydi, agent
    esa oylab uzluksiz turadi. Shuning uchun unga alohida, muddatsiz qurilma
    tokeni beriladi.

    Token bazada OCHIQ saqlanmaydi — faqat SHA-256 xesh. Xeshlash bcrypt emas,
    chunki bu token har hodisada tekshiriladi (sekundiga bir necha marta), va
    tasodifiy 32 baytli tokenda bcrypt'ning lug'at hujumiga qarshi sekinligi
    hech narsa qo'shmaydi.

    ``hotel_id`` — eng muhim maydon: qidiruv doirasi shu yerdan keladi, ya'ni
    bir mehmonxona agenti boshqasining mehmonlarini hech qachon ko'rmaydi.
    """

    __tablename__ = "vision_devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Agent o'zini shu nom bilan tanitadi (odatda kompyuter hostname'i).
    device_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    #: Tokenning oxirgi 4 belgisi — ro'yxatda qaysi token ekanini ajratish uchun.
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    events_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
