"""trusted devices

Xodim faqat administrator tasdiqlagan qurilmadan kira oladi. Begona
qurilmadan urinish jadvalga PENDING holatida tushadi va kutib qoladi.

Revision ID: w0f1a2b3c4d5
Revises: v9e0f1a2b3c4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w0f1a2b3c4d5"
down_revision: Union[str, None] = "v9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trusted_devices",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("hotel_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="PENDING"
        ),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=100), nullable=True),
        sa.Column("last_user_id", sa.UUID(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["hotel_id"], ["hotels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "hotel_id", "device_id", name="uq_trusted_devices_hotel_device"
        ),
    )
    op.create_index(
        "ix_trusted_devices_hotel_status",
        "trusted_devices",
        ["hotel_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_trusted_devices_hotel_status", table_name="trusted_devices")
    op.drop_table("trusted_devices")
