"""Fiskal cheklar (E-POS fiskal moduli) jadvallari.

Fiskal modul IXTIYORIY: filial sozlamasida (`branches.settings["fiscal"]`)
yoqilmagan bo'lsa bu jadvallarga hech narsa yozilmaydi va tizim avvalgidek
ishlaydi.

* `fiscal_receipts` — bitta fiskal chek: sotuv, avans yoki qaytarim.
  Server chekni TAYYORLAYDI (`payload` — E-POS'ga boradigan so'rov), kassa
  kompyuteri uni E-POS Communicator'ga yuboradi va natijani (fiskal belgi,
  soliq havolasi) shu yerga qaytaradi. Holatlar:
  pending (navbatda) → sent (yuborilmoqda) → done | failed; skipped —
  fiskallashtirilmaydigan to'lov (masalan bank o'tkazmasi).
* `fiscal_receipt_links` — chek qaysi to'lov(lar)ni qamraydi. Bitta to'lov
  faqat BITTA chekka kiradi (unique) — ikki kassa bir vaqtda bitta to'lovni
  ikki marta fiskallashtira olmaydi.

Batafsil: app/application/services/fiscal_service.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import BranchScoped, FullMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class FiscalReceipt(BranchScoped, FullMixin, Base):
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "fiscal_receipts"
    __table_args__ = (
        UniqueConstraint("hotel_id", "number", name="uq_fiscal_receipts_hotel_number"),
        Index("ix_fiscal_receipts_branch_status", "branch_id", "status"),
        Index("ix_fiscal_receipts_invoice", "invoice_id"),
        Index("ix_fiscal_receipts_sale", "shop_sale_id"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    #: sale | advance | refund
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: RESERVATION | SHOP
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    reservation_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    #: FK emas: sotuv bekor qilinganda o'chadi, chek esa (qaytarim uchun) qoladi
    shop_sale_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    #: Chek raqami — E-POS'da `paycheckNumber` (tashqi kalit: qayta yuborishdan
    #: oldin shu raqam bo'yicha chek bor-yo'qligi tekshiriladi)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Ko'rsatish uchun (bron raqami / "Do'kon")
    title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    cash_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    card_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    #: E-POS'ga boradigan so'rov (token'siz)
    payload: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    #: pending | sent | done | failed | skipped
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    terminal_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    receipt_seq: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fiscal_sign: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    #: E-POS qaytargan vaqt: "20260305181816"
    fiscal_datetime: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    #: Soliq havolasi (ofd.soliq.uz/check?...) — chekda QR bo'lib chiqadi
    qr_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Qaytarim cheki uchun — asl sotuv cheki
    refund_of_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("fiscal_receipts.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FiscalReceiptLink(Base):
    """Chek ↔ to'lov. `source`: payment (bron to'lovi) | shop_payment (do'kon)."""

    __tablename__ = "fiscal_receipt_links"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_fiscal_links_source"),
        Index("ix_fiscal_links_receipt", "receipt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fiscal_receipts.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(14), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
