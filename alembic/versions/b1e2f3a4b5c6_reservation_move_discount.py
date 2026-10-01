"""reservation move discount

Qimmatroq xonaga ko'chirishda resepshn bergan chegirma (so'm). Bron
chegirmasidan (discount_amount / discount_percent) alohida saqlanadi.
Standart — 0: mavjud bronlarning summasi o'zgarmaydi. Sozlamaning o'zi
hotels.settings["room_move_discount"] da, alohida ustun talab qilmaydi.

Revision ID: b1e2f3a4b5c6
Revises: a0d1e2f3a4b5
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b1e2f3a4b5c6'
down_revision: Union[str, None] = 'a0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reservations',
        sa.Column(
            'move_discount_amount',
            sa.Numeric(12, 2),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    op.drop_column('reservations', 'move_discount_amount')
