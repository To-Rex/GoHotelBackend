from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class Amenity(FullMixin, Base):
    __tablename__ = "amenities"
    __table_args__ = (
        UniqueConstraint("name", name="uq_amenities_name"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    hotels: Mapped[list["Hotel"]] = relationship(
        "Hotel",
        secondary="hotel_amenities",
        back_populates="amenities",
    )
    rooms: Mapped[list["Room"]] = relationship(
        "Room",
        secondary="room_amenities",
        back_populates="amenities",
    )


class HotelAmenity(Base):
    __tablename__ = "hotel_amenities"
    __table_args__ = (
        UniqueConstraint("hotel_id", "amenity_id", name="uq_hotel_amenities"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    amenity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("amenities.id", ondelete="CASCADE"), primary_key=True
    )


class RoomAmenity(Base):
    __tablename__ = "room_amenities"
    __table_args__ = (
        UniqueConstraint("room_id", "amenity_id", name="uq_room_amenities"),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    amenity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("amenities.id", ondelete="CASCADE"), primary_key=True
    )
