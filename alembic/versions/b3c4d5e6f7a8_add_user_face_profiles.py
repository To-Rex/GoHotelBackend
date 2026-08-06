"""add user face profiles

DIQQAT: bu fayl production sxemasidan QAYTA TIKLANGAN. b3c4d5e6f7a8
migratsiyasi boshqa ish stansiyasidan to'g'ridan-to'g'ri bazaga qo'llangan,
lekin fayli repozitoriyga yuklanmagan — natijada serverdagi
`alembic upgrade head` bu revisionni topolmay yiqilardi (502). Jadval
tuzilishi pg_catalog'dan aynan ko'chirilgan.

Revision ID: b3c4d5e6f7a8
Revises: i4d5e6f7a8b9
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'i4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_face_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('embedding', sa.Text(), nullable=False),
        sa.Column('device_label', sa.String(255), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_user_face_profiles_user_id', 'user_face_profiles', ['user_id'])


def downgrade() -> None:
    op.drop_table('user_face_profiles')
