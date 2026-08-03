"""add floor soft delete columns and partial unique index

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e0f1a2b3c4d5'
down_revision: Union[str, None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'floors',
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column(
        'floors',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Unikallik faqat faol qavatlar orasida — arxivdagi qavat raqami
    # yangi qavat yaratishga to'sqinlik qilmaydi. Eski unikal cheklov turli
    # muhitlarda turlicha nomlangan — ikkalasini ham himoyalangan o'chiramiz.
    op.execute("ALTER TABLE floors DROP CONSTRAINT IF EXISTS uq_floors_branch_number")
    op.execute(
        "ALTER TABLE floors DROP CONSTRAINT IF EXISTS floors_branch_id_floor_number_key"
    )
    op.create_index(
        'uq_floors_branch_number_active',
        'floors',
        ['branch_id', 'floor_number'],
        unique=True,
        postgresql_where=sa.text('is_deleted = false'),
    )


def downgrade() -> None:
    op.drop_index('uq_floors_branch_number_active', table_name='floors')
    op.create_unique_constraint('uq_floors_branch_number', 'floors', ['branch_id', 'floor_number'])
    op.drop_column('floors', 'deleted_at')
    op.drop_column('floors', 'is_deleted')
