"""guest blacklist

Nojo'ya xatti-harakat qilgan mehmonni qora ro'yxatga kiritish. Sabab
majburiy: "nega bu odam ro'yxatda?" degan savolga keyin ham javob bo'lishi
kerak.

Revision ID: x1a2b3c4d5e6
Revises: w0f1a2b3c4d5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x1a2b3c4d5e6"
down_revision: Union[str, None] = "w0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guests", sa.Column("blacklisted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("guests", sa.Column("blacklist_reason", sa.Text(), nullable=True))
    op.add_column("guests", sa.Column("blacklisted_by", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_guests_blacklisted_by_users",
        "guests",
        "users",
        ["blacklisted_by"],
        ["id"],
        ondelete="SET NULL",
    )
    # Qora ro'yxat har bir bron yaratishda tekshiriladi — qisman indeks
    # faqat ro'yxatdagilarni saqlaydi, ya'ni kichik va tez
    op.create_index(
        "ix_guests_blacklisted",
        "guests",
        ["hotel_id"],
        postgresql_where=sa.text("blacklisted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_guests_blacklisted", table_name="guests")
    op.drop_constraint("fk_guests_blacklisted_by_users", "guests", type_="foreignkey")
    op.drop_column("guests", "blacklisted_by")
    op.drop_column("guests", "blacklist_reason")
    op.drop_column("guests", "blacklisted_at")
