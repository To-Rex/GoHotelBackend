"""guests.id_document_number uchun trigram indeks.

Mehmon qidiruvi JSHSHIR/hujjat raqami bo'yicha ham ishlaydi; katta bazada
ILIKE '%...%' uchun oddiy indeks yaramaydi. pg_trgm mavjud bo'lmasa
migratsiya indeksni o'tkazib yuboradi.

Revision ID: b8c9d0e1f2a3
Revises: f0e1d2c3b4a5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "f0e1d2c3b4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_guests_trgm_id_document_number"


def _trigram_available() -> bool:
    try:
        bind = op.get_bind()
        if bind is None:
            return False
        row = bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
        ).first()
        return row is not None
    except Exception:
        return False


def upgrade() -> None:
    if not _trigram_available():
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        "ON guests USING gin (id_document_number gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
