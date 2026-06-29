from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint, String, Text, and_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, foreign

from app.domain.enums import HotelStatus
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class Hotel(FullMixin, Base):
    __tablename__ = "hotels"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stars: Mapped[int] = mapped_column(
        CheckConstraint("stars >= 1 AND stars <= 5", name="ck_hotels_stars"),
        nullable=False,
    )
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=HotelStatus.ACTIVE.value
    )
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    branches: Mapped[list["Branch"]] = relationship(
        "Branch", back_populates="hotel"
    )
    room_types: Mapped[list["RoomType"]] = relationship(
        "RoomType",
        secondary="hotel_room_types",
        back_populates="hotels",
    )
    rooms: Mapped[list["Room"]] = relationship(
        "Room", back_populates="hotel"
    )
    users: Mapped[list["User"]] = relationship(
        "User", back_populates="hotel"
    )
    employees: Mapped[list["User"]] = relationship(
        "User",
        primaryjoin="and_(Hotel.id == foreign(User.hotel_id), User.user_type == 'EMPLOYEE')",
        viewonly=True,
    )
    guests: Mapped[list["Guest"]] = relationship(
        "Guest", back_populates="hotel"
    )
    reservations: Mapped[list["Reservation"]] = relationship(
        "Reservation", back_populates="hotel"
    )
    invoices: Mapped[list["Invoice"]] = relationship(
        "Invoice", back_populates="hotel"
    )
    housekeeping_tasks: Mapped[list["HousekeepingTask"]] = relationship(
        "HousekeepingTask", back_populates="hotel"
    )
    hotel_services: Mapped[list["HotelService"]] = relationship(
        "HotelService", back_populates="hotel"
    )
    reservation_services: Mapped[list["ReservationService"]] = relationship(
        "ReservationService", back_populates="hotel"
    )
    ledgers: Mapped[list["Ledger"]] = relationship(
        "Ledger", back_populates="hotel"
    )
    journal_entries: Mapped[list["JournalEntry"]] = relationship(
        "JournalEntry", back_populates="hotel"
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="hotel"
    )
    amenities: Mapped[list["Amenity"]] = relationship(
        "Amenity",
        secondary="hotel_amenities",
        back_populates="hotels",
    )
    buildings: Mapped[list["Building"]] = relationship(
        "Building", back_populates="hotel"
    )
