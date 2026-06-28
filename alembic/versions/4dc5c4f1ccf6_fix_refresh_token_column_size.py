"""fix_refresh_token_column_size

Revision ID: 4dc5c4f1ccf6
Revises: 250c3edb6dfe
Create Date: 2026-06-22 11:21:20.965239
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '4dc5c4f1ccf6'
down_revision: Union[str, None] = '250c3edb6dfe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("user_sessions", "refresh_token_hash",
                    existing_type=sa.String(255),
                    type_=sa.Text(),
                    existing_nullable=True)
    op.alter_column("user_sessions", "ip_address",
                    existing_type=sa.String(50),
                    type_=sa.String(100),
                    existing_nullable=True)


def downgrade() -> None:
    op.alter_column("user_sessions", "refresh_token_hash",
                    existing_type=sa.Text(),
                    type_=sa.String(255),
                    existing_nullable=True)
    op.alter_column("user_sessions", "ip_address",
                    existing_type=sa.String(100),
                    type_=sa.String(50),
                    existing_nullable=True)
