"""make_amenities_global

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('uq_amenities_hotel_name', 'amenities', type_='unique')
    op.drop_constraint('amenities_hotel_id_fkey', 'amenities', type_='foreignkey')
    op.drop_column('amenities', 'hotel_id')
    op.create_unique_constraint('uq_amenities_name', 'amenities', ['name'])

    op.create_table(
        'hotel_amenities',
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('amenity_id', UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint('hotel_id', 'amenity_id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['amenity_id'], ['amenities.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('hotel_id', 'amenity_id', name='uq_hotel_amenities'),
    )


def downgrade() -> None:
    op.drop_table('hotel_amenities')

    op.add_column('amenities',
                  sa.Column('hotel_id', UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint('uq_amenities_hotel_name', 'amenities', ['hotel_id', 'name'])
    op.create_foreign_key('amenities_hotel_id_fkey', 'amenities', 'hotels', ['hotel_id'], ['id'])
