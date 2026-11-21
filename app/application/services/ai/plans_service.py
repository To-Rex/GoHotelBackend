"""Rejalar: xodimning o'z eslatmalari, ishlari va qaydlari.

* reminder — vaqti kelganda bildirishnoma (mobil push + sayt ichida).
* task — bajariladigan ish (muddati bo'lsa — muddatida eslatiladi).
* note — qayd (eslatma yuborilmaydi).

Yordamchi suhbatda yaratadi ("ertaga 9:00 da kassani tekshirishni eslat")
yoki xodim o'zi qo'lda qo'shadi. Reja — shaxsiy: faqat egasi ko'radi.
Biznes ma'lumotiga tegmaydi, shuning uchun tasdiq kartasisiz yoziladi
(bekor qilish — bir tugma). Hech qachon o'chirilmaydi — "bekor qilindi".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.ai import AiPlan

logger = logging.getLogger(__name__)

KINDS = ("reminder", "task", "note")
STATUSES = ("open", "done", "cancelled")
MAX_OPEN_PLANS = 300


def _offset() -> timedelta:
    from app.core.config import settings

    return timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)


def parse_local(value) -> datetime | None:
    """"2026-11-23T09:00" (mehmonxona mahalliy vaqti) yoki zonali ISO -> UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
        if len(text) == 10:
            text += "T09:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValidationException("Vaqt noto'g'ri (YYYY-MM-DDTHH:MM)", "AI_PLAN_DUE_INVALID") from exc
    if moment.tzinfo is None:
        moment = (moment - _offset()).replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def to_local_text(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return (aware.astimezone(timezone.utc) + _offset()).strftime("%Y-%m-%dT%H:%M")


def serialize(p: AiPlan) -> dict:
    now = datetime.now(timezone.utc)
    due = p.due_at if (p.due_at is None or p.due_at.tzinfo) else p.due_at.replace(tzinfo=timezone.utc)
    return {
        "id": str(p.id),
        "title": p.title,
        "note": p.note,
        "kind": p.kind,
        "status": p.status,
        "due_at": due.isoformat() if due else None,
        "due_local": to_local_text(due),
        "overdue": bool(p.status == "open" and due and due < now and p.kind != "note"),
        "notified_at": p.notified_at.isoformat() if p.notified_at else None,
        "done_at": p.done_at.isoformat() if p.done_at else None,
        "created_via": p.created_via,
        "conversation_id": str(p.conversation_id) if p.conversation_id else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


class AiPlanService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, *, hotel_id: UUID, branch_id: UUID | None, user_id: UUID, title: str, kind: str = "task",
        due_at=None, note: str | None = None, created_via: str = "manual", conversation_id: UUID | None = None,
    ) -> AiPlan:
        title = " ".join((title or "").split())[:200]
        if not title:
            raise ValidationException("Reja nomi kerak", "AI_PLAN_TITLE_REQUIRED")
        kind = kind if kind in KINDS else "task"
        due = parse_local(due_at)
        if kind == "reminder" and due is None:
            raise ValidationException("Eslatma uchun vaqt kerak", "AI_PLAN_DUE_REQUIRED")
        open_count = len(
            (
                await self.session.execute(
                    select(AiPlan.id).where(AiPlan.user_id == user_id, AiPlan.status == "open").limit(MAX_OPEN_PLANS + 1)
                )
            ).all()
        )
        if open_count >= MAX_OPEN_PLANS:
            raise ValidationException("Ochiq rejalar juda ko'p — avval eskilarini yoping", "AI_PLAN_LIMIT")
        plan = AiPlan(
            hotel_id=hotel_id, branch_id=branch_id, user_id=user_id, title=title, kind=kind, due_at=due,
            note=(note or "").strip()[:2000] or None, status="open", created_via=created_via,
            conversation_id=conversation_id,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def get_own(self, plan_id: UUID, user_id) -> AiPlan:
        plan = await self.session.get(AiPlan, plan_id)
        if plan is None or str(plan.user_id) != str(user_id):
            raise NotFoundException("Reja topilmadi", "AI_PLAN_NOT_FOUND")
        return plan

    async def list(self, user_id, *, status: str | None = "open", limit: int = 100) -> list[AiPlan]:
        q = select(AiPlan).where(AiPlan.user_id == user_id)
        if status in STATUSES:
            q = q.where(AiPlan.status == status)
        elif status == "recent":
            since = datetime.now(timezone.utc) - timedelta(days=14)
            q = q.where(or_(AiPlan.status == "open", AiPlan.updated_at >= since))
        # Muddatlilar avval (yaqinidan), keyin muddatsizlar
        q = q.order_by(AiPlan.status, AiPlan.due_at.is_(None), AiPlan.due_at, AiPlan.created_at.desc())
        return list((await self.session.execute(q.limit(max(1, min(limit, 300))))).scalars())

    async def update(self, plan: AiPlan, data: dict) -> AiPlan:
        if "title" in data and data["title"] is not None:
            title = " ".join(str(data["title"]).split())[:200]
            if not title:
                raise ValidationException("Reja nomi kerak", "AI_PLAN_TITLE_REQUIRED")
            plan.title = title
        if "note" in data:
            plan.note = (str(data["note"] or "")).strip()[:2000] or None
        if "kind" in data and data["kind"] in KINDS:
            plan.kind = data["kind"]
        if "due_at" in data:
            new_due = parse_local(data["due_at"])
            if new_due != plan.due_at:
                plan.due_at = new_due
                plan.notified_at = None  # yangi vaqtda qayta eslatiladi
        if "status" in data and data["status"] in STATUSES:
            plan.status = data["status"]
            plan.done_at = datetime.now(timezone.utc) if data["status"] == "done" else None
        if plan.kind == "reminder" and plan.due_at is None:
            raise ValidationException("Eslatma uchun vaqt kerak", "AI_PLAN_DUE_REQUIRED")
        await self.session.flush()
        return plan

    async def due_for_user(self, user_id, since_minutes: int = 24 * 60) -> list[AiPlan]:
        """Sayt ichidagi eslatma: vaqti kelgan, ochiq rejalar (oxirgi sutka)."""
        now = datetime.now(timezone.utc)
        q = (
            select(AiPlan)
            .where(
                AiPlan.user_id == user_id,
                AiPlan.status == "open",
                AiPlan.kind.in_(("reminder", "task")),
                AiPlan.due_at.is_not(None),
                AiPlan.due_at <= now,
                AiPlan.due_at >= now - timedelta(minutes=since_minutes),
            )
            .order_by(AiPlan.due_at)
            .limit(20)
        )
        return list((await self.session.execute(q)).scalars())


async def run_due_tick(limit: int = 100) -> int:
    """Vaqti kelgan eslatmalar uchun bildirishnoma (push). Har reja — alohida
    sessiyada, o'z filiali doirasida; xato bittasini o'tkazib yuboradi xolos."""
    from app.application.services.notification_service import NotificationService
    from app.core.database import _get_session_factory
    from app.infrastructure.tenant.branch_scope import set_branch_scope

    now = datetime.now(timezone.utc)
    factory = _get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(AiPlan.id)
                .where(
                    AiPlan.status == "open",
                    AiPlan.kind.in_(("reminder", "task")),
                    AiPlan.notified_at.is_(None),
                    and_(AiPlan.due_at.is_not(None), AiPlan.due_at <= now),
                    # Juda eski (masalan, server uzoq to'xtagan) — eslatma yubormaymiz
                    AiPlan.due_at >= now - timedelta(days=2),
                )
                .order_by(AiPlan.due_at)
                .limit(limit)
            )
        ).scalars().all()
    sent = 0
    for plan_id in rows:
        try:
            async with factory() as session:
                plan = await session.get(AiPlan, plan_id)
                if plan is None or plan.notified_at is not None or plan.status != "open":
                    continue
                set_branch_scope(session, plan.hotel_id, plan.branch_id)
                plan.notified_at = datetime.now(timezone.utc)
                await NotificationService(session).notify(
                    plan.hotel_id,
                    plan.user_id,
                    ("Eslatma: " if plan.kind == "reminder" else "Reja vaqti: ") + plan.title,
                    plan.note or "Yordamchi rejasi — vaqti keldi",
                    entity_type="ai_plan",
                    entity_id=plan.id,
                    send_push=True,
                )
                await session.commit()
                sent += 1
        except Exception:
            logger.exception("Reja eslatmasi yuborilmadi (%s)", plan_id)
    return sent


__all__ = ["AiPlanService", "KINDS", "STATUSES", "parse_local", "run_due_tick", "serialize", "to_local_text"]
