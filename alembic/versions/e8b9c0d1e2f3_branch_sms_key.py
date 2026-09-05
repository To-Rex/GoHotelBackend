"""branch sms key

Har filialga Xabarchi SMS API kaliti — shifrlangan holda saqlanadi.

Revision ID: e8b9c0d1e2f3
Revises: d7a8b9c0d1e2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8b9c0d1e2f3"
down_revision: Union[str, None] = "d7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("branches", sa.Column("sms_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("branches", "sms_api_key")
