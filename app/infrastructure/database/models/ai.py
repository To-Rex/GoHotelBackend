"""Sun'iy intellekt yordamchisi ("Yordamchi" sahifasi) jadvallari.

* `ai_configs` — mehmonxona sozlamasi: provider, model, API kaliti
  (Fernet bilan SHIFRLANGAN), limitlar. Sozlovchi o'zgartiradi.
* `ai_members` — kim ishlata oladi (hatto administrator ham ro'yxatda
  bo'lmasa — yo'q) va xodimning oylik token limiti.
* `ai_conversations` / `ai_messages` — har xodimning O'Z suhbatlari;
  xabarda matn, bloklar (jadval, grafik, fayl, amal kartasi...) va
  token sarfi.
* `ai_actions` — AI taklif qilgan YOZUVCHI amallar: xodim tasdiqlamaguncha
  bajarilmaydi; bajarilganidan keyin orqaga qaytarish rejasi saqlanadi.
* `ai_incidents` — xavfli so'rovlar (ma'lumotni o'chirishga urinish,
  qo'rqitish, rolidan tashqari so'rov...) — superadmin va sozlovchiga.
* `ai_plans` — rejalar: eslatmalar va ishlar (vaqti kelganda bildirishnoma).
* `ai_files` — yordamchi tayyorlagan Excel hisobotlar (bazada).

Batafsil: app/application/services/ai/.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.shared.mixins import FullMixin, UUIDPrimaryKeyMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class AiConfig(Base):
    """Mehmonxona bo'yicha AI sozlamasi (bitta qator)."""

    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_configs"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: openai | anthropic | deepseek | deploy | custom
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="openai")
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    #: OpenAI bilan mos boshqa xizmatlar uchun manzil (https)
    base_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    #: API kaliti — Fernet bilan shifrlangan; hech qachon javobda qaytmaydi
    api_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Kalitning oxirgi 4 belgisi — sozlamada "••••abcd" ko'rsatish uchun
    api_key_hint: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    #: Xodim boshiga oylik token limiti (bo'sh — cheklanmagan)
    default_monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: Mehmonxona bo'yicha jami oylik limit (bo'sh — cheklanmagan)
    hotel_monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: Narx (USD / 1M token) — xarajatni taxminan ko'rsatish uchun (ixtiyoriy)
    price_input: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_output: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    #: Pasport, PINFL kabi maxfiy maydonlarni AI'ga yubormaslik
    mask_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AiMember(UUIDPrimaryKeyMixin, Base):
    """Xodimning yordamchiga ruxsati va oylik token limiti."""

    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_members"
    __table_args__ = (UniqueConstraint("hotel_id", "user_id", name="uq_ai_members_hotel_user"),)

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Shaxsiy oylik limit (bo'sh — mehmonxonaning standart limiti)
    monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AiConversation(FullMixin, Base):
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_conversations"
    __table_args__ = (Index("ix_ai_conversations_user_updated", "user_id", "updated_at"),)

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="Yangi suhbat")
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AiMessage(UUIDPrimaryKeyMixin, Base):
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_messages"
    __table_args__ = (
        Index("ix_ai_messages_conversation", "conversation_id", "created_at"),
        # Token sarfi: xodim + oy
        Index("ix_ai_messages_usage", "hotel_id", "user_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    hotel_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    #: user | assistant
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Ko'rsatiladigan bloklar: jadval, grafik, ko'rsatkichlar, fayl, havola,
    #: amal kartasi, javob variantlari, reja
    blocks: Mapped[Optional[list]] = mapped_column(_JSON, nullable=True)
    #: Bajarilgan qadamlar (asbob chaqiruvlari) — ekranda "nima qilindi"
    steps: Mapped[Optional[list]] = mapped_column(_JSON, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiAction(FullMixin, Base):
    """AI taklif qilgan yozuvchi amal — xodim tasdiqlaganda bajariladi."""

    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_actions"
    __table_args__ = (Index("ix_ai_actions_conversation", "conversation_id"),)

    hotel_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    params: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    body: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    #: Amal turi (registr kaliti) va sarlavhasi
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: proposed | executed | failed | rejected | undone | undo_failed | expired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    result: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    #: Orqaga qaytarish rejasi (bajarilgandan keyin yoziladi)
    undo: Mapped[Optional[dict]] = mapped_column(_JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AiIncident(UUIDPrimaryKeyMixin, Base):
    """Xavfli so'rov: savol, javob, toifa va manba."""

    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_incidents"
    __table_args__ = (Index("ix_ai_incidents_hotel_created", "hotel_id", "created_at"),)

    hotel_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    #: data_destruction | manipulation | prompt_injection | privilege | secrets | out_of_role | other
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    #: heuristic | model | server
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiFile(UUIDPrimaryKeyMixin, Base):
    """Yordamchi tayyorlagan fayl (Excel hisobot) — bazada saqlanadi:
    suhbat tarixidan keyin ham yuklab olinadi, qayta deploy'da yo'qolmaydi."""

    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_files"
    __table_args__ = (Index("ix_ai_files_user_created", "user_id", "created_at"),)

    hotel_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiPlan(FullMixin, Base):
    """Reja: eslatma yoki ish (xodimning o'zi uchun)."""

    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "ai_plans"
    __table_args__ = (
        Index("ix_ai_plans_user_status", "user_id", "status"),
        Index("ix_ai_plans_due", "status", "due_at"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    branch_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: reminder | task | note
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="task")
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: open | done | cancelled
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: ai | manual
    created_via: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
