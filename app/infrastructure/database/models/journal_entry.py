from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import EntryStatus
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, UUIDPrimaryKeyMixin


class JournalEntry(FullMixin, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("hotel_id", "entry_number", name="uq_journal_entries_hotel_number"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    entry_number: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    reference_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    reference_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_debit: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    total_credit: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EntryStatus.DRAFT.value
    )
    posted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    hotel: Mapped["Hotel"] = relationship("Hotel", back_populates="journal_entries")
    poster: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="posted_journal_entries",
        foreign_keys=[posted_by],
    )
    lines: Mapped[list["JournalEntryLine"]] = relationship(
        "JournalEntryLine", back_populates="journal_entry"
    )


class JournalEntryLine(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "journal_entry_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0", name="ck_journal_entry_lines_debit"),
        CheckConstraint("credit >= 0", name="ck_journal_entry_lines_credit"),
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="ck_journal_entry_lines_debit_credit_xor",
        ),
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False
    )
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    ledger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledgers.id", ondelete="RESTRICT"), nullable=False
    )
    debit: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    credit: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    journal_entry: Mapped["JournalEntry"] = relationship(
        "JournalEntry", back_populates="lines"
    )
    ledger: Mapped["Ledger"] = relationship("Ledger", back_populates="journal_lines")
    hotel: Mapped["Hotel"] = relationship("Hotel")
