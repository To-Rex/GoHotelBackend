"""document scans

Telefonda skanerlangan hujjat: server uni o'qiydi, veb ekrani esa
yozuvni ko'rib yangi bandlov oynasini ochadi.

Revision ID: b5e6f7a8b9c0
Revises: a4d5e6f7a8b9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5e6f7a8b9c0"
down_revision: Union[str, None] = "a4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_scans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hotel_id", sa.UUID(), nullable=False),
        sa.Column("document_type", sa.String(length=20), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("document_number", sa.String(length=64), nullable=True),
        sa.Column("guest_id", sa.UUID(), nullable=True),
        sa.Column("guest_name", sa.String(length=200), nullable=True),
        sa.Column(
            "verified", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("device_id", sa.String(length=128), nullable=True),
        sa.Column("scanned_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["hotel_id"], ["hotels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scanned_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_scans_hotel_id", "document_scans", ["hotel_id"])
    op.create_index(
        "ix_document_scans_document_number", "document_scans", ["document_number"]
    )
    op.create_index("ix_document_scans_created_at", "document_scans", ["created_at"])
    # Veb ekrani faqat YOPILMAGAN skanerlarni so'raydi — qisman indeks
    # jadval o'sganda ham o'sha so'rovni tez ushlab turadi
    op.create_index(
        "ix_document_scans_open",
        "document_scans",
        ["hotel_id", "created_at"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_document_scans_open", table_name="document_scans")
    op.drop_index("ix_document_scans_created_at", table_name="document_scans")
    op.drop_index("ix_document_scans_document_number", table_name="document_scans")
    op.drop_index("ix_document_scans_hotel_id", table_name="document_scans")
    op.drop_table("document_scans")
