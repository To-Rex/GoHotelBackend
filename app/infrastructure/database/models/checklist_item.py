from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class ChecklistItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "checklist_items"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("housekeeping_tasks.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)

    task: Mapped["HousekeepingTask"] = relationship(
        "HousekeepingTask", back_populates="checklist_items"
    )
