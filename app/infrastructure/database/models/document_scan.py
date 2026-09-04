from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import UUIDPrimaryKeyMixin


class DocumentScan(UUIDPrimaryKeyMixin, Base):
    """Telefonda skanerlangan hujjat — qabulxona ekraniga uzatiladi.

    Resepsiya xodimi mehmonning pasportini telefonda suratga oladi;
    server uni o'qiydi va natija shu yerda turadi. Veb ekrani bu
    yozuvni ko'rib, yangi bandlov oynasini o'zi ochadi: mehmon bazada
    bo'lsa tanlangan holda, bo'lmasa maydonlari to'ldirilgan holda.

    Nima uchun ALOHIDA jadval:

    * `guests` ga darhol yozib qo'yib bo'lmaydi — skaner xato o'qishi
      mumkin va tasdiqlanmagan yozuv bazani ifloslantirardi. Xodim veb
      oynasida ko'rib, tasdiqlagach mehmon yaratiladi.
    * `incoming_calls` bilan qo'shib yuborish ham noto'g'ri: u yerda
      raqam bor, bu yerda hujjat maydonlari — ikkalasi ham bir-biriga
      bo'sh ustun qo'shardi.

    RASM SAQLANMAYDI. Faqat o'qilgan maydonlar yoziladi: hujjat surati
    shaxsiy ma'lumot va uni qabulxona oynasi uchun saqlashning hojati
    yo'q.
    """

    __tablename__ = "document_scans"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hotels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: ID_CARD yoki PASSPORT.
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Skanerdan chiqqan TO'LIQ javob (tekshiruvlar, ishonch darajalari
    #: bilan). Veb oynasi maydonlarni shundan oladi — ustun qo'shmasdan
    #: yangi maydonni qo'llab-quvvatlash uchun.
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    #: Ro'yxatda ko'rsatish uchun nusxalar.
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    document_number: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )

    #: Hujjat raqami bo'yicha topilgan mehmon (bo'lsa).
    guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guests.id", ondelete="SET NULL"), nullable=True
    )
    guest_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    #: Skaner o'qishni to'liq tasdiqlaganmi (MRZ nazorat raqamlari).
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    device_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    scanned_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    #: Veb oynasi yozuvni olgan payt — ikkinchi marta ochilmasligi uchun.
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
