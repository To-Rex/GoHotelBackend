"""make_room_types_global

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('room_types_hotel_id_name_key', 'room_types', type_='unique')

    # Deduplicate names — keep first (lowest id), rename others with hotel suffix
    op.execute(sa.text("""
        UPDATE room_types rt1
        SET name = rt1.name || ' (' || rt1.hotel_id::text || ')'
        WHERE rt1.id IN (
            SELECT rt2.id
            FROM room_types rt2
            WHERE EXISTS (
                SELECT 1 FROM room_types rt3
                WHERE rt3.name = rt2.name
                  AND rt3.id < rt2.id
            )
        )
    """))

    op.drop_constraint('room_types_hotel_id_fkey', 'room_types', type_='foreignkey')
    op.drop_column('room_types', 'hotel_id')
    op.create_unique_constraint('uq_room_types_name', 'room_types', ['name'])

    op.create_table(
        'hotel_room_types',
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('room_type_id', UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint('hotel_id', 'room_type_id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['room_type_id'], ['room_types.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('hotel_id', 'room_type_id', name='uq_hotel_room_types'),
    )


def downgrade() -> None:
    op.drop_table('hotel_room_types')

    op.add_column('room_types',
                  sa.Column('hotel_id', UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint('room_types_hotel_id_name_key', 'room_types', ['hotel_id', 'name'])
    op.create_foreign_key('room_types_hotel_id_fkey', 'room_types', 'hotels', ['hotel_id'], ['id'])
