"""shift_sessions.pending_approval — kassa topshirilishida admin tasdig'i

"Kassani topshirishda admin/menejer tasdiqlasin" sozlamasi yoqilganda
xodim kassani topshirgach sessiya PENDING_HANDOVER holatida TASDIQ kutadi;
faqat admin/menejer tasdiqlagach sessiya yopiladi va (kassir ishda davom
etsa) yangi sessiya 0 so'mdan ochiladi. Ustun qaysi oqimdan kelganini
saqlaydi: "close_cash" | "end_shift"; NULL — oddiy oqim o'zgarmagan.

Revision ID: f0e1d2c3b4a5
Revises: e9c0d1e2f3a4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0e1d2c3b4a5"
down_revision: Union[str, None] = "e9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shift_sessions",
        sa.Column("pending_approval", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shift_sessions", "pending_approval")
