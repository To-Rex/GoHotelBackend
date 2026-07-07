from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import TaskPriority, TaskStatus, TaskType
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class HousekeepingTask(FullMixin, Base):
    __tablename__ = "housekeeping_tasks"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.OPEN.value
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskPriority.MEDIUM.value
    )
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="housekeeping_tasks")
    branch: Mapped["Branch"] = relationship("Branch", back_populates="housekeeping_tasks")
    room: Mapped["Room"] = relationship("Room", back_populates="housekeeping_tasks")
    assigned_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="housekeeping_tasks",
        foreign_keys=[assigned_to],
    )
    created_by_user: Mapped["User"] = relationship(
        "User",
        back_populates="created_housekeeping_tasks",
        foreign_keys=[created_by],
    )
    checklist_items: Mapped[list["ChecklistItem"]] = relationship(
        "ChecklistItem", back_populates="task", cascade="all, delete-orphan"
    )
