"""shop sales paid_at

Sotuvga to'lov sanasi qo'shiladi: moliya hisobotida do'kon tushumi aynan
to'lov olingan kunga tushishi uchun. Mavjud PAID sotuvlar uchun created_at
bilan to'ldiriladi.

Revision ID: i4d5e6f7a8b9
Revises: h3c4d5e6f7a8
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'i4d5e6f7a8b9'
down_revision: Union[str, None] = 'h3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'shop_sales',
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE shop_sales SET paid_at = created_at WHERE status = 'PAID' AND paid_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column('shop_sales', 'paid_at')
