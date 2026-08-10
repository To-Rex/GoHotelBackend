"""reservation room moves

Bronni boshqa xonaga ko'chirish auditi: har ko'chirish (kim, qachon, qaysi
xonadan qaysinisiga, narx qanday o'zgardi) JSONB ro'yxatiga qo'shib boriladi.

Revision ID: p3d4e5f6a7b8
Revises: p2c3d4e5f6a7
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'p3d4e5f6a7b8'
down_revision: Union[str, None] = 'p2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reservations',
        sa.Column('room_moves', postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('reservations', 'room_moves')
