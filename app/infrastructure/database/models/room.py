from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Numeric, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import RoomStatus
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, SoftDeleteMixin


class Room(FullMixin, SoftDeleteMixin, Base):
    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("branch_id", "room_number", name="uq_rooms_branch_number"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    floor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("floors.id", ondelete="RESTRICT"), nullable=False
    )
    room_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("room_types.id", ondelete="RESTRICT"), nullable=False
    )
    room_number: Mapped[str] = mapped_column(String(20), nullable=False)
    base_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    capacity: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True, default=1)
    current_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RoomStatus.AVAILABLE.value
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="rooms")
    branch: Mapped["Branch"] = relationship("Branch", back_populates="rooms")
    floor: Mapped["Floor"] = relationship("Floor", back_populates="rooms")
    room_type: Mapped["RoomType"] = relationship("RoomType", back_populates="rooms")
    status_history: Mapped[list["RoomStatusHistory"]] = relationship(
        "RoomStatusHistory", back_populates="room"
    )
    reservations: Mapped[list["Reservation"]] = relationship(
        "Reservation", back_populates="room"
    )
    housekeeping_tasks: Mapped[list["HousekeepingTask"]] = relationship(
        "HousekeepingTask", back_populates="room"
    )
    amenities: Mapped[list["Amenity"]] = relationship(
        "Amenity",
        secondary="room_amenities",
        back_populates="rooms",
    )
