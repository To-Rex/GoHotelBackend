"""fiscal — E-POS fiskal moduli: fiskal cheklar va mahsulot IKPU kodi

Fiskal modul ixtiyoriy va har filial uchun alohida yoqiladi (sozlama
`branches.settings["fiscal"]` da — bu migratsiya uni o'zgartirmaydi).

* `fiscal_receipts` — tayyorlangan va E-POS'ga yuborilgan fiskal cheklar
  (fiskal belgi, soliq havolasi).
* `fiscal_receipt_links` — chek qaysi to'lovlarni qamraydi (bitta to'lov —
  bitta chek).
* `shop_products.ikpu_code`, `package_code` — mahsulotning IKPU va o'ram kodi.

Hamma narsa yangi — mavjud ma'lumotlarga tegilmaydi.

Revision ID: e6a8c0d2f4b6
Revises: d5f7b9c1e3a4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6a8c0d2f4b6"
down_revision: Union[str, None] = "d5f7b9c1e3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "fiscal_receipts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, sa.ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID, sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("invoice_id", UUID, nullable=True),
        sa.Column("reservation_id", UUID, nullable=True),
        sa.Column("shop_sale_id", UUID, nullable=True),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("title", sa.String(120), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("cash_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("card_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("terminal_id", sa.String(40), nullable=True),
        sa.Column("receipt_seq", sa.String(20), nullable=True),
        sa.Column("fiscal_sign", sa.String(40), nullable=True),
        sa.Column("fiscal_datetime", sa.String(20), nullable=True),
        sa.Column("qr_url", sa.Text(), nullable=True),
        sa.Column(
            "refund_of_id", UUID,
            sa.ForeignKey("fiscal_receipts.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("completed_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("hotel_id", "number", name="uq_fiscal_receipts_hotel_number"),
    )
    op.create_index("ix_fiscal_receipts_branch_id", "fiscal_receipts", ["branch_id"])
    op.create_index("ix_fiscal_receipts_branch_status", "fiscal_receipts", ["branch_id", "status"])
    op.create_index("ix_fiscal_receipts_invoice", "fiscal_receipts", ["invoice_id"])
    op.create_index("ix_fiscal_receipts_sale", "fiscal_receipts", ["shop_sale_id"])

    op.create_table(
        "fiscal_receipt_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "receipt_id", UUID,
            sa.ForeignKey("fiscal_receipts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source", sa.String(14), nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.UniqueConstraint("source", "source_id", name="uq_fiscal_links_source"),
    )
    op.create_index("ix_fiscal_links_receipt", "fiscal_receipt_links", ["receipt_id"])

    op.add_column("shop_products", sa.Column("ikpu_code", sa.String(20), nullable=True))
    op.add_column("shop_products", sa.Column("package_code", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("shop_products", "package_code")
    op.drop_column("shop_products", "ikpu_code")
    op.drop_index("ix_fiscal_links_receipt", table_name="fiscal_receipt_links")
    op.drop_table("fiscal_receipt_links")
    op.drop_index("ix_fiscal_receipts_sale", table_name="fiscal_receipts")
    op.drop_index("ix_fiscal_receipts_invoice", table_name="fiscal_receipts")
    op.drop_index("ix_fiscal_receipts_branch_status", table_name="fiscal_receipts")
    op.drop_index("ix_fiscal_receipts_branch_id", table_name="fiscal_receipts")
    op.drop_table("fiscal_receipts")
