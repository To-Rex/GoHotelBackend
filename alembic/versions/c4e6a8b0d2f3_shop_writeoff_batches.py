"""shop_writeoffs.batches — spisaniye qaysi partiyalardan chiqqani

Moliya → Do'kon bo'limida foyda va zarar statistikasi qo'shildi. Spisaniye
va inventarizatsiya zarari TANNARXDA hisoblanadi, buning uchun har yozuvda
qaysi partiyadan qancha chiqqani (yoki inventarizatsiyada qaysi partiya
qo'shilgani) saqlanadi: [{"batch_id", "quantity", "cost_price",
"sale_price"}].

Ustun bo'sh bo'lishi mumkin — eski yozuvlar o'zgarmaydi, ular uchun
mahsulotning o'rtacha tannarxi (taxminiy) olinadi.

Revision ID: c4e6a8b0d2f3
Revises: b2d4f6a8c0e1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4e6a8b0d2f3"
down_revision: Union[str, None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shop_writeoffs",
        sa.Column(
            "batches",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("shop_writeoffs", "batches")
