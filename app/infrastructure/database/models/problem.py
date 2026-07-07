from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class Problem(FullMixin, Base):
    __tablename__ = "problems"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), nullable=True
    )
    room_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("housekeeping_tasks.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OPEN"
    )
    reported_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    room_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    hotel: Mapped["Hotel"] = relationship("Hotel")
    branch: Mapped[Optional["Branch"]] = relationship("Branch")
    room: Mapped[Optional["Room"]] = relationship("Room")
    task: Mapped[Optional["HousekeepingTask"]] = relationship("HousekeepingTask")
    reporter: Mapped["User"] = relationship("User", foreign_keys=[reported_by])
