"""reservation penalties

Bron jarimalari (kech chiqish, shikast, boshqa) jadvali va bronning
faol jarimalar yig'indisi (`reservations.penalty_amount`, standart 0 —
mavjud bronlar summasi o'zgarmaydi).

Revision ID: c2f3a4b5c6d7
Revises: b1e2f3a4b5c6
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c2f3a4b5c6d7'
down_revision: Union[str, None] = 'b1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reservations',
        sa.Column('penalty_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
    )
    op.create_table(
        'reservation_penalties',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('reservation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('reservations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('penalty_date', sa.Date(), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('voided_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('void_reason', sa.Text(), nullable=True),
        sa.CheckConstraint('amount > 0', name='ck_reservation_penalties_amount'),
    )
    op.create_index('ix_reservation_penalties_hotel_id', 'reservation_penalties', ['hotel_id'])
    op.create_index('ix_reservation_penalties_reservation_id', 'reservation_penalties', ['reservation_id'])
    op.create_index('ix_reservation_penalties_penalty_date', 'reservation_penalties', ['penalty_date'])


def downgrade() -> None:
    op.drop_index('ix_reservation_penalties_penalty_date', table_name='reservation_penalties')
    op.drop_index('ix_reservation_penalties_reservation_id', table_name='reservation_penalties')
    op.drop_index('ix_reservation_penalties_hotel_id', table_name='reservation_penalties')
    op.drop_table('reservation_penalties')
    op.drop_column('reservations', 'penalty_amount')
