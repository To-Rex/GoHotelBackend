from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class GuestFaceProfile(Base):
    """Mehmonning yuz shabloni — keyingi tashrifda uni tanib olish uchun.

    Rasm SAQLANMAYDI: faqat yuzdan hisoblangan embedding saqlanadi, xuddi
    xodimlar uchun ishlatiladigan [UserFaceProfile] kabi. Farqi ikkitada:

    1. Vektor JSON matn emas, **paketlangan float32** (``LargeBinary``, 512
       bayt). 1:N qidiruvda har qatorni ``json.loads`` qilish minglab profilda
       soniyalarga aylanadi; ``np.frombuffer`` esa nusxasiz va bir zumda
       ishlaydi. Butun mehmonxona indeksi bitta matritsaga yig'iladi.
    2. ``hotel_id`` alohida ustun bo'lib turibdi (mehmon orqali ham
       topilardi). Qidiruv indeksi HAR DOIM mehmonxona bo'yicha quriladi, va
       bu ustun bo'lmasa har indeks yangilanishida ``guests`` bilan JOIN
       kerak bo'lardi.

    Bir mehmonga bir nechta shablon saqlanadi (turli yorug'lik va burchak) —
    bu 1:N aniqligini sezilarli oshiradi.
    """

    __tablename__ = "guest_face_profiles"
    __table_args__ = (
        Index("ix_guest_face_profiles_hotel_guest", "hotel_id", "guest_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    guest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: L2-normallashtirilgan float32 vektor, little-endian. 128 o'lcham = 512 bayt.
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dim: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=128)
    #: Vektorni ishlab chiqargan model. Model almashsa eski vektorlar
    #: solishtirib bo'lmaydigan bo'lib qoladi — indeks ularni chetlab o'tadi.
    model: Mapped[str] = mapped_column(String(32), nullable=False, default="sface_2021dec")

    #: Shablon nechta kadrdan yig'ilgan va ular bir-biriga qanchalik o'xshash.
    #: Past kogeziya — kadrlar orasida boshqa odam bo'lgan degani.
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cohesion: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    #: "vision" — kameradan avtomatik, "manual" — xodim qo'lda biriktirgan.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="vision")
    camera_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_matched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
