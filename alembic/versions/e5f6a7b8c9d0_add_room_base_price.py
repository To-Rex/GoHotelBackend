"""add_room_base_price

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rooms',
                  sa.Column('base_price', sa.Numeric(12, 2), nullable=False,
                            server_default=sa.text('0')))


def downgrade() -> None:
    op.drop_column('rooms', 'base_price')
