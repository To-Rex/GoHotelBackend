"""add shop tables

Do'kon: mahsulotlar, FIFO partiyalar (har biri o'z narxi bilan), sotuvlar
va sotuv qatorlari. Sotuv bronga biriktirilishi mumkin (PENDING holat).

Revision ID: h3c4d5e6f7a8
Revises: g2b3c4d5e6f7
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'h3c4d5e6f7a8'
down_revision: Union[str, None] = 'g2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'shop_products',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('emoji', sa.String(8), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_shop_products_hotel_id', 'shop_products', ['hotel_id'])
    op.create_index(
        'uq_shop_products_hotel_name', 'shop_products', ['hotel_id', 'name'], unique=True
    )

    op.create_table(
        'shop_batches',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('shop_products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('remaining', sa.Integer(), nullable=False),
        sa.Column('cost_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('sale_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('quantity > 0', name='ck_shop_batches_quantity'),
        sa.CheckConstraint('remaining >= 0', name='ck_shop_batches_remaining'),
    )
    op.create_index('ix_shop_batches_hotel_id', 'shop_batches', ['hotel_id'])
    op.create_index('ix_shop_batches_product_id', 'shop_batches', ['product_id'])

    op.create_table(
        'shop_sales',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('reservation_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('reservations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('payment_method', sa.String(20), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='PAID'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_shop_sales_hotel_id', 'shop_sales', ['hotel_id'])
    op.create_index('ix_shop_sales_reservation_id', 'shop_sales', ['reservation_id'])

    op.create_table(
        'shop_sale_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('sale_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('shop_sales.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('shop_products.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('batch_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('shop_batches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('product_name', sa.String(120), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('total_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_shop_sale_items_sale_id', 'shop_sale_items', ['sale_id'])


def downgrade() -> None:
    op.drop_table('shop_sale_items')
    op.drop_table('shop_sales')
    op.drop_table('shop_batches')
    op.drop_table('shop_products')
