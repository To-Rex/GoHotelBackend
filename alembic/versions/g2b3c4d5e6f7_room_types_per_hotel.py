"""room types become per-hotel

Xona turlari endi mehmonxonaga tegishli: hotel_id ustuni qo'shiladi,
global nom unikaliligi (hotel_id, name) juftligiga almashtiriladi.

Revision ID: g2b3c4d5e6f7
Revises: a2b3c4d5e6f7
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'g2b3c4d5e6f7'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'room_types',
        sa.Column(
            'hotel_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('hotels.id', ondelete='RESTRICT'),
            nullable=True,
        ),
    )
    op.create_index('ix_room_types_hotel_id', 'room_types', ['hotel_id'])
    # Eski global unikal cheklov turli muhitlarda turlicha nomlangan bo'lishi
    # mumkin — ikkalasini ham himoyalangan holda o'chiramiz
    op.execute("ALTER TABLE room_types DROP CONSTRAINT IF EXISTS uq_room_types_name")
    op.execute("ALTER TABLE room_types DROP CONSTRAINT IF EXISTS room_types_name_key")
    op.create_index(
        'uq_room_types_hotel_name', 'room_types', ['hotel_id', 'name'], unique=True
    )


def downgrade() -> None:
    op.drop_index('uq_room_types_hotel_name', table_name='room_types')
    op.create_unique_constraint('uq_room_types_name', 'room_types', ['name'])
    op.drop_index('ix_room_types_hotel_id', table_name='room_types')
    op.drop_column('room_types', 'hotel_id')
