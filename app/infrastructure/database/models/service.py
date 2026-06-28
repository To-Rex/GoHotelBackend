from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, UUIDPrimaryKeyMixin


class Service(FullMixin, Base):
    __tablename__ = "services"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    hotel_services: Mapped[list["HotelService"]] = relationship(
        "HotelService", back_populates="service"
    )


class HotelService(FullMixin, Base):
    __tablename__ = "hotel_services"
    __table_args__ = (
        UniqueConstraint("hotel_id", "service_id", name="uq_hotel_services_hotel_svc"),
        CheckConstraint("price >= 0", name="ck_hotel_services_price"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="hotel_services")
    service: Mapped["Service"] = relationship("Service", back_populates="hotel_services")
    reservation_services: Mapped[list["ReservationService"]] = relationship(
        "ReservationService", back_populates="hotel_service"
    )


class ReservationService(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "reservation_services"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_reservation_services_qty"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reservations.id", ondelete="CASCADE"), nullable=False
    )
    hotel_service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotel_services.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    total_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="reservation_services")
    reservation: Mapped["Reservation"] = relationship(
        "Reservation", back_populates="reservation_services"
    )
    hotel_service: Mapped["HotelService"] = relationship(
        "HotelService", back_populates="reservation_services"
    )
