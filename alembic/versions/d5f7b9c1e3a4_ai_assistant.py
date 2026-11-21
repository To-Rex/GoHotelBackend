"""ai assistant — "Yordamchi" sahifasi jadvallari

Sun'iy intellekt yordamchisi: mehmonxona sozlamasi (provider, model,
shifrlangan API kaliti, limitlar), kim ishlata olishi va xodim limiti,
suhbatlar va xabarlar (token sarfi bilan), AI taklif qilgan amallar
(tasdiq va orqaga qaytarish), xavfli so'rovlar jurnali, rejalar
(eslatmalar, ishlar).

Hamma jadval yangi — mavjud ma'lumotlarga tegilmaydi.

Revision ID: d5f7b9c1e3a4
Revises: c4e6a8b0d2f3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f7b9c1e3a4"
down_revision: Union[str, None] = "c4e6a8b0d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "ai_configs",
        sa.Column("hotel_id", UUID, sa.ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider", sa.String(20), nullable=False, server_default="openai"),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("base_url", sa.String(300), nullable=True),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("api_key_hint", sa.String(8), nullable=True),
        sa.Column("default_monthly_limit", sa.Integer(), nullable=True),
        sa.Column("hotel_monthly_limit", sa.Integer(), nullable=True),
        sa.Column("price_input", sa.Float(), nullable=True),
        sa.Column("price_output", sa.Float(), nullable=True),
        sa.Column("mask_sensitive", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "ai_members",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, sa.ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("monthly_limit", sa.Integer(), nullable=True),
        sa.Column("updated_by", UUID, nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("hotel_id", "user_id", name="uq_ai_members_hotel_user"),
    )
    op.create_index("ix_ai_members_hotel_id", "ai_members", ["hotel_id"])
    op.create_table(
        "ai_conversations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, sa.ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID, nullable=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False, server_default="Yangi suhbat"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_conversations_hotel_id", "ai_conversations", ["hotel_id"])
    op.create_index("ix_ai_conversations_user_updated", "ai_conversations", ["user_id", "updated_at"])
    op.create_table(
        "ai_messages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("conversation_id", UUID, sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hotel_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("blocks", JSONB, nullable=True),
        sa.Column("steps", JSONB, nullable=True),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_messages_conversation", "ai_messages", ["conversation_id", "created_at"])
    op.create_index("ix_ai_messages_usage", "ai_messages", ["hotel_id", "user_id", "created_at"])
    op.create_table(
        "ai_actions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=True),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("conversation_id", UUID, sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", UUID, nullable=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("params", JSONB, nullable=True),
        sa.Column("body", JSONB, nullable=True),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("undo", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("executed_at", TS, nullable=True),
        sa.Column("undone_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_actions_hotel_id", "ai_actions", ["hotel_id"])
    op.create_index("ix_ai_actions_conversation", "ai_actions", ["conversation_id"])
    op.create_table(
        "ai_incidents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("conversation_id", UUID, nullable=True),
        sa.Column("message_id", UUID, nullable=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_incidents_hotel_created", "ai_incidents", ["hotel_id", "created_at"])
    op.create_table(
        "ai_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(12), nullable=False, server_default="task"),
        sa.Column("due_at", TS, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("notified_at", TS, nullable=True),
        sa.Column("done_at", TS, nullable=True),
        sa.Column("created_via", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("conversation_id", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_plans_user_status", "ai_plans", ["user_id", "status"])
    op.create_index("ix_ai_plans_due", "ai_plans", ["status", "due_at"])
    op.create_table(
        "ai_files",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("hotel_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column(
            "conversation_id", UUID, sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_files_user_created", "ai_files", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("ai_files")
    op.drop_table("ai_plans")
    op.drop_table("ai_incidents")
    op.drop_table("ai_actions")
    op.drop_table("ai_messages")
    op.drop_table("ai_conversations")
    op.drop_table("ai_members")
    op.drop_table("ai_configs")
