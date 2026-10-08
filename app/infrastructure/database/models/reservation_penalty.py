from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin, BranchScoped


class ReservationPenalty(BranchScoped, UUIDPrimaryKeyMixin, Base):
    """Bron bo'yicha jarima: kech chiqish, shikast (buzilgan narsa) yoki boshqa.

    Jarima bron summasiga DARHOL qo'shiladi (`reservations.penalty_amount`)
    va hisob-fakturada alohida "PENALTY" qatori bo'ladi — to'lanmasa qarz
    bo'lib ko'rinadi va mavjud "qo'shimcha to'lov" oqimi bilan olinadi.

    O'chirilmaydi: xato kiritilgani BEKOR qilinadi (`voided_at`, kim va
    nima uchun) — kim qachon qancha jarima yozgani doim ko'rinib turadi.
    Qoidalar: application/services/reservation_penalty_service.py
    """

    __tablename__ = "reservation_penalties"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_reservation_penalties_amount"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reservations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: LATE_CHECKOUT / DAMAGE / OTHER
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Hisobot kuni — to'lovlardagi `payment_date` bilan bir xil tartibda
    penalty_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    voided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
