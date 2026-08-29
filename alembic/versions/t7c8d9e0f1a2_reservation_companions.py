"""reservation companions

Bir xonaga bir necha kishi joylashganda hamrohlar ham mehmon sifatida
ro'yxatga olinadi. Ular alohida `guests` yozuvi bo'ladi (ya'ni bazada
qidiriladi, hujjati saqlanadi), bronda esa ularning ro'yxati JSONB bo'lib
turadi: [{"guest_id": ..., "name": ...}, ...].

Ism bilan birga saqlanishi ataylab: bronlar ro'yxatini ko'rsatish uchun har
safar mehmonlar jadvaliga borish shart bo'lmaydi va eski bronning hamrohi
mehmonlar ro'yxatining chegarasidan tashqarida qolib ketmaydi.

Revision ID: t7c8d9e0f1a2
Revises: s6b7c8d9e0f1
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t7c8d9e0f1a2"
down_revision: Union[str, None] = "s6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reservations",
        sa.Column("companions", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reservations", "companions")
