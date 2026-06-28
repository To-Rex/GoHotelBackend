from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import BranchStatus
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class Branch(FullMixin, Base):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("hotel_id", "code", name="uq_branches_hotel_code"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    address_line1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_main_branch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BranchStatus.ACTIVE.value
    )

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="branches")
    floors: Mapped[list["Floor"]] = relationship("Floor", back_populates="branch")
    rooms: Mapped[list["Room"]] = relationship("Room", back_populates="branch")
    users: Mapped[list["User"]] = relationship("User", back_populates="branch")
    reservations: Mapped[list["Reservation"]] = relationship(
        "Reservation", back_populates="branch"
    )
    housekeeping_tasks: Mapped[list["HousekeepingTask"]] = relationship(
        "HousekeepingTask", back_populates="branch"
    )
