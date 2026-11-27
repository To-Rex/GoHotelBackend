"""emehmon — E-mehmon (davlat ro'yxati) integratsiyasi: ro'yxatlar jadvali

Integratsiya ixtiyoriy va har filial uchun alohida yoqiladi (sozlama
`branches.settings["emehmon"]` da — bu migratsiya uni o'zgartirmaydi).

* `emehmon_registrations` — mehmonning E-mehmon ro'yxati (listok): yuborilgan
  shakl, E-mehmon bergan raqam, chiqarilgan vaqti va xatolar.

Hamma narsa yangi — mavjud ma'lumotlarga tegilmaydi.

Revision ID: b8d0f2a4c6e8
Revises: e6a8c0d2f4b6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d0f2a4c6e8"
down_revision: Union[str, None] = "e6a8c0d2f4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "emehmon_registrations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, sa.ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID, sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("reservation_id", UUID, nullable=True),
        sa.Column("guest_id", UUID, nullable=True),
        sa.Column("role", sa.String(10), nullable=False, server_default="main"),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("passport_number", sa.String(50), nullable=True),
        sa.Column("environment", sa.String(12), nullable=False, server_default="sandbox"),
        sa.Column("status", sa.String(12), nullable=False, server_default="sending"),
        sa.Column("listok_id", sa.BigInteger(), nullable=True),
        sa.Column("reg_num", sa.String(40), nullable=True),
        sa.Column("form", JSONB, nullable=True),
        sa.Column("response", JSONB, nullable=True),
        sa.Column("idempotency_key", sa.String(40), nullable=True),
        sa.Column("body_hash", sa.String(64), nullable=True),
        sa.Column("uncertain", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_meta", JSONB, nullable=True),
        sa.Column("arrived_at", TS, nullable=True),
        sa.Column("registered_at", TS, nullable=True),
        sa.Column(
            "registered_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("checked_out_at", TS, nullable=True),
        sa.Column("checkout_sum", sa.Numeric(14, 2), nullable=True),
        sa.Column("checkout_payment_type", sa.Integer(), nullable=True),
        sa.Column(
            "checkout_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("checkout_key", sa.String(40), nullable=True),
        sa.Column("checkout_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkout_tried_at", TS, nullable=True),
        sa.Column("checkout_error_code", sa.String(60), nullable=True),
        sa.Column("checkout_error", sa.Text(), nullable=True),
        sa.Column("date_visit_off", sa.String(40), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_emehmon_registrations_branch_id", "emehmon_registrations", ["branch_id"])
    op.create_index(
        "ix_emehmon_reg_reservation", "emehmon_registrations", ["reservation_id", "guest_id"]
    )
    op.create_index(
        "ix_emehmon_reg_branch_status", "emehmon_registrations", ["branch_id", "status"]
    )
    op.create_index("ix_emehmon_reg_listok", "emehmon_registrations", ["hotel_id", "listok_id"])


def downgrade() -> None:
    op.drop_index("ix_emehmon_reg_listok", table_name="emehmon_registrations")
    op.drop_index("ix_emehmon_reg_branch_status", table_name="emehmon_registrations")
    op.drop_index("ix_emehmon_reg_reservation", table_name="emehmon_registrations")
    op.drop_index("ix_emehmon_registrations_branch_id", table_name="emehmon_registrations")
    op.drop_table("emehmon_registrations")
