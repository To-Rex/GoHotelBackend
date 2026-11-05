"""api error logs

Panel so'rovlar jurnalidagi xatolar (4xx/5xx) uchun jadval — jonli jurnal
xotirada bo'lgani uchun har deploy'da yo'qolardi; endi xatolar bazada
3 kun (API_ERROR_LOG_RETENTION_DAYS) saqlanadi. FK yo'q, ustunlar —
jurnal yozuvidagi matnlar.

Revision ID: b9d1f3a5c7e9
Revises: f3b8d1c7e9a2
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b9d1f3a5c7e9'
down_revision: Union[str, None] = 'f3b8d1c7e9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'api_error_logs',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('method', sa.String(10), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Float(), nullable=False, server_default='0'),
        sa.Column('ip', sa.String(64), nullable=True),
        sa.Column('request_body', sa.Text(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('hotel_id', sa.String(36), nullable=True),
        sa.Column('branch_id', sa.String(36), nullable=True),
        sa.Column('user_id', sa.String(36), nullable=True),
        sa.Column('user_type', sa.String(30), nullable=True),
        sa.Column('user_agent', sa.String(300), nullable=True),
    )
    op.create_index('ix_api_error_logs_ts', 'api_error_logs', ['ts'])
    op.create_index('ix_api_error_logs_status', 'api_error_logs', ['status'])
    op.create_index('ix_api_error_logs_hotel_id', 'api_error_logs', ['hotel_id'])


def downgrade() -> None:
    op.drop_index('ix_api_error_logs_hotel_id', table_name='api_error_logs')
    op.drop_index('ix_api_error_logs_status', table_name='api_error_logs')
    op.drop_index('ix_api_error_logs_ts', table_name='api_error_logs')
    op.drop_table('api_error_logs')
