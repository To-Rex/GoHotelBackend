"""user work schedule

Xodimga kunlik ish soati (default 8) hamda ish vaqti oralig'i (soat nechadan
nechagacha, default 09:00-18:00) belgilanadi.

Revision ID: m8a9b0c1d2e3
Revises: k6f7a8b9c0d1
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'm8a9b0c1d2e3'
down_revision: Union[str, None] = 'k6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('work_hours_per_day', sa.Integer(), nullable=False, server_default='8'),
    )
    op.add_column(
        'users',
        sa.Column('work_start', sa.String(length=5), nullable=False, server_default='09:00'),
    )
    op.add_column(
        'users',
        sa.Column('work_end', sa.String(length=5), nullable=False, server_default='18:00'),
    )


def downgrade() -> None:
    op.drop_column('users', 'work_end')
    op.drop_column('users', 'work_start')
    op.drop_column('users', 'work_hours_per_day')
