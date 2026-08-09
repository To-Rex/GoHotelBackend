"""task auto completed flag

Xo'jalik vazifasi belgilangan vaqt ichida qo'lda yakunlanmasa scheduler uni
avtomatik yopadi — bu bayroq ilovada "avto yakunlangan"ligini ko'rsatadi.

Revision ID: k6f7a8b9c0d1
Revises: j5e6f7a8b9c0
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'k6f7a8b9c0d1'
down_revision: Union[str, None] = 'j5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'housekeeping_tasks',
        sa.Column(
            'auto_completed',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('housekeeping_tasks', 'auto_completed')
