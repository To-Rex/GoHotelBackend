"""reservation debt acknowledgement (checkout with debt)

Mehmon qarzi bilan chiqarilganda kim, qachon, qancha qarz bilan va NIMA
UCHUN chiqargani yoziladi. Ustunlar bo'sh bo'lishi mumkin — mavjud bronlar
o'zgarmaydi.

Revision ID: f5c6d7e8f9a0
Revises: e4b5c6d7e8f9
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f5c6d7e8f9a0'
down_revision: Union[str, None] = 'e4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reservations', sa.Column('debt_ack_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'reservations',
        sa.Column(
            'debt_ack_by',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column('reservations', sa.Column('debt_ack_note', sa.Text(), nullable=True))
    op.add_column('reservations', sa.Column('debt_ack_amount', sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column('reservations', 'debt_ack_amount')
    op.drop_column('reservations', 'debt_ack_note')
    op.drop_column('reservations', 'debt_ack_by')
    op.drop_column('reservations', 'debt_ack_at')
