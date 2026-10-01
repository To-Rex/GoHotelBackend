"""user allow outside work hours

Mehmonxonada "ish vaqtidan tashqarida ishlamaslik" sozlamasi yoqilganda ham
to'silmaydigan xodim belgisi. Standart — false (belgi yo'q); sozlamaning
o'zi hotels.settings["work_hours"] da, alohida ustun talab qilmaydi.

Revision ID: a0d1e2f3a4b5
Revises: f9c0d1e2f3a4
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a0d1e2f3a4b5'
down_revision: Union[str, None] = 'f9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'allow_outside_work_hours',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'allow_outside_work_hours')
