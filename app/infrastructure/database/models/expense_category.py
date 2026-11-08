from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin


class ExpenseCategory(FullMixin, Base):
    """Xarajat toifasi — mehmonxonaning tanlash ro'yxatidagi bitta qator.

    Xarajat toifani NOM bilan saqlaydi (`expenses.category`), bu jadval
    esa nomlar ro'yxati: belgi, rang, tartib. Shu sabab toifa o'chirilsa
    ham eski xarajatlar o'z toifasida qoladi va hisobotlar o'zgarmaydi;
    toifa QAYTA NOMLANSA xarajatlardagi nom ham birga o'zgaradi
    (expense_category_service) — guruh bo'linib ketmasin.

    Mehmonxona darajasida, filialga bog'lanmagan: bir mehmonxonaning
    filiallari bitta ro'yxatdan foydalanadi.
    """

    __tablename__ = "expense_categories"
    __table_args__ = (
        # Bir mehmonxonada bir nom — harf kattaligidan qat'i nazar
        Index(
            "ux_expense_categories_hotel_name",
            "hotel_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hotels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: `expenses.category` bilan bir xil uzunlik (50)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    #: Mijoz biladigan belgi nomi (app/shared/expense_categories.ICONS)
    icon: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    #: Palitra kaliti (app/shared/expense_categories.COLORS)
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: O'chirish o'rniga arxiv: ishlatilgan toifa yo'qolmaydi, faqat
    #: tanlovdan chiqadi; qayta yoqsa bo'ladi
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
