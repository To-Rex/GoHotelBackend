"""add housekeeping_tasks.reservation_id link

Avtomatik tozalash/chiqish oqimi uchun tozalash tunini bronga bog'laydigan
ixtiyoriy FK ustuni qo'shiladi.

Revision ID: b7c8d9e0f1a2
Revises: 19971469eafa
Create Date: 2026-07-19 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = '19971469eafa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'housekeeping_tasks',
        sa.Column('reservation_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        'fk_housekeeping_tasks_reservation_id',
        'housekeeping_tasks',
        'reservations',
        ['reservation_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_housekeeping_tasks_reservation_id',
        'housekeeping_tasks',
        type_='foreignkey',
    )
    op.drop_column('housekeeping_tasks', 'reservation_id')
