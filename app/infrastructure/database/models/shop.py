from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, BranchScoped


class ShopProduct(BranchScoped, FullMixin, Base):
    """Do'kon mahsuloti (ichimlik, shirinlik va h.k.) — mehmonxonaga tegishli.

    Narx mahsulotda emas, partiyada (ShopBatch) saqlanadi: yangi partiya
    boshqa narxda kelishi mumkin, sotuv esa FIFO bo'yicha eng eski partiyadan
    boshlab yuradi.
    """

    __tablename__ = "shop_products"
    __table_args__ = (
        Index("uq_shop_products_branch_name", "branch_id", "name", unique=True),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    emoji: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    batches: Mapped[list["ShopBatch"]] = relationship(
        "ShopBatch", back_populates="product", cascade="all, delete-orphan"
    )


class ShopBatch(BranchScoped, FullMixin, Base):
    """Mahsulot partiyasi (kirim): miqdor, qoldiq va shu partiyaning narxi."""

    __tablename__ = "shop_batches"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_shop_batches_quantity"),
        CheckConstraint("remaining >= 0", name="ck_shop_batches_remaining"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shop_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    # Olish (tannarx) narxi ixtiyoriy — foyda hisobotlari uchun keyin kerak bo'ladi
    cost_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    sale_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    product: Mapped["ShopProduct"] = relationship("ShopProduct", back_populates="batches")


class ShopSale(BranchScoped, FullMixin, Base):
    """Sotuv cheki. reservation_id bo'lsa — bron hisobiga yozilgan (PENDING),
    to'lov keyin olinadi; oddiy sotuvda darhol PAID bo'ladi.

    Bronga yozilgan savdo QISMAN ham to'lanishi mumkin (istalgancha marta,
    har safar bir yoki bir necha usulda): to'langan qism `paid_amount` da,
    har to'lov `shop_sale_payments` da. Qoldiq qolgan ekan — PENDING."""

    __tablename__ = "shop_sales"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    reservation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # PAID sotuvda to'lov usuli bor; PENDING (bronga yozilgan)da hali yo'q.
    # Bo'lib to'lashda "MIXED" bo'ladi, bo'laklar payments ro'yxatida
    payment_method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Bo'lib to'lash bo'laklari: [{"amount": 10000, "payment_method": "CASH"}, ...]
    # Oddiy (bitta usulli) to'lovda NULL qoladi
    payments: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PAID", server_default="PAID"
    )
    # To'lov qachon olingan — moliya hisobotida tushum shu sanaga tushadi
    # (bronga yozilgan sotuv keyinroq to'lansa, o'sha kunning tushumi bo'ladi)
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # To'langan qism (qisman to'lovlar yig'indisi). PAID sotuvda = jami
    paid_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    items: Mapped[list["ShopSaleItem"]] = relationship(
        "ShopSaleItem", back_populates="sale", cascade="all, delete-orphan"
    )


class ShopSalePayment(BranchScoped, Base):
    """Do'kon savdosi bo'yicha bitta to'lov (qisman yoki to'liq).

    Do'kon tushumi va kassa shu jadvaldan hisoblanadi: pul QACHON
    (`paid_at`) va KIM tomonidan (`created_by` — pulni qabul qilgan xodim)
    olingan bo'lsa, o'sha kun va o'sha kassaga yoziladi. Bo'lib to'lashda
    har usul alohida qator. Sotuv o'chirilsa — to'lovlari ham (CASCADE).
    """

    __tablename__ = "shop_sale_payments"
    __table_args__ = (
        Index("ix_shop_sale_payments_hotel_paid_at", "hotel_id", "paid_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    sale_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shop_sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Eski (to'lov usulisiz) yozuvlar uchun bo'sh bo'lishi mumkin
    payment_method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ShopWriteoff(BranchScoped, FullMixin, Base):
    """Ombor harakati: spisaniye yoki inventarizatsiya tuzatishi.

    quantity ISHORALI: musbat — ombordan chiqarilgan (kamomad/spisaniye),
    manfiy — inventarizatsiyada ortiqcha topilib qoldiqqa qo'shilgan.
    Chiqarish sotuvdagi kabi FIFO tartibida partiyalardan yechiladi.
    """

    __tablename__ = "shop_writeoffs"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: Filial — yozuv faqat shu filial ichida ko'rinadi (branch_scope.py)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shop_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # WRITEOFF — qo'lda spisaniye; INVENTORY — inventarizatsiya tuzatishi
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="WRITEOFF", server_default="WRITEOFF"
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    product: Mapped["ShopProduct"] = relationship("ShopProduct")


class ShopSaleItem(FullMixin, Base):
    """Sotuv qatori. FIFO bo'yicha bitta mahsulot ikki partiyadan olinsa,
    har xil narxli ikkita qator bo'ladi. batch_id — bekor qilishda qoldiqni
    aynan o'sha partiyaga qaytarish uchun."""

    __tablename__ = "shop_sale_items"

    sale_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shop_sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shop_products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("shop_batches.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    total_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    sale: Mapped["ShopSale"] = relationship("ShopSale", back_populates="items")
