"""checklist templates

Vazifa turi uchun standart ish bandlari. Administrator bir marta yozib
qo'yadi, har yangi vazifa ochilganda ular vazifaning o'z ro'yxatiga
nusxa bo'lib tushadi va farrosh ularni belgilaydi.

Revision ID: y2b3c4d5e6f7
Revises: x1a2b3c4d5e6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y2b3c4d5e6f7"
down_revision: Union[str, None] = "x1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checklist_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hotel_id", sa.UUID(), nullable=False),
        sa.Column("task_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
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
        sa.ForeignKeyConstraint(["hotel_id"], ["hotels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Ro'yxat har doim mehmonxona + vazifa turi bo'yicha o'qiladi
    op.create_index(
        "ix_checklist_templates_hotel_type",
        "checklist_templates",
        ["hotel_id", "task_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_checklist_templates_hotel_type", table_name="checklist_templates")
    op.drop_table("checklist_templates")
