"""Smena tugallash tasdiqsiz: eski "end_shift" tasdiq belgilarini tozalash.

Admin tasdig'i endi faqat kassa adminga topshirilganda (`close_cash`)
so'raladi. Smena tugallanganda pul keyingi kassirga o'tadi va u paroli
bilan qabul qiladi — tasdiq kerak emas. Yangilanishdan oldin tugallanib,
hali tasdiq kutib turgan smenalar oddiy topshirilgan smenaga aylanadi:
keyingi kassir ularni darhol qabul qila oladi, sanalgan pul uning
boshlang'ich kassasi bo'ladi (ilgari tasdiq ularni yopib, pulni hisobdan
chiqarib yuborardi).

Revision ID: e2a7c4d9b1f6
Revises: b8c9d0e1f2a3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2a7c4d9b1f6"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE shift_sessions SET pending_approval = NULL "
            "WHERE pending_approval = 'end_shift' AND status = 'PENDING_HANDOVER'"
        )
    )


def downgrade() -> None:
    # Ma'lumot migratsiyasi: qaysi smenalar tasdiq kutgani qaytarilmaydi —
    # ular oddiy topshirilgan smena bo'lib qolaveradi (xavfsiz holat).
    pass
