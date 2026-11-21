"""expenses.edit_history — xarajat tahrirlari tarixi

Xarajatlar sahifasida xarajatni TAHRIRLASH imkoniyati qo'shildi (faqat
administrator, super admin va sozlovchi). Har tahrir shu ustunda qoladi:
kim, qachon, qaysi vazifada, qaysi maydon nimadan nimaga o'zgargani va
izoh. Bo'lingan (qisman to'lovli) xarajatda barcha bo'laklarda bir xil
ro'yxat turadi.

Ustun bo'sh bo'lishi mumkin — mavjud xarajatlar o'zgarmaydi.

Revision ID: b2d4f6a8c0e1
Revises: a9c1e3f5b7d2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, None] = "a9c1e3f5b7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "expenses",
        sa.Column(
            "edit_history",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("expenses", "edit_history")
