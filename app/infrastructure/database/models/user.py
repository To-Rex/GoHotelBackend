from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import UserStatus, UserType
from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, SoftDeleteMixin


class User(FullMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    user_type: Mapped[str] = mapped_column(String(20), nullable=False)
    hotel_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("hotels.id", ondelete="SET NULL"), nullable=True
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserStatus.ACTIVE.value
    )
    hire_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    termination_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    hotel: Mapped[Optional["Hotel"]] = relationship("Hotel", back_populates="users")
    branch: Mapped[Optional["Branch"]] = relationship("Branch", back_populates="users")
    user_permissions: Mapped[list["UserPermission"]] = relationship(
        "UserPermission", back_populates="user", foreign_keys="UserPermission.user_id"
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession", back_populates="user"
    )
    created_reservations: Mapped[list["Reservation"]] = relationship(
        "Reservation", back_populates="created_by_user", foreign_keys="Reservation.created_by"
    )
    cancelled_reservations: Mapped[list["Reservation"]] = relationship(
        "Reservation", back_populates="canceller", foreign_keys="Reservation.cancelled_by"
    )
    housekeeping_tasks: Mapped[list["HousekeepingTask"]] = relationship(
        "HousekeepingTask",
        back_populates="assigned_user",
        foreign_keys="HousekeepingTask.assigned_to",
    )
    created_housekeeping_tasks: Mapped[list["HousekeepingTask"]] = relationship(
        "HousekeepingTask",
        back_populates="created_by_user",
        foreign_keys="HousekeepingTask.created_by",
    )
    created_invoices: Mapped[list["Invoice"]] = relationship(
        "Invoice", back_populates="created_by_user", foreign_keys="Invoice.created_by"
    )
    created_payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="created_by_user", foreign_keys="Payment.created_by"
    )
    posted_journal_entries: Mapped[list["JournalEntry"]] = relationship(
        "JournalEntry", back_populates="poster", foreign_keys="JournalEntry.posted_by"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="user"
    )
