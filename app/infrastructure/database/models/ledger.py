from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import LedgerType
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class Ledger(FullMixin, Base):
    __tablename__ = "ledgers"
    __table_args__ = (
        UniqueConstraint("hotel_id", "code", name="uq_ledgers_hotel_code"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ledgers.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="ledgers")
    parent: Mapped[Optional["Ledger"]] = relationship(
        "Ledger", back_populates="children", remote_side="Ledger.id"
    )
    children: Mapped[list["Ledger"]] = relationship(
        "Ledger", back_populates="parent"
    )
    journal_lines: Mapped[list["JournalEntryLine"]] = relationship(
        "JournalEntryLine", back_populates="ledger"
    )
