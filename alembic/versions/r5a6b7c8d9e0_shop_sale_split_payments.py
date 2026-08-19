"""shop sale split payments

Do'kon sotuvida bo'lib to'lash: bir chekning bir qismi naqd, qismi karta,
qismi o'tkazma bo'lishi mumkin. Bo'laklar shop_sales.payments JSONB'da
saqlanadi ([{"amount": ..., "payment_method": ...}]), payment_method esa
bunday chekda "MIXED" bo'ladi. Oddiy to'lovlarda ustun NULL qoladi.

Revision ID: r5a6b7c8d9e0
Revises: q4e5f6a7b8c9
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'r5a6b7c8d9e0'
down_revision: Union[str, None] = 'q4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'shop_sales',
        sa.Column('payments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('shop_sales', 'payments')
