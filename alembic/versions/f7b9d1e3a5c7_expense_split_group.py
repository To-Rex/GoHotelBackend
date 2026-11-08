"""expense split group

Qisman (aralash) to'lovli xarajat: 100 000 so'mning 60 000 i naqd,
40 000 i online bo'lsa, har bo'lak alohida `expenses` qatori bo'lib
yoziladi va `split_group` bilan bog'lanadi. Hisobotlar (smena kassasi,
moliya, shaxsiy hisobot) qatorlar bo'yicha ishlayveradi — har bo'lakning
o'z to'lov turi bor; ro'yxatlar bo'laklarni bitta xarajat qilib ko'rsatadi.

Revision ID: f7b9d1e3a5c7
Revises: e5a7c9d1f3b5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b9d1e3a5c7"
down_revision: Union[str, None] = "e5a7c9d1f3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("split_group", sa.UUID(), nullable=True))
    op.create_index("ix_expenses_split_group", "expenses", ["split_group"])


def downgrade() -> None:
    op.drop_index("ix_expenses_split_group", table_name="expenses")
    op.drop_column("expenses", "split_group")
