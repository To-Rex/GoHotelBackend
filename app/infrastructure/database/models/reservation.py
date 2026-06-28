from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import BookingType, PaymentStatus, ReservationStatus
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, SoftDeleteMixin


class Reservation(FullMixin, SoftDeleteMixin, Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("check_out_date > check_in_date", name="ck_reservations_dates"),
        CheckConstraint("adults >= 1", name="ck_reservations_adults"),
        CheckConstraint("children >= 0", name="ck_reservations_children"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    reservation_number: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True
    )
    guest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guests.id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    booking_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BookingType.DAILY.value
    )
    check_in_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_out_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_datetime: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    check_out_datetime: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    adults: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    children: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReservationStatus.PENDING.value
    )
    total_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    paid_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    payment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentStatus.UNPAID.value
    )
    discount_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    discount_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    cancelled_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="reservations")
    branch: Mapped["Branch"] = relationship("Branch", back_populates="reservations")
    guest: Mapped["Guest"] = relationship("Guest", back_populates="reservations")
    room: Mapped["Room"] = relationship("Room", back_populates="reservations")
    created_by_user: Mapped["User"] = relationship(
        "User",
        back_populates="created_reservations",
        foreign_keys=[created_by],
    )
    canceller: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="cancelled_reservations",
        foreign_keys=[cancelled_by],
    )
    invoice: Mapped[Optional["Invoice"]] = relationship(
        "Invoice", back_populates="reservation", uselist=False
    )
    reservation_services: Mapped[list["ReservationService"]] = relationship(
        "ReservationService", back_populates="reservation"
    )
