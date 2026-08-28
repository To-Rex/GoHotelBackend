"""Bir xodim — bitta ochiq smena (baza darajasidagi kafolat)

Xodimda bir vaqtda ikkita ochiq sessiya paydo bo'lsa, kassa ekrani bir
sessiyani, topshirish summasi esa boshqasini ko'rsatardi — natijada xodimning
o'z tushumi hisobdan tushib qolgandek ko'rinardi. Xizmat qatlamida tekshiruv
qo'shildi, lekin yagona ishonchli kafolat — bazadagi cheklov: u parallel
so'rovlarda ham, kelajakdagi yangi kod yo'llarida ham buziladi.

Revision ID: s6b7c8d9e0f1
Revises: r5a6b7c8d9e0
"""
from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "s6b7c8d9e0f1"
down_revision: Union[str, None] = "r5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_shift_sessions_one_open_per_user"


def upgrade() -> None:
    # Indeks qo'yishdan oldin mavjud dublikatlarni yopamiz: har xodimning eng
    # OXIRGI ochiq sessiyasi qoladi (kassa zanjiri o'shanda davom etgan),
    # eskilari yopilgan deb belgilanadi. Bu yerda pul summalari tegilmaydi —
    # faqat holat, shuning uchun hisobotlardagi raqamlar o'zgarmaydi.
    op.execute(
        f"""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY hotel_id, user_id
                       ORDER BY started_at DESC, created_at DESC
                   ) AS rn
            FROM shift_sessions
            WHERE status IN ('ACTIVE', 'PENDING_HANDOVER')
        )
        UPDATE shift_sessions s
           SET status = 'CLOSED',
               ended_at = COALESCE(s.ended_at, s.started_at),
               notes = COALESCE(s.notes, '') ||
                       ' [tizim: takrorlangan ochiq sessiya yopildi]'
          FROM ranked r
         WHERE s.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX {INDEX_NAME}
            ON shift_sessions (hotel_id, user_id)
         WHERE status IN ('ACTIVE', 'PENDING_HANDOVER')
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
