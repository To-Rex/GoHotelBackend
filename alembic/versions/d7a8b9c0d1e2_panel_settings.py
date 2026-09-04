"""panel settings

Panel darajasidagi sozlamalar (hozircha Firebase kaliti) — qiymatlar
shifrlangan holda saqlanadi.

Revision ID: d7a8b9c0d1e2
Revises: c6f7a8b9c0d1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7a8b9c0d1e2"
down_revision: Union[str, None] = "c6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "panel_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["panel_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_panel_settings_name", "panel_settings", ["name"])


def downgrade() -> None:
    op.drop_index("ix_panel_settings_name", table_name="panel_settings")
    op.drop_table("panel_settings")
