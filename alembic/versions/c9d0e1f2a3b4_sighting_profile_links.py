"""face_sightings: qaysi shablon mos keldi / nima o'rganildi

"Bu u emas" tugmasi uchun: xodim moslikni bekor qilganda shu ko'rinishdan
o'rganilgan shablon o'chiriladi, mos kelgan shablon avtomatik o'rganilgan
bo'lsa u ham — xato indeksda qolib ketmasin.

Revision ID: c9d0e1f2a3b4
Revises: a6b7c8d9e0f1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "face_sightings",
        sa.Column("matched_profile_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "face_sightings",
        sa.Column("learned_profile_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "face_sightings",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "face_sightings",
        sa.Column("rejected_by", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_face_sightings_matched_profile",
        "face_sightings",
        "guest_face_profiles",
        ["matched_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_face_sightings_learned_profile",
        "face_sightings",
        "guest_face_profiles",
        ["learned_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_face_sightings_rejected_by",
        "face_sightings",
        "users",
        ["rejected_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_face_sightings_rejected_by", "face_sightings", type_="foreignkey")
    op.drop_constraint("fk_face_sightings_learned_profile", "face_sightings", type_="foreignkey")
    op.drop_constraint("fk_face_sightings_matched_profile", "face_sightings", type_="foreignkey")
    op.drop_column("face_sightings", "rejected_by")
    op.drop_column("face_sightings", "rejected_at")
    op.drop_column("face_sightings", "learned_profile_id")
    op.drop_column("face_sightings", "matched_profile_id")
