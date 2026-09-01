"""guest face recognition

Mehmonni yuzidan tanish uchun uchta jadval:

* ``guest_face_profiles`` — mehmon yuz shabloni. Vektor JSON matn emas,
  paketlangan float32 (``BYTEA``, 512 bayt): 1:N qidiruvda minglab qatorni
  JSON'dan ochish soniyalarga aylanadi, ``np.frombuffer`` esa bir zumda
  ishlaydi.
* ``face_sightings`` — kamera ko'rgan epizodlar. ``track_uid`` UNIQUE, chunki
  agentning offline navbati bir epizodni qayta yuborishi mumkin va panel uni
  ikki marta ko'rsatmasligi kerak.
* ``vision_devices`` — kamera agentlari uchun muddatsiz qurilma tokenlari
  (xodim JWT'si ikki soatda tugaydi, agent esa oylab ishlaydi).

Shuningdek ``guests.face_consent_at`` — biometrik rozilikning yagona manbasi.

Revision ID: u8d9e0f1a2b3
Revises: t7c8d9e0f1a2
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'u8d9e0f1a2b3'
down_revision: Union[str, None] = 't7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- rozilik ----------------------------------------------------------
    op.add_column(
        'guests',
        sa.Column('face_consent_at', sa.DateTime(timezone=True), nullable=True),
    )

    # -- mehmon yuz shablonlari -------------------------------------------
    op.create_table(
        'guest_face_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('guest_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('guests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('embedding', sa.LargeBinary(), nullable=False),
        sa.Column('dim', sa.SmallInteger(), nullable=False, server_default='128'),
        sa.Column('model', sa.String(length=32), nullable=False,
                  server_default='sface_2021dec'),
        sa.Column('sample_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('cohesion', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('quality', sa.Float(), nullable=False, server_default='0'),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='vision'),
        sa.Column('camera_id', sa.String(length=64), nullable=True),
        sa.Column('match_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_matched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_guest_face_profiles_guest_id', 'guest_face_profiles', ['guest_id'])
    op.create_index('ix_guest_face_profiles_hotel_id', 'guest_face_profiles', ['hotel_id'])
    # Indeks butun mehmonxona bo'yicha bir marta yuklanadi — bu kompozit
    # indeks o'sha yuklashni ketma-ket o'qishga aylantiradi.
    op.create_index(
        'ix_guest_face_profiles_hotel_guest', 'guest_face_profiles',
        ['hotel_id', 'guest_id'],
    )

    # -- kamera ko'rinishlari ----------------------------------------------
    op.create_table(
        'face_sightings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('branch_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('branches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('camera_id', sa.String(length=64), nullable=False),
        sa.Column('camera_name', sa.String(length=128), nullable=True),
        sa.Column('location', sa.String(length=128), nullable=True),
        sa.Column('device_id', sa.String(length=128), nullable=True),
        sa.Column('track_uid', sa.String(length=64), nullable=False),
        sa.Column('capture_id', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='unknown'),
        sa.Column('guest_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('guests.id', ondelete='SET NULL'), nullable=True),
        sa.Column('similarity', sa.Float(), nullable=False, server_default='0'),
        sa.Column('margin', sa.Float(), nullable=False, server_default='0'),
        sa.Column('quality_score', sa.Float(), nullable=False, server_default='0'),
        sa.Column('sample_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cohesion', sa.Float(), nullable=False, server_default='0'),
        sa.Column('embedding', sa.LargeBinary(), nullable=True),
        sa.Column('thumbnail', sa.LargeBinary(), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reservation_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('reservations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('track_uid', name='uq_face_sightings_track_uid'),
    )
    op.create_index('ix_face_sightings_hotel_seen', 'face_sightings',
                    ['hotel_id', 'seen_at'])
    op.create_index('ix_face_sightings_seen_at', 'face_sightings', ['seen_at'])
    op.create_index('ix_face_sightings_guest_id', 'face_sightings', ['guest_id'])
    op.create_index('ix_face_sightings_expires', 'face_sightings', ['expires_at'])

    # -- kamera agentlari ---------------------------------------------------
    op.create_table(
        'vision_devices',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hotel_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('hotels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('branch_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('branches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('device_id', sa.String(length=128), nullable=True),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('token_hint', sa.String(length=8), nullable=False, server_default=''),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('events_received', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_vision_devices_hotel_id', 'vision_devices', ['hotel_id'])
    # Bitta UNIKAL indeks, alohida UNIQUE cheklov + oddiy indeks emas. Ikkalasi
    # ham unikallikni ta'minlaydi, lekin modelda `unique=True, index=True` aynan
    # shu — unikal indeks. Farq qilsa, keyingi `alembic revision --autogenerate`
    # har safar buni "tuzatmoqchi" bo'lib turadi.
    op.create_index(
        'ix_vision_devices_token_hash', 'vision_devices', ['token_hash'], unique=True
    )


def downgrade() -> None:
    op.drop_index('ix_vision_devices_token_hash', table_name='vision_devices')
    op.drop_index('ix_vision_devices_hotel_id', table_name='vision_devices')
    op.drop_table('vision_devices')

    op.drop_index('ix_face_sightings_expires', table_name='face_sightings')
    op.drop_index('ix_face_sightings_guest_id', table_name='face_sightings')
    op.drop_index('ix_face_sightings_seen_at', table_name='face_sightings')
    op.drop_index('ix_face_sightings_hotel_seen', table_name='face_sightings')
    op.drop_table('face_sightings')

    op.drop_index('ix_guest_face_profiles_hotel_guest', table_name='guest_face_profiles')
    op.drop_index('ix_guest_face_profiles_hotel_id', table_name='guest_face_profiles')
    op.drop_index('ix_guest_face_profiles_guest_id', table_name='guest_face_profiles')
    op.drop_table('guest_face_profiles')

    op.drop_column('guests', 'face_consent_at')
