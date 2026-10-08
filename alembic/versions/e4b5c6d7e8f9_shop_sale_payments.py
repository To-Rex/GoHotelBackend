"""shop sale payments (partial payments)

Do'kon savdosini qisman to'lash: har to'lov `shop_sale_payments` da
(summa, usul, qachon, kim qabul qilgan), savdoda to'langan qism
`shop_sales.paid_amount`.

Mavjud ma'lumot SAQLANADI va hisob-kitoblar o'zgarmaydi: har to'langan
(PAID) savdo uchun to'lov qatorlari yaratiladi — bo'lib to'langan bo'lsa
har bo'lak alohida, aks holda bitta qator jami summa bilan; vaqti
`paid_at` (bo'lmasa `created_at`), qabul qilgan — sotuvchi (avval kassa
hisobi ham sotuvchiga yozilardi). `paid_amount` = PAID uchun jami, PENDING
uchun 0.

Revision ID: e4b5c6d7e8f9
Revises: d3a4b5c6d7e8
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e4b5c6d7e8f9'
down_revision: Union[str, None] = 'd3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'shop_sales',
        sa.Column('paid_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
    )
    op.execute("UPDATE shop_sales SET paid_amount = total_amount WHERE status = 'PAID'")

    op.create_table(
        'shop_sale_payments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('sale_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('shop_sales.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('payment_method', sa.String(20), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
    )
    op.create_index('ix_shop_sale_payments_sale_id', 'shop_sale_payments', ['sale_id'])
    op.create_index('ix_shop_sale_payments_hotel_paid_at', 'shop_sale_payments', ['hotel_id', 'paid_at'])

    # Bo'lib to'langan savdolar — har bo'lak alohida qator
    op.execute(
        """
        INSERT INTO shop_sale_payments (id, hotel_id, sale_id, amount, payment_method, paid_at, created_by)
        SELECT gen_random_uuid(), s.hotel_id, s.id, (p.value->>'amount')::numeric,
               p.value->>'payment_method', COALESCE(s.paid_at, s.created_at), s.created_by
        FROM shop_sales s
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(s.payments) = 'array' THEN s.payments ELSE '[]'::jsonb END
        ) AS p(value)
        WHERE s.status = 'PAID'
          AND s.payments IS NOT NULL
          AND (p.value->>'amount') IS NOT NULL
        """
    )
    # Bitta usulda to'langanlar — bitta qator jami summa bilan
    op.execute(
        """
        INSERT INTO shop_sale_payments (id, hotel_id, sale_id, amount, payment_method, paid_at, created_by)
        SELECT gen_random_uuid(), s.hotel_id, s.id, s.total_amount, s.payment_method,
               COALESCE(s.paid_at, s.created_at), s.created_by
        FROM shop_sales s
        WHERE s.status = 'PAID'
          -- CASE: massiv bo'lmagan qiymatda jsonb_array_length chaqirilmasin
          AND (CASE WHEN s.payments IS NOT NULL AND jsonb_typeof(s.payments) = 'array'
                    THEN jsonb_array_length(s.payments) ELSE 0 END) = 0
        """
    )


def downgrade() -> None:
    op.drop_index('ix_shop_sale_payments_hotel_paid_at', table_name='shop_sale_payments')
    op.drop_index('ix_shop_sale_payments_sale_id', table_name='shop_sale_payments')
    op.drop_table('shop_sale_payments')
    op.drop_column('shop_sales', 'paid_amount')
