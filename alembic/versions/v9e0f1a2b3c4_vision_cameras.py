"""vision cameras

Kamerani filialga biriktirish. Filial faqat qurilmada (kompyuterda) saqlansa,
bitta agent boqayotgan barcha kameralar bitta filialga yozilardi — qabulxona
xodimi esa yonidagi filialning odamlarini ko'rib qolardi.

``face_sightings.branch_id`` allaqachon bor edi, lekin u qurilmadan olinardi.
Endi u kameradan olinadi va shu ustun bo'yicha indeks qo'shiladi, chunki
qabulxona ro'yxati aynan "shu filial, oxirgi N daqiqa" bo'yicha so'raladi.

Revision ID: v9e0f1a2b3c4
Revises: u8d9e0f1a2b3
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'v9e0f1a2b3c4'
down_revision: Union[str, None] = 'u8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vision_cameras',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('branch_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('branches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('device_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('vision_devices.id', ondelete='CASCADE'), nullable=False),
        sa.Column('camera_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=True),
        sa.Column('location', sa.String(length=128), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sightings_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('device_id', 'camera_id',
                            name='uq_vision_cameras_device_camera'),
    )
    op.create_index('ix_vision_cameras_hotel_id', 'vision_cameras', ['hotel_id'])
    op.create_index('ix_vision_cameras_branch_id', 'vision_cameras', ['branch_id'])
    op.create_index('ix_vision_cameras_device_id', 'vision_cameras', ['device_id'])

    # Qabulxona ro'yxati "shu mehmonxona, shu filial, oxirgi N daqiqa" bo'yicha
    # so'raladi — mavjud (hotel_id, seen_at) indeksi filialni qamramaydi.
    op.create_index(
        'ix_face_sightings_branch_seen', 'face_sightings', ['branch_id', 'seen_at']
    )


def downgrade() -> None:
    op.drop_index('ix_face_sightings_branch_seen', table_name='face_sightings')
    op.drop_index('ix_vision_cameras_device_id', table_name='vision_cameras')
    op.drop_index('ix_vision_cameras_branch_id', table_name='vision_cameras')
    op.drop_index('ix_vision_cameras_hotel_id', table_name='vision_cameras')
    op.drop_table('vision_cameras')
