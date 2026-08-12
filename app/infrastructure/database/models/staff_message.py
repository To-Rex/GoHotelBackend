from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class StaffMessage(UUIDPrimaryKeyMixin, Base):
    """Xodimlar o'rtasidagi xabar/so'rovlar taxtasi.

    Farrosh mobil ilovadan so'rov yuboradi ("104-xonani tekshiring"),
    admin/menejer esa saytdagi Xabarlar sahifasidan. Oqim: OPEN → DONE
    (kim va qachon bajargani saqlanadi).
    """

    __tablename__ = "staff_messages"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    room_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="OPEN", index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    done_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    done_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
