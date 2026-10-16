"""payments.method_changes — to'lov turi tahriri tarixi

To'lov TURINI tahrirlash imkoniyati qo'shildi (summa va boshqa maydonlar
tegilmaydi). Har tahrir shu ustunda qoladi: kim, qachon, nimadan nimaga
o'zgargani va izoh. Huquq: administrator/super admin (cheklovsiz) yoki
menejer — faqat oxirgi 2 kunlik to'lovlar. Mavjud to'lovlarga tegilmaydi
(ustun bo'sh qoladi — avvalgi xatti-harakat o'zgarmaydi).

Revision ID: e9c0d1e2f3a4
Revises: d2e3f4a5b6c7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e9c0d1e2f3a4"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "method_changes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("payments", "method_changes")
