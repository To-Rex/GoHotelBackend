"""shift corrections

Yopilgan smena sessiyasidagi sanalgan summani admin/menejer tuzatishi uchun
audit ustuni: har bir tuzatish (eski/yangi qiymat, kim, qachon, izoh) JSONB
ro'yxatiga qo'shib boriladi — ustidan yozilmaydi.

Revision ID: o1c2d3e4f5a6
Revises: n0b1c2d3e4f5
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'o1c2d3e4f5a6'
down_revision: Union[str, None] = 'n0b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'shift_sessions',
        sa.Column('corrections', postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('shift_sessions', 'corrections')
