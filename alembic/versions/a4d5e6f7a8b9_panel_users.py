"""panel users

Boshqaruv paneliga kira oladigan odamlar. Mehmonxona xodimlaridan
(`users`) alohida jadval: panel butun tizim ustidan nazorat beradi.

Revision ID: a4d5e6f7a8b9
Revises: z3c4d5e6f7a8
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4d5e6f7a8b9"
down_revision: Union[str, None] = "z3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "panel_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("email_sha256", sa.String(length=64), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column(
            "is_root", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["panel_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_panel_users_email"),
        sa.UniqueConstraint("email_sha256", name="uq_panel_users_email_sha256"),
    )
    op.create_index("ix_panel_users_email", "panel_users", ["email"])


def downgrade() -> None:
    op.drop_index("ix_panel_users_email", table_name="panel_users")
    op.drop_table("panel_users")
