from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, BranchScoped


class ChecklistTemplate(BranchScoped, FullMixin, Base):
    """Vazifa turi uchun STANDART ish bandi.

    Administrator "xonani tozalash", "shampun va sovunni almashtirish"
    kabi bandlarni bir marta yozib qo'yadi; har yangi vazifa ochilganda
    ular o'sha vazifaning `checklist_items` iga NUSXA bo'lib tushadi.

    Nusxa olinishi ataylab: farrosh belgilagan holat vazifaga tegishli
    bo'lib qolishi kerak. Administrator keyinroq bandni o'zgartirsa yoki
    o'chirsa, allaqachon ochilgan vazifalar buzilmaydi — ular o'z
    nusxasi bilan yashaydi.
    """

    __tablename__ = "checklist_templates"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hotels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    #: Qaysi vazifa turiga tegishli: CLEANING, DEEP_CLEANING, MAINTENANCE,
    #: INSPECTION, TURN_DOWN. Har turning o'z ro'yxati bo'ladi — ta'mir
    #: bandlari tozalash bandlari bilan aralashib ketmasligi kerak.
    task_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: O'chirish o'rniga o'chirib qo'yish: band vaqtincha kerak bo'lmasa
    #: uni yo'qotmasdan ro'yxatdan chiqarib turish mumkin.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
