"""add checkout requested at

Resepsiya "mehmon chiqmoqda" deb belgilaganda vaqt shu ustunga yoziladi:
farroshga tozalash vazifasi boradi, farrosh yakunlagach bron avtomatik
CHECKED_OUT bo'ladi.

Revision ID: j5e6f7a8b9c0
Revises: b3c4d5e6f7a8
Create Date: 2026-08-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'j5e6f7a8b9c0'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reservations',
        sa.Column('checkout_requested_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('reservations', 'checkout_requested_at')
