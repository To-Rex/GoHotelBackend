"""Hujjat sanalari: UTC bo'yicha yozilib qolgan eski yozuvlarni mahalliy kunga keltirish.

2026-11-02 dagi yangilanishgacha to'lov, hisob-faktura, xarajat, jarima,
jurnal yozuvi va tozalash vazifasi sanalari `date.today()` (UTC) bilan
yozilardi: Toshkent vaqti bilan 00:00–05:00 da olingan hujjat bir kun
orqaga tushib qolgan. Moliya hisobotida bunday to'lov "kecha"da, smena
hisobida esa (vaqt belgisi bo'yicha) bugunda turardi — ikki sahifa mos
kelmasdi. Bu migratsiya faqat aynan shu imzoga ega yozuvlarni (sana = UTC
kuni ≠ mahalliy kun) created_at ning mahalliy kuniga keltiradi; hisob-
fakturaning `due_date` (sana + 7) ham birga suriladi. Qo'lda kiritilgan
sanalar tegilmaydi. Mantiq va testlar: `app/shared/date_repair.py`.

Revision ID: f3b8d1c7e9a2
Revises: e2a7c4d9b1f6
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op

revision: str = "f3b8d1c7e9a2"
down_revision: Union[str, None] = "e2a7c4d9b1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Yangilanish (hotel_today) 2026-11-02 kuni deploy qilingan. Undan keyingi
# tun — 2026-11-03 00:00 Toshkent (= 02-noyabr 19:00 UTC) — allaqachon
# to'g'ri yozilgan; chegara shu, undan keyingi yozuvlar ko'rilmaydi.
FIXED_SINCE = datetime(2026, 11, 2, 19, 0, tzinfo=timezone.utc)


def upgrade() -> None:
    from app.core.config import settings
    from app.shared.date_repair import repair_all_utc_dates

    counts = repair_all_utc_dates(
        op.get_bind(), settings.APP_TZ_OFFSET_MINUTES, before=FIXED_SINCE
    )
    for key, n in counts.items():
        if n:
            print(f"  {key}: {n} ta yozuv mahalliy kunga keltirildi")


def downgrade() -> None:
    # Ma'lumot migratsiyasi: eski (noto'g'ri) sanalarga qaytarilmaydi —
    # tuzatilgan sanalar to'g'ri holat.
    pass
