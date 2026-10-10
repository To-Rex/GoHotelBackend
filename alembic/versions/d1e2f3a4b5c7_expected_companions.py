"""reservations.expected_companions — kechikib keladigan hamrohlar

Bron yaratilganda (yoki keyin) "yana bir hamroh kechikib keladi" deb
belgilanadi: joy band, kelganda bron oynasidan haqiqiy hamroh sifatida
biriktiriladi. Mavjud bronlarga tegilmaydi (ustun bo'sh qoladi).

Revision ID: d1e2f3a4b5c7
Revises: c9d0e1f2a3b4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c7"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reservations",
        sa.Column("expected_companions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reservations", "expected_companions")
