"""reservation daily unit

Kunlik bron qaysi hisobda yaratilgani (`reservations.daily_unit`):
"12h" — 1 kecha = xona narxi, "24h" — 1 kun = xona narxi × 2. Mavjud
bronlar — "12h" (standart), ya'ni ularning narxi o'zgarmaydi.

Revision ID: d3a4b5c6d7e8
Revises: c2f3a4b5c6d7
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd3a4b5c6d7e8'
down_revision: Union[str, None] = 'c2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reservations',
        sa.Column('daily_unit', sa.String(4), nullable=False, server_default='12h'),
    )


def downgrade() -> None:
    op.drop_column('reservations', 'daily_unit')
