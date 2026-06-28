"""add_amenities_and_room_capacity

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'amenities',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('icon', sa.String(50), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.UniqueConstraint('hotel_id', 'name', name='uq_amenities_hotel_name'),
    )

    op.create_table(
        'room_amenities',
        sa.Column('room_id', UUID(as_uuid=True), nullable=False),
        sa.Column('amenity_id', UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint('room_id', 'amenity_id'),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['amenity_id'], ['amenities.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('room_id', 'amenity_id', name='uq_room_amenities'),
    )

    op.add_column('rooms',
                  sa.Column('capacity', sa.SmallInteger(), nullable=True,
                            server_default='1'))


def downgrade() -> None:
    op.drop_column('rooms', 'capacity')
    op.drop_table('room_amenities')
    op.drop_table('amenities')
