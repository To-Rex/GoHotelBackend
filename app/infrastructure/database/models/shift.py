from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class ShiftSession(FullMixin, Base):
    """Xodim smenasi + kassa sessiyasi (kassali rejimda).

    Pul kassaning emas, SESSIYANING hisobida: har bir xodim o'z sessiyasidagi
    naqd pulga javob beradi. Sessiya yopilishida "ko'r sanash" qo'llanadi —
    xodim avval haqiqiy pulni kiritadi, kutilgan summa keyin ochiladi.

    Holatlar:
      ACTIVE            — xodim ishlamoqda
      PENDING_HANDOVER  — smena tugallandi, keyingi xodim qabul qilishi kutilmoqda
      CLOSED            — yopilgan (oddiy, qabul qilingan yoki majburiy)
    """

    __tablename__ = "shift_sessions"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE", server_default="ACTIVE", index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Kassa: boshlang'ich, kutilgan (hisoblangan), sanab kiritilgan va farq
    opening_cash: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, server_default="0"
    )
    expected_cash: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    counted_cash: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    cash_diff: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)

    # Ish vaqti tugagach "Davom etish" bosilgan (keyingi xodim kelmadi)
    continue_after_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Admin/menejer tomonidan majburiy yopilgan
    force_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    closed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Tuzatishlar tarixi (audit): [{old/new qiymatlar, kim, qachon, izoh}, ...]
    corrections: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
