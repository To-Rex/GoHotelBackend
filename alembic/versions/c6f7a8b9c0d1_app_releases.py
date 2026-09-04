"""app releases

Dasturlar do'koni: panel Android/Windows fayllarini yuklaydi,
mehmonxona administratorlari yuklab oladi.

Revision ID: c6f7a8b9c0d1
Revises: b5e6f7a8b9c0
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6f7a8b9c0d1"
down_revision: Union[str, None] = "b5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_releases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("minio_bucket", sa.String(length=100), nullable=False),
        sa.Column("minio_path", sa.String(length=500), nullable=False),
        sa.Column(
            "download_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["panel_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_releases_platform", "app_releases", ["platform"])
    op.create_index("ix_app_releases_created_at", "app_releases", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_app_releases_created_at", table_name="app_releases")
    op.drop_index("ix_app_releases_platform", table_name="app_releases")
    op.drop_table("app_releases")
