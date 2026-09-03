from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class IncomingCall(UUIDPrimaryKeyMixin, Base):
    """Qabulxona telefoniga kelgan qo'ng'iroq.

    Mehmon qo'ng'iroq qilganda resepsiya qurilmasi raqamni yuboradi va
    tizim uni bazadan qidiradi. Natija veb ekranidagi menyuda ko'rinadi —
    xodim gaplashayotgan odam kimligini gapirib berishini kutmasdan
    biladi.

    Nima uchun ALOHIDA jadval: yuz tanish yozuvi (`face_sightings`)
    kameraga bog'langan — unda `track_uid`, `similarity`, `embedding`
    bor. Qo'ng'iroqda bularning hech biri yo'q, ularni bo'sh qoldirib
    o'sha jadvalga tiqish esa ikkala hodisani ham tushunarsiz qilardi.
    """

    __tablename__ = "incoming_calls"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hotels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Qurilma ko'rsatgan raqam — asl ko'rinishida saqlanadi.
    phone: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Faqat raqamlar. Qidiruv va takrorni aniqlash shu ustun bo'yicha:
    #: "+998 90 123 45 67" va "998901234567" bir xil qo'ng'iroq.
    phone_digits: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    #: Topilgan mehmon. Topilmasa null — bu ham foydali yozuv:
    #: yangi mijoz qo'ng'iroq qilgan bo'lishi mumkin.
    guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guests.id", ondelete="SET NULL"), nullable=True
    )
    #: Nom NUSXA bo'lib saqlanadi: mehmon keyin o'chirilsa ham qo'ng'iroq
    #: tarixi "kim edi" degan savolga javob berishi kerak.
    guest_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    #: Qo'ng'iroq paytidagi faol bron (bo'lsa) va xona raqami.
    reservation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reservations.id", ondelete="SET NULL"),
        nullable=True,
    )
    room_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    #: Qaysi qurilma xabar berdi va kim kirgan edi.
    device_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reported_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    #: Xodim ko'rib, yopgan payt — menyuda qayta ko'rinmasligi uchun.
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
