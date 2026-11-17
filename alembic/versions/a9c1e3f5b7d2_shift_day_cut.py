"""shift day cut handover

Kassani KUNLIK KESIM bo'yicha topshirish: xodim hamma pulni emas, tanlangan
kesim momentigacha (masalan 00:05 dan 00:05 gacha) bo'lgan kunlarni
topshiradi; kesimdan keyingi tushum davom etayotgan sessiyada qoladi.

  handed_at    — pul haqiqatda topshirilgan vaqt (sessiya `ended_at` da,
                 ya'ni kesim momentida yopiladi). NULL — oddiy topshirish.
  carried_cash — kassada qoldirilgan summa: kesimdan keyingi naqd xarajat
                 o'sha kunning pulidan to'langan bo'lsa, shu qism
                 topshirilmaydi va keyingi sessiyaning boshlang'ich kassasi
                 bo'ladi.

Ikkala ustun ham bo'sh bo'lishi mumkin — mavjud sessiyalar o'zgarmaydi.

Revision ID: a9c1e3f5b7d2
Revises: f7b9d1e3a5c7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c1e3f5b7d2"
down_revision: Union[str, None] = "f7b9d1e3a5c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shift_sessions",
        sa.Column("handed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "shift_sessions",
        sa.Column("carried_cash", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shift_sessions", "carried_cash")
    op.drop_column("shift_sessions", "handed_at")
