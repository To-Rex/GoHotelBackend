"""shift sessions

Smena va kassa sessiyalari: xodim smenani ochadi/yopadi, kassa "ko'r sanash"
bilan topshiriladi, keyingi xodim parol bilan qabul qiladi. Kassali rejim
mehmonxona sozlamalarida (hotels.settings JSONB) yoqiladi.

Revision ID: n0b1c2d3e4f5
Revises: m8a9b0c1d2e3
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'n0b1c2d3e4f5'
down_revision: Union[str, None] = 'm8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'shift_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('branch_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('branches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ACTIVE'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('opening_cash', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('expected_cash', sa.Numeric(14, 2), nullable=True),
        sa.Column('counted_cash', sa.Numeric(14, 2), nullable=True),
        sa.Column('cash_diff', sa.Numeric(14, 2), nullable=True),
        sa.Column('continue_after_end', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('force_closed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('closed_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('accepted_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_shift_sessions_hotel_id', 'shift_sessions', ['hotel_id'])
    op.create_index('ix_shift_sessions_branch_id', 'shift_sessions', ['branch_id'])
    op.create_index('ix_shift_sessions_user_id', 'shift_sessions', ['user_id'])
    op.create_index('ix_shift_sessions_status', 'shift_sessions', ['status'])


def downgrade() -> None:
    op.drop_index('ix_shift_sessions_status', table_name='shift_sessions')
    op.drop_index('ix_shift_sessions_user_id', table_name='shift_sessions')
    op.drop_index('ix_shift_sessions_branch_id', table_name='shift_sessions')
    op.drop_index('ix_shift_sessions_hotel_id', table_name='shift_sessions')
    op.drop_table('shift_sessions')
