from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Numeric, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class RoomType(FullMixin, Base):
    __tablename__ = "room_types"
    __table_args__ = (
        UniqueConstraint("name", name="uq_room_types_name"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capacity: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    base_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    amenities: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    hotels: Mapped[list["Hotel"]] = relationship(
        "Hotel",
        secondary="hotel_room_types",
        back_populates="room_types",
    )
    rooms: Mapped[list["Room"]] = relationship("Room", back_populates="room_type")


class HotelRoomType(Base):
    __tablename__ = "hotel_room_types"
    __table_args__ = (
        UniqueConstraint("hotel_id", "room_type_id", name="uq_hotel_room_types"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    room_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("room_types.id", ondelete="CASCADE"), primary_key=True
    )
