"""shop writeoffs

Ombor: spisaniye va inventarizatsiya tuzatishlari jadvali. quantity ishorali:
musbat — ombordan chiqarilgan, manfiy — inventarizatsiyada qoldiqqa qo'shilgan.

Revision ID: p2c3d4e5f6a7
Revises: o1c2d3e4f5a6
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'p2c3d4e5f6a7'
down_revision: Union[str, None] = 'o1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'shop_writeoffs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('shop_products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False, server_default='WRITEOFF'),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_shop_writeoffs_hotel_id', 'shop_writeoffs', ['hotel_id'])
    op.create_index('ix_shop_writeoffs_product_id', 'shop_writeoffs', ['product_id'])


def downgrade() -> None:
    op.drop_index('ix_shop_writeoffs_product_id', table_name='shop_writeoffs')
    op.drop_index('ix_shop_writeoffs_hotel_id', table_name='shop_writeoffs')
    op.drop_table('shop_writeoffs')
