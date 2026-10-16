from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin, BranchScoped


class Payment(BranchScoped, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("hotel_id", "payment_number", name="uq_payments_hotel_number"),
        CheckConstraint("amount > 0", name="ck_payments_amount"),
        # Moliyaviy hisobotlar: hotel + sana oralig'i
        Index("ix_payments_hotel_payment_date", "hotel_id", "payment_date"),
        # Smena kassasi / shaxsiy hisobot: xodim + vaqt oynasi
        Index("ix_payments_created_by_created_at", "created_by", "created_at"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    payment_number: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    # To'lov TURINI tahrirlash tarixi (audit): [{old, new, by, by_name, at,
    # note}, ...]. Summa va boshqa maydonlar o'zgarmaydi; faqat admin/super
    # admin (cheklovsiz) va menejer (oxirgi 2 kun) tahrirlay oladi.
    method_changes: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="payments")
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payments")
    created_by_user: Mapped["User"] = relationship(
        "User",
        back_populates="created_payments",
        foreign_keys=[created_by],
    )
