"""initial_schema

Revision ID: 250c3edb6dfe
Revises:
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = '250c3edb6dfe'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. hotels
    # ------------------------------------------------------------------
    op.create_table(
        'hotels',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('stars', sa.SmallInteger(), nullable=False),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('address_line1', sa.String(255), nullable=True),
        sa.Column('address_line2', sa.String(255), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('state', sa.String(100), nullable=True),
        sa.Column('country', sa.String(100), nullable=True),
        sa.Column('postal_code', sa.String(20), nullable=True),
        sa.Column('status', sa.String(20), nullable=False,
                  server_default='ACTIVE'),
        sa.Column('code', sa.String(10), nullable=False),
        sa.Column('settings', JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
        sa.CheckConstraint('stars >= 1 AND stars <= 5',
                           name='ck_hotels_stars'),
    )

    # ------------------------------------------------------------------
    # 2. branches
    # ------------------------------------------------------------------
    op.create_table(
        'branches',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('code', sa.String(20), nullable=False),
        sa.Column('address_line1', sa.String(255), nullable=True),
        sa.Column('address_line2', sa.String(255), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('state', sa.String(100), nullable=True),
        sa.Column('country', sa.String(100), nullable=True),
        sa.Column('postal_code', sa.String(20), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('is_main_branch', sa.Boolean(), nullable=True,
                  server_default=sa.text('false')),
        sa.Column('status', sa.String(20), nullable=True,
                  server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.UniqueConstraint('hotel_id', 'code'),
    )

    # ------------------------------------------------------------------
    # 3. users
    # ------------------------------------------------------------------
    op.create_table(
        'users',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_type', sa.String(20), nullable=False),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=True),
        sa.Column('branch_id', UUID(as_uuid=True), nullable=True),
        sa.Column('username', sa.String(100), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('first_name', sa.String(100), nullable=False),
        sa.Column('last_name', sa.String(100), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('status', sa.String(20), nullable=True,
                  server_default='ACTIVE'),
        sa.Column('hire_date', sa.Date(), nullable=True),
        sa.Column('termination_date', sa.Date(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True,
                  server_default=sa.text('false')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.UniqueConstraint('username'),
    )

    # ------------------------------------------------------------------
    # 4. user_sessions
    # ------------------------------------------------------------------
    op.create_table(
        'user_sessions',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('token_jti', sa.String(64), nullable=False),
        sa.Column('refresh_token_hash', sa.String(255), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_address', sa.String(50), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                ondelete='CASCADE'),
        sa.UniqueConstraint('token_jti'),
    )

    # ------------------------------------------------------------------
    # 5. permissions
    # ------------------------------------------------------------------
    op.create_table(
        'permissions',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('code', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('module', sa.String(50), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    # ------------------------------------------------------------------
    # 6. user_permissions
    # ------------------------------------------------------------------
    op.create_table(
        'user_permissions',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('permission_id', UUID(as_uuid=True), nullable=False),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('granted_by', UUID(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id']),
        sa.UniqueConstraint('user_id', 'permission_id'),
    )

    # ------------------------------------------------------------------
    # 7. floors
    # ------------------------------------------------------------------
    op.create_table(
        'floors',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('branch_id', UUID(as_uuid=True), nullable=False),
        sa.Column('floor_number', sa.SmallInteger(), nullable=False),
        sa.Column('name', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.UniqueConstraint('branch_id', 'floor_number'),
    )

    # ------------------------------------------------------------------
    # 8. room_types
    # ------------------------------------------------------------------
    op.create_table(
        'room_types',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('capacity', sa.SmallInteger(), nullable=True,
                  server_default='1'),
        sa.Column('base_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('amenities', JSONB(), nullable=True,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column('is_active', sa.Boolean(), nullable=True,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.UniqueConstraint('hotel_id', 'name'),
    )

    # ------------------------------------------------------------------
    # 9. rooms
    # ------------------------------------------------------------------
    op.create_table(
        'rooms',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('branch_id', UUID(as_uuid=True), nullable=False),
        sa.Column('floor_id', UUID(as_uuid=True), nullable=False),
        sa.Column('room_type_id', UUID(as_uuid=True), nullable=False),
        sa.Column('room_number', sa.String(20), nullable=False),
        sa.Column('current_status', sa.String(20), nullable=True,
                  server_default='AVAILABLE'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True,
                  server_default=sa.text('false')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['floor_id'], ['floors.id']),
        sa.ForeignKeyConstraint(['room_type_id'], ['room_types.id']),
        sa.UniqueConstraint('branch_id', 'room_number'),
    )

    # ------------------------------------------------------------------
    # 10. room_status_history
    # ------------------------------------------------------------------
    op.create_table(
        'room_status_history',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('room_id', UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('changed_by', UUID(as_uuid=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id']),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id']),
    )

    # ------------------------------------------------------------------
    # 11. guests
    # ------------------------------------------------------------------
    op.create_table(
        'guests',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('first_name', sa.String(100), nullable=False),
        sa.Column('last_name', sa.String(100), nullable=False),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('passport_number', sa.String(100), nullable=True),
        sa.Column('nationality', sa.String(100), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('id_document_type', sa.String(50), nullable=True),
        sa.Column('id_document_number', sa.String(100), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True,
                  server_default=sa.text('false')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
    )

    # ------------------------------------------------------------------
    # 12. reservations
    # ------------------------------------------------------------------
    op.create_table(
        'reservations',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('branch_id', UUID(as_uuid=True), nullable=False),
        sa.Column('reservation_number', sa.String(50), nullable=False),
        sa.Column('guest_id', UUID(as_uuid=True), nullable=False),
        sa.Column('room_id', UUID(as_uuid=True), nullable=False),
        sa.Column('check_in_date', sa.Date(), nullable=False),
        sa.Column('check_out_date', sa.Date(), nullable=False),
        sa.Column('adults', sa.SmallInteger(), nullable=True,
                  server_default='1'),
        sa.Column('children', sa.SmallInteger(), nullable=True,
                  server_default='0'),
        sa.Column('status', sa.String(20), nullable=True,
                  server_default='PENDING'),
        sa.Column('discount_amount', sa.Numeric(12, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('discount_percent', sa.Numeric(5, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=False),
        sa.Column('cancelled_reason', sa.Text(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_by', UUID(as_uuid=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True,
                  server_default=sa.text('false')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['guest_id'], ['guests.id']),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['cancelled_by'], ['users.id']),
        sa.UniqueConstraint('reservation_number'),
        sa.CheckConstraint('check_out_date > check_in_date',
                           name='ck_reservations_dates'),
    )

    # ------------------------------------------------------------------
    # 13. services
    # ------------------------------------------------------------------
    op.create_table(
        'services',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    # ------------------------------------------------------------------
    # 14. hotel_services
    # ------------------------------------------------------------------
    op.create_table(
        'hotel_services',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('service_id', UUID(as_uuid=True), nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['service_id'], ['services.id']),
        sa.UniqueConstraint('hotel_id', 'service_id'),
    )

    # ------------------------------------------------------------------
    # 15. reservation_services
    # ------------------------------------------------------------------
    op.create_table(
        'reservation_services',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('reservation_id', UUID(as_uuid=True), nullable=False),
        sa.Column('hotel_service_id', UUID(as_uuid=True), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=True,
                  server_default='1'),
        sa.Column('unit_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('total_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('service_date', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['hotel_service_id'], ['hotel_services.id']),
    )

    # ------------------------------------------------------------------
    # 16. housekeeping_tasks
    # ------------------------------------------------------------------
    op.create_table(
        'housekeeping_tasks',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('branch_id', UUID(as_uuid=True), nullable=False),
        sa.Column('room_id', UUID(as_uuid=True), nullable=False),
        sa.Column('task_type', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=True,
                  server_default='OPEN'),
        sa.Column('priority', sa.String(20), nullable=True,
                  server_default='MEDIUM'),
        sa.Column('assigned_to', UUID(as_uuid=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('scheduled_date', sa.Date(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id']),
        sa.ForeignKeyConstraint(['assigned_to'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
    )

    # ------------------------------------------------------------------
    # 17. ledgers
    # ------------------------------------------------------------------
    op.create_table(
        'ledgers',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('type', sa.String(20), nullable=False),
        sa.Column('parent_id', UUID(as_uuid=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['parent_id'], ['ledgers.id']),
        sa.UniqueConstraint('hotel_id', 'code'),
    )

    # ------------------------------------------------------------------
    # 18. journal_entries
    # ------------------------------------------------------------------
    op.create_table(
        'journal_entries',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('entry_number', sa.String(50), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('reference_type', sa.String(50), nullable=True),
        sa.Column('reference_id', UUID(as_uuid=True), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('total_debit', sa.Numeric(14, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('total_credit', sa.Numeric(14, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('status', sa.String(20), nullable=True,
                  server_default='DRAFT'),
        sa.Column('posted_by', UUID(as_uuid=True), nullable=True),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['posted_by'], ['users.id']),
        sa.UniqueConstraint('hotel_id', 'entry_number'),
    )

    # ------------------------------------------------------------------
    # 19. journal_entry_lines
    # ------------------------------------------------------------------
    op.create_table(
        'journal_entry_lines',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('journal_entry_id', UUID(as_uuid=True), nullable=False),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('ledger_id', UUID(as_uuid=True), nullable=False),
        sa.Column('debit', sa.Numeric(14, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('credit', sa.Numeric(14, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['journal_entry_id'],
                                ['journal_entries.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['ledger_id'], ['ledgers.id']),
    )

    # ------------------------------------------------------------------
    # 20. invoices
    # ------------------------------------------------------------------
    op.create_table(
        'invoices',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('reservation_id', UUID(as_uuid=True), nullable=False),
        sa.Column('guest_id', UUID(as_uuid=True), nullable=False),
        sa.Column('invoice_number', sa.String(50), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('subtotal', sa.Numeric(12, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('tax_amount', sa.Numeric(12, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('discount_amount', sa.Numeric(12, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('paid_amount', sa.Numeric(12, 2), nullable=True,
                  server_default=sa.text('0')),
        sa.Column('status', sa.String(20), nullable=True,
                  server_default='DRAFT'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id']),
        sa.ForeignKeyConstraint(['guest_id'], ['guests.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.UniqueConstraint('hotel_id', 'invoice_number'),
    )

    # ------------------------------------------------------------------
    # 21. invoice_line_items
    # ------------------------------------------------------------------
    op.create_table(
        'invoice_line_items',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('invoice_id', UUID(as_uuid=True), nullable=False),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('description', sa.String(500), nullable=False),
        sa.Column('line_type', sa.String(20), nullable=False),
        sa.Column('reference_type', sa.String(50), nullable=True),
        sa.Column('reference_id', UUID(as_uuid=True), nullable=True),
        sa.Column('quantity', sa.Numeric(10, 2), nullable=True,
                  server_default='1'),
        sa.Column('unit_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('total_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
    )

    # ------------------------------------------------------------------
    # 22. payments
    # ------------------------------------------------------------------
    op.create_table(
        'payments',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('invoice_id', UUID(as_uuid=True), nullable=False),
        sa.Column('payment_number', sa.String(50), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('payment_method', sa.String(20), nullable=False),
        sa.Column('payment_date', sa.Date(), nullable=False),
        sa.Column('reference', sa.String(255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.UniqueConstraint('hotel_id', 'payment_number'),
    )

    # ------------------------------------------------------------------
    # 23. audit_logs
    # ------------------------------------------------------------------
    op.create_table(
        'audit_logs',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), nullable=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', UUID(as_uuid=True), nullable=False),
        sa.Column('old_values', JSONB(), nullable=True),
        sa.Column('new_values', JSONB(), nullable=True),
        sa.Column('ip_address', sa.String(50), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )

    # ------------------------------------------------------------------
    # 24. file_attachments
    # ------------------------------------------------------------------
    op.create_table(
        'file_attachments',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', UUID(as_uuid=True), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('original_name', sa.String(500), nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('minio_bucket', sa.String(100), nullable=False),
        sa.Column('minio_path', sa.String(500), nullable=False),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('uploaded_by', UUID(as_uuid=True), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=True,
                  server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id']),
    )

    # ------------------------------------------------------------------
    # 25. notifications
    # ------------------------------------------------------------------
    op.create_table(
        'notifications',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), nullable=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('entity_type', sa.String(50), nullable=True),
        sa.Column('entity_id', UUID(as_uuid=True), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=True,
                  server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )

    # ------------------------------------------------------------------
    # 26. reports
    # ------------------------------------------------------------------
    op.create_table(
        'reports',
        sa.Column('id', UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('hotel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('report_type', sa.String(100), nullable=False),
        sa.Column('parameters', JSONB(), nullable=True,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column('result_data', JSONB(), nullable=True),
        sa.Column('generated_by', UUID(as_uuid=True), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id']),
        sa.ForeignKeyConstraint(['generated_by'], ['users.id']),
    )

    # ==================================================================
    # INDEXES
    # ==================================================================
    op.create_index('idx_branches_hotel_id', 'branches', ['hotel_id'])
    op.create_index('idx_floors_branch_id', 'floors', ['branch_id'])
    op.create_index('idx_floors_hotel_id', 'floors', ['hotel_id'])
    op.create_index('idx_room_types_hotel_id', 'room_types', ['hotel_id'])
    op.create_index('idx_rooms_hotel_id', 'rooms', ['hotel_id'])
    op.create_index('idx_rooms_branch_id', 'rooms', ['branch_id'])
    op.create_index('idx_rooms_floor_id', 'rooms', ['floor_id'])
    op.create_index('idx_rooms_room_type_id', 'rooms', ['room_type_id'])
    op.create_index('idx_rooms_status', 'rooms',
                    ['hotel_id', 'current_status'])
    op.create_index('idx_room_status_history_room_id', 'room_status_history',
                    ['room_id', sa.text('created_at DESC')])
    op.create_index('idx_users_hotel_id', 'users', ['hotel_id'])
    op.create_index('idx_user_sessions_user_id', 'user_sessions',
                    ['user_id'])
    op.create_index('idx_user_sessions_token_jti', 'user_sessions',
                    ['token_jti'])
    op.create_index('idx_emp_permissions_employee_id', 'user_permissions',
                    ['user_id'])
    op.create_index('idx_emp_permissions_hotel_id', 'user_permissions',
                    ['hotel_id'])
    op.create_index('idx_guests_hotel_id', 'guests', ['hotel_id'])
    op.create_index('idx_guests_name', 'guests',
                    ['hotel_id', 'last_name', 'first_name'])
    op.create_index('idx_guests_passport', 'guests',
                    ['hotel_id', 'passport_number'])
    op.create_index('idx_reservations_hotel_id', 'reservations',
                    ['hotel_id'])
    op.create_index('idx_reservations_guest_id', 'reservations',
                    ['guest_id'])
    op.create_index('idx_reservations_status', 'reservations',
                    ['hotel_id', 'status'])
    op.create_index('idx_reservations_dates', 'reservations',
                    ['hotel_id', 'check_in_date', 'check_out_date'])
    op.create_index('idx_services_category', 'services', ['category'])
    op.create_index('idx_hotel_services_hotel_id', 'hotel_services',
                    ['hotel_id'])
    op.create_index('idx_reservation_services_reserv_id',
                    'reservation_services', ['reservation_id'])
    op.create_index('idx_hk_tasks_hotel_id', 'housekeeping_tasks',
                    ['hotel_id'])
    op.create_index('idx_hk_tasks_room_id', 'housekeeping_tasks',
                    ['room_id'])
    op.create_index('idx_hk_tasks_assigned_to', 'housekeeping_tasks',
                    ['assigned_to'])
    op.create_index('idx_hk_tasks_status', 'housekeeping_tasks',
                    ['hotel_id', 'status'])
    op.create_index('idx_ledgers_hotel_id', 'ledgers', ['hotel_id'])
    op.create_index('idx_ledgers_type', 'ledgers',
                    ['hotel_id', 'type'])
    op.create_index('idx_journal_entries_hotel_id', 'journal_entries',
                    ['hotel_id'])
    op.create_index('idx_journal_entries_date', 'journal_entries',
                    ['hotel_id', 'entry_date'])
    op.create_index('idx_journal_lines_entry_id', 'journal_entry_lines',
                    ['journal_entry_id'])
    op.create_index('idx_journal_lines_ledger_id', 'journal_entry_lines',
                    ['ledger_id'])
    op.create_index('idx_invoices_hotel_id', 'invoices', ['hotel_id'])
    op.create_index('idx_invoices_reservation_id', 'invoices',
                    ['reservation_id'])
    op.create_index('idx_invoices_status', 'invoices',
                    ['hotel_id', 'status'])
    op.create_index('idx_invoice_lines_invoice_id', 'invoice_line_items',
                    ['invoice_id'])
    op.create_index('idx_payments_hotel_id', 'payments', ['hotel_id'])
    op.create_index('idx_payments_invoice_id', 'payments', ['invoice_id'])
    op.create_index('idx_audit_logs_hotel_id', 'audit_logs',
                    ['hotel_id', sa.text('created_at DESC')])
    op.create_index('idx_audit_logs_entity', 'audit_logs',
                    ['entity_type', 'entity_id'])
    op.create_index('idx_file_attachments_entity', 'file_attachments',
                    ['entity_type', 'entity_id'])
    op.create_index('idx_notifications_user_unread', 'notifications',
                    ['user_id', 'is_read', sa.text('created_at DESC')])
    op.create_index('idx_reports_hotel_id', 'reports', ['hotel_id'])


def downgrade() -> None:
    # Drop all indexes first
    op.drop_index('idx_reports_hotel_id', table_name='reports')
    op.drop_index('idx_notifications_user_unread',
                  table_name='notifications')
    op.drop_index('idx_file_attachments_entity',
                  table_name='file_attachments')
    op.drop_index('idx_audit_logs_entity', table_name='audit_logs')
    op.drop_index('idx_audit_logs_hotel_id', table_name='audit_logs')
    op.drop_index('idx_payments_invoice_id', table_name='payments')
    op.drop_index('idx_payments_hotel_id', table_name='payments')
    op.drop_index('idx_invoice_lines_invoice_id',
                  table_name='invoice_line_items')
    op.drop_index('idx_invoices_status', table_name='invoices')
    op.drop_index('idx_invoices_reservation_id', table_name='invoices')
    op.drop_index('idx_invoices_hotel_id', table_name='invoices')
    op.drop_index('idx_journal_lines_ledger_id',
                  table_name='journal_entry_lines')
    op.drop_index('idx_journal_lines_entry_id',
                  table_name='journal_entry_lines')
    op.drop_index('idx_journal_entries_date', table_name='journal_entries')
    op.drop_index('idx_journal_entries_hotel_id',
                  table_name='journal_entries')
    op.drop_index('idx_ledgers_type', table_name='ledgers')
    op.drop_index('idx_ledgers_hotel_id', table_name='ledgers')
    op.drop_index('idx_hk_tasks_status', table_name='housekeeping_tasks')
    op.drop_index('idx_hk_tasks_assigned_to',
                  table_name='housekeeping_tasks')
    op.drop_index('idx_hk_tasks_room_id', table_name='housekeeping_tasks')
    op.drop_index('idx_hk_tasks_hotel_id', table_name='housekeeping_tasks')
    op.drop_index('idx_reservation_services_reserv_id',
                  table_name='reservation_services')
    op.drop_index('idx_hotel_services_hotel_id',
                  table_name='hotel_services')
    op.drop_index('idx_services_category', table_name='services')
    op.drop_index('idx_reservations_dates', table_name='reservations')
    op.drop_index('idx_reservations_status', table_name='reservations')
    op.drop_index('idx_reservations_guest_id', table_name='reservations')
    op.drop_index('idx_reservations_hotel_id', table_name='reservations')
    op.drop_index('idx_guests_passport', table_name='guests')
    op.drop_index('idx_guests_name', table_name='guests')
    op.drop_index('idx_guests_hotel_id', table_name='guests')
    op.drop_index('idx_emp_permissions_hotel_id',
                  table_name='user_permissions')
    op.drop_index('idx_emp_permissions_employee_id',
                  table_name='user_permissions')
    op.drop_index('idx_user_sessions_token_jti',
                  table_name='user_sessions')
    op.drop_index('idx_user_sessions_user_id',
                  table_name='user_sessions')
    op.drop_index('idx_users_hotel_id', table_name='users')
    op.drop_index('idx_room_status_history_room_id',
                  table_name='room_status_history')
    op.drop_index('idx_rooms_status', table_name='rooms')
    op.drop_index('idx_rooms_room_type_id', table_name='rooms')
    op.drop_index('idx_rooms_floor_id', table_name='rooms')
    op.drop_index('idx_rooms_branch_id', table_name='rooms')
    op.drop_index('idx_rooms_hotel_id', table_name='rooms')
    op.drop_index('idx_room_types_hotel_id', table_name='room_types')
    op.drop_index('idx_floors_hotel_id', table_name='floors')
    op.drop_index('idx_floors_branch_id', table_name='floors')
    op.drop_index('idx_branches_hotel_id', table_name='branches')

    # Drop tables in reverse dependency order
    op.drop_table('reports')
    op.drop_table('notifications')
    op.drop_table('file_attachments')
    op.drop_table('audit_logs')
    op.drop_table('payments')
    op.drop_table('invoice_line_items')
    op.drop_table('invoices')
    op.drop_table('journal_entry_lines')
    op.drop_table('journal_entries')
    op.drop_table('ledgers')
    op.drop_table('housekeeping_tasks')
    op.drop_table('reservation_services')
    op.drop_table('hotel_services')
    op.drop_table('services')
    op.drop_table('reservations')
    op.drop_table('guests')
    op.drop_table('room_status_history')
    op.drop_table('rooms')
    op.drop_table('room_types')
    op.drop_table('floors')
    op.drop_table('user_permissions')
    op.drop_table('permissions')
    op.drop_table('user_sessions')
    op.drop_table('users')
    op.drop_table('branches')
    op.drop_table('hotels')
