"""add expenses table

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'expenses',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('branches.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('payment_method', sa.String(length=20), nullable=False, server_default='CASH'),
        sa.Column('expense_date', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_expenses_amount'),
    )
    op.create_index('ix_expenses_hotel_id', 'expenses', ['hotel_id'])
    op.create_index('ix_expenses_expense_date', 'expenses', ['expense_date'])


def downgrade() -> None:
    op.drop_index('ix_expenses_expense_date', table_name='expenses')
    op.drop_index('ix_expenses_hotel_id', table_name='expenses')
    op.drop_table('expenses')
