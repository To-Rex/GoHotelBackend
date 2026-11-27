"""E-mehmon ro'yxatlari (listok) jadvali.

E-mehmon integratsiyasi IXTIYORIY: filial sozlamasida
(`branches.settings["emehmon"]`) yoqilmagan bo'lsa bu jadvalga hech narsa
yozilmaydi va tizim avvalgidek ishlaydi.

`emehmon_registrations` — bitta mehmonning bitta turishi uchun E-mehmon
ro'yxati. Bronda asosiy mehmon va hamrohlar bo'lsa — har biriga alohida
qator (E-mehmon'da ham har biriga alohida listok). Holatlar:

    sending     — so'rov yuborilmoqda (javob hali yozilmagan)
    registered  — E-mehmon ro'yxatga oldi (`listok_id`, `reg_num`)
    checked_out — E-mehmon'dan chiqarildi
    failed      — yuborilmadi yoki rad etildi (`error_code`, `error`)
    skipped     — xodim "kerak emas" deb belgiladi (masalan, kabinetda
                  qo'lda ro'yxatga olingan)

`uncertain` — natija noma'lum (tarmoq uzilgan): qayta yuborishda o'sha
`idempotency_key` ishlatiladi, shunda mehmon ikki marta yozilmaydi.

Batafsil: app/application/services/emehmon_service.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import BranchScoped, FullMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")
_BIGINT = BigInteger().with_variant(Integer(), "sqlite")


class EmehmonRegistration(BranchScoped, FullMixin, Base):
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "emehmon_registrations"
    __table_args__ = (
        Index("ix_emehmon_reg_reservation", "reservation_id", "guest_id"),
        Index("ix_emehmon_reg_branch_status", "branch_id", "status"),
        Index("ix_emehmon_reg_listok", "hotel_id", "listok_id"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    #: FK emas: bron o'chirilsa ham davlat ro'yxatiga yuborilgan yozuv qoladi
    reservation_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    #: main | companion
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="main")
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    passport_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    #: sandbox | production — qaysi muhitga yuborilgan
    environment: Mapped[str] = mapped_column(String(12), nullable=False, default="sandbox")
    #: sending | registered | checked_out | failed | skipped
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="sending")
    listok_id: Mapped[Optional[int]] = mapped_column(_BIGINT, nullable=True)
    reg_num: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    #: Xodim to'ldirgan shakl (tozalangan) — qayta to'ldirish va tekshiruv uchun
    form: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    #: E-mehmon javobi (`data`)
    response: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    body_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_meta: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    #: E-mehmon'ga yuborilgan kirish vaqti (`datevisiton`)
    arrived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    registered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # --- chiqarish (checkout) ---
    checked_out_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkout_sum: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    checkout_payment_type: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: Bo'sh — avtomatik chiqarilgan
    checkout_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    checkout_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    checkout_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkout_tried_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkout_error_code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    checkout_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: E-mehmon qaytargan chiqish vaqti (`dateVisitOff`)
    date_visit_off: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
