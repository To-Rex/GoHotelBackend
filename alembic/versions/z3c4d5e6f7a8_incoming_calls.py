"""incoming calls

Qabulxona telefoniga kelgan qo'ng'iroq: raqam bo'yicha mehmon topiladi
va veb ekranidagi menyuda ko'rinadi.

Revision ID: z3c4d5e6f7a8
Revises: y2b3c4d5e6f7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z3c4d5e6f7a8"
down_revision: Union[str, None] = "y2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incoming_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hotel_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("phone_digits", sa.String(length=32), nullable=False),
        sa.Column("guest_id", sa.UUID(), nullable=True),
        sa.Column("guest_name", sa.String(length=200), nullable=True),
        sa.Column("reservation_id", sa.UUID(), nullable=True),
        sa.Column("room_number", sa.String(length=20), nullable=True),
        sa.Column("device_id", sa.String(length=128), nullable=True),
        sa.Column("reported_by", sa.UUID(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["hotel_id"], ["hotels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["reservations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["reported_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incoming_calls_hotel_id", "incoming_calls", ["hotel_id"]
    )
    op.create_index(
        "ix_incoming_calls_phone_digits", "incoming_calls", ["phone_digits"]
    )
    op.create_index(
        "ix_incoming_calls_received_at", "incoming_calls", ["received_at"]
    )
    # Menyu faqat YOPILMAGAN qo'ng'iroqlarni so'raydi — qisman indeks
    # jadval o'sganda ham o'sha so'rovni tez ushlab turadi
    op.create_index(
        "ix_incoming_calls_open",
        "incoming_calls",
        ["hotel_id", "received_at"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_incoming_calls_open", table_name="incoming_calls")
    op.drop_index("ix_incoming_calls_received_at", table_name="incoming_calls")
    op.drop_index("ix_incoming_calls_phone_digits", table_name="incoming_calls")
    op.drop_index("ix_incoming_calls_hotel_id", table_name="incoming_calls")
    op.drop_table("incoming_calls")
