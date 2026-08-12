"""staff messages

Xodimlar xabar/so'rovlari taxtasi: farrosh mobil ilovadan so'rov yuboradi
("104-xonani tekshiring"), admin/menejer saytdagi Xabarlar sahifasidan.
OPEN -> DONE oqimi, kim bajarganini saqlaymiz.

Revision ID: q4e5f6a7b8c9
Revises: p3d4e5f6a7b8
Create Date: 2026-08-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'q4e5f6a7b8c9'
down_revision: Union[str, None] = 'p3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'staff_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('room_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('rooms.id', ondelete='SET NULL'), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='OPEN'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('done_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_staff_messages_hotel_id', 'staff_messages', ['hotel_id'])
    op.create_index('ix_staff_messages_status', 'staff_messages', ['status'])


def downgrade() -> None:
    op.drop_index('ix_staff_messages_status', table_name='staff_messages')
    op.drop_index('ix_staff_messages_hotel_id', table_name='staff_messages')
    op.drop_table('staff_messages')
