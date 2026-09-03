from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class PanelUser(UUIDPrimaryKeyMixin, Base):
    """Boshqaruv paneliga kira oladigan odam.

    Mehmonxona xodimlaridan (`users`) ATAYLAB alohida jadval: panel
    butun tizim ustidan nazorat beradi va uning ro'yxati mehmonxona
    xodimlari ro'yxati bilan aralashmasligi kerak. Bitta mehmonxonaning
    administratori tasodifan panelga tushib qolmaydi.

    ILDIZ hisob (kodda hashlangan holda turadigan egasi) bu yerda ham
    yozuvga ega bo'ladi — lekin faqat parolini o'zgartirganda. Shu
    paytgacha u kodagi hash bilan kiradi, ya'ni bazasi bo'sh tizimga ham
    kirish mumkin.
    """

    __tablename__ = "panel_users"

    #: Pochta manzili. Ildiz hisobda bu maydon BO'SH bo'ladi — uning
    #: manzili ochiq matnda saqlanmaydi, faqat yig'indisi bilan tekshiriladi.
    email: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    #: Ildiz hisob yozuvini topish uchun — pochta o'rniga yig'indi.
    email_sha256: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    #: Ildiz hisobni o'chirib bo'lmaydi va undan huquq olib bo'lmaydi —
    #: aks holda egasi o'zini tizimdan qulflab qo'yishi mumkin edi.
    is_root: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("panel_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
