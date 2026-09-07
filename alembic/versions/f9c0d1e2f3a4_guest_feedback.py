"""guest feedback

Talab, taklif va shikoyatlar — mehmon murojaatlari kitobi. Jadval bilan
birga modulning uchta ruxsat kodi ham qo'shiladi (idempotent: mavjud bo'lsa
tashlab ketiladi), chunki deploy'da faqat `alembic upgrade head` ishlaydi,
seed skripti emas.

Revision ID: f9c0d1e2f3a4
Revises: e8b9c0d1e2f3
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "f9c0d1e2f3a4"
down_revision: Union[str, None] = "e8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# scripts/seed_permissions.py dagi bilan bir xil
PERMISSIONS = (
    (
        "feedback.view",
        "View Guest Feedback",
        "View guest requests, suggestions and complaints",
    ),
    (
        "feedback.create",
        "Record Guest Feedback",
        "Record guest requests, suggestions and complaints",
    ),
    (
        "feedback.manage",
        "Manage Guest Feedback",
        "Assign, respond to, close and delete guest feedback",
    ),
)


def upgrade() -> None:
    op.create_table(
        "guest_feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hotel_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=True),
        sa.Column("guest_id", sa.UUID(), nullable=True),
        sa.Column("reservation_id", sa.UUID(), nullable=True),
        sa.Column("room_id", sa.UUID(), nullable=True),
        sa.Column("feedback_type", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="NEW"
        ),
        sa.Column(
            "priority", sa.String(length=20), nullable=False, server_default="MEDIUM"
        ),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("guest_name", sa.String(length=200), nullable=True),
        sa.Column("guest_phone", sa.String(length=50), nullable=True),
        sa.Column("room_number", sa.String(length=20), nullable=True),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["hotel_id"], ["hotels.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["reservations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guest_feedback_hotel_id", "guest_feedback", ["hotel_id"])
    # Sahifa doim "shu mehmonxona + holat" bo'yicha so'raydi
    op.create_index(
        "ix_guest_feedback_hotel_status", "guest_feedback", ["hotel_id", "status"]
    )
    op.create_index("ix_guest_feedback_created_at", "guest_feedback", ["created_at"])

    # Ruxsat kodlari — bor bo'lsa tegilmaydi (seed skripti bilan to'qnashmaydi)
    for code, name, description in PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions "
                "(id, name, code, description, module, is_active, created_at) "
                "SELECT CAST(:id AS uuid), :name, :code, :description, "
                "'feedback', true, now() "
                "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"
            ).bindparams(
                id=str(uuid4()), name=name, code=code, description=description
            )
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM user_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE module = 'feedback')"
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE module = 'feedback'"))
    op.drop_index("ix_guest_feedback_created_at", table_name="guest_feedback")
    op.drop_index("ix_guest_feedback_hotel_status", table_name="guest_feedback")
    op.drop_index("ix_guest_feedback_hotel_id", table_name="guest_feedback")
    op.drop_table("guest_feedback")
