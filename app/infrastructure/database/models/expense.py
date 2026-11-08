from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import BranchScoped, FullMixin


class Expense(BranchScoped, FullMixin, Base):
    """Mehmonxona xarajati (chiqim) — Xarajatlar sahifasidan kiritiladi.

    Kim kiritgani (created_by) majburiy saqlanadi va ro'yxatda ko'rsatiladi.
    Moliya hisobotlarida tushumdan ayirilib "sof natija" hisoblanadi.
    """

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_expenses_amount"),
        # Moliyaviy hisobotlar: hotel + kun filtri
        Index("ix_expenses_hotel_expense_date", "hotel_id", "expense_date"),
        # Smena kassasi: xodim + vaqt oynasi
        Index("ix_expenses_created_by_created_at", "created_by", "created_at"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="CASH", server_default="CASH"
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: Qisman (aralash) to'lov: bitta xarajat bir necha to'lov turiga
    #: bo'linganda har bo'lak alohida qator bo'lib, shu kalit bilan
    #: bog'lanadi (expense_service). Oddiy xarajatda bo'sh. Hisobotlar
    #: qatorlar bo'yicha ishlayveradi — har bo'lakning o'z to'lov turi bor.
    split_group: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    created_by_user: Mapped["User"] = relationship("User", foreign_keys=[created_by])
