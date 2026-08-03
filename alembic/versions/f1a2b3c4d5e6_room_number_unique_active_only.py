"""room number unique among active rooms only

Arxivdagi (soft-delete) xona o'z raqamini abadiy band qilib qo'ymasligi
uchun unikal cheklov faqat faol xonalarga qo'llanadigan qisman indeksga
almashtiriladi.

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e0f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Eski unikal cheklov turli muhitlarda turlicha nomlangan bo'lishi mumkin
    op.execute("ALTER TABLE rooms DROP CONSTRAINT IF EXISTS uq_rooms_branch_number")
    op.execute(
        "ALTER TABLE rooms DROP CONSTRAINT IF EXISTS rooms_branch_id_room_number_key"
    )
    op.create_index(
        'uq_rooms_branch_number_active',
        'rooms',
        ['branch_id', 'room_number'],
        unique=True,
        postgresql_where=sa.text('is_deleted = false'),
    )


def downgrade() -> None:
    op.drop_index('uq_rooms_branch_number_active', table_name='rooms')
    op.create_unique_constraint(
        'uq_rooms_branch_number', 'rooms', ['branch_id', 'room_number']
    )
