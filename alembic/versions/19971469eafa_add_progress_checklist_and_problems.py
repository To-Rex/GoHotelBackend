"""add_progress_checklist_and_problems

Revision ID: 19971469eafa
Revises: a7b8c9d0e1f2
Create Date: 2026-07-07 14:39:25.259015
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '19971469eafa'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'housekeeping_tasks',
        sa.Column('progress', sa.Integer(), nullable=False, server_default=sa.text('0'))
    )

    op.create_table(
        'checklist_items',
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('is_completed', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['housekeeping_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table(
        'problems',
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=True),
        sa.Column('room_id', sa.UUID(), nullable=True),
        sa.Column('task_id', sa.UUID(), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column('reported_by', sa.UUID(), nullable=False),
        sa.Column('room_number', sa.String(length=20), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['reported_by'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['task_id'], ['housekeeping_tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('problems')
    op.drop_table('checklist_items')
    op.drop_column('housekeeping_tasks', 'progress')
