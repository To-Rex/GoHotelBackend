"""expense categories

Xarajat toifalari jadvali. Toifa xarajatda NOM bilan saqlanishi
o'zgarmaydi (`expenses.category`), bu jadval mehmonxonaning tanlash
ro'yxati: nom, belgi, rang, tartib, arxiv.

Ma'lumot: har mavjud mehmonxonaga standart toifalar va xarajatlarda
ilgari qo'lda yozilgan toifa nomlari kiritiladi
(app/shared/expense_categories.seed_expense_categories) — eski
xarajatlarning birortasi ro'yxatdan tashqarida qolmaydi.

Revision ID: e5a7c9d1f3b5
Revises: c1e3f5a7b9d2
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.shared.expense_categories import seed_expense_categories

revision: str = "e5a7c9d1f3b5"
down_revision: Union[str, None] = "c1e3f5a7b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "expense_categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hotel_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("icon", sa.String(length=30), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_expense_categories_hotel_id", "expense_categories", ["hotel_id"])
    # Bir mehmonxonada bir nom — harf kattaligidan qat'i nazar
    op.create_index(
        "ux_expense_categories_hotel_name",
        "expense_categories",
        ["hotel_id", sa.text("lower(name)")],
        unique=True,
    )

    result = seed_expense_categories(op.get_bind())
    print(
        f"Xarajat toifalari: {result['hotels']} ta mehmonxona, "
        f"{result['defaults']} ta standart, {result['imported']} ta xarajatlardan olingan"
    )


def downgrade() -> None:
    op.drop_index("ux_expense_categories_hotel_name", table_name="expense_categories")
    op.drop_index("ix_expense_categories_hotel_id", table_name="expense_categories")
    op.drop_table("expense_categories")
