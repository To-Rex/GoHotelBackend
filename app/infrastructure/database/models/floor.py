from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class Floor(FullMixin, Base):
    __tablename__ = "floors"
    __table_args__ = (
        UniqueConstraint("branch_id", "floor_number", name="uq_floors_branch_number"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    floor_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    branch: Mapped["Branch"] = relationship("Branch", back_populates="floors")
    rooms: Mapped[list["Room"]] = relationship("Room", back_populates="floor")
