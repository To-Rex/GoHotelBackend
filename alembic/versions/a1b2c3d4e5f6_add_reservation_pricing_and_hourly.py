"""add_reservation_pricing_and_hourly

Revision ID: a1b2c3d4e5f6
Revises: 4dc5c4f1ccf6
Create Date: 2026-06-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '4dc5c4f1ccf6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reservations',
                  sa.Column('booking_type', sa.String(20), nullable=True,
                            server_default='DAILY'))
    op.add_column('reservations',
                  sa.Column('check_in_datetime', sa.DateTime(timezone=True), nullable=True))
    op.add_column('reservations',
                  sa.Column('check_out_datetime', sa.DateTime(timezone=True), nullable=True))
    op.add_column('reservations',
                  sa.Column('total_amount', sa.Numeric(12, 2), nullable=True,
                            server_default=sa.text('0')))
    op.add_column('reservations',
                  sa.Column('paid_amount', sa.Numeric(12, 2), nullable=True,
                            server_default=sa.text('0')))
    op.add_column('reservations',
                  sa.Column('payment_status', sa.String(20), nullable=True,
                            server_default='UNPAID'))


def downgrade() -> None:
    op.drop_column('reservations', 'payment_status')
    op.drop_column('reservations', 'paid_amount')
    op.drop_column('reservations', 'total_amount')
    op.drop_column('reservations', 'check_out_datetime')
    op.drop_column('reservations', 'check_in_datetime')
    op.drop_column('reservations', 'booking_type')
