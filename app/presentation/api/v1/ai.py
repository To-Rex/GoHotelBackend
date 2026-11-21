"""AI yordamchi ("Yordamchi" sahifasi) API.

Kirish:
* /ai/me — har bir xodim: yordamchi ochiqmi, limiti, huquq belgilari.
* Sozlash (provider, model, kalit, kim ishlatadi) — faqat sozlovchi va
  tizim ma'muri. Kalit hech qachon javobda qaytmaydi.
* Token sarfi va xodim limiti — administrator, tizim ma'muri, sozlovchi.
* Xavfli so'rovlar jurnali — faqat tizim ma'muri va sozlovchi.
* Suhbat, amal, fayl, reja — faqat egasi (va yordamchi unga ochiq bo'lsa).

Xabar yuborish — SSE oqim (text/event-stream). Batafsil:
app/application/services/ai/agent.py
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.ai import agent
from app.application.services.ai.actions_service import AiActionService
from app.application.services.ai.config_service import (
    AiConfigService,
    assert_configure,
    assert_manage_usage,
    can_configure,
    can_manage_usage,
    can_view_incidents,
    month_bounds,
)
from app.application.services.ai.executor import Auth
from app.application.services.ai.plans_service import AiPlanService
from app.application.services.ai.plans_service import serialize as plan_dict
from app.application.services.ai.prompt import title_from
from app.application.services.ai.providers import (
    PROVIDERS,
    AiProviderError,
    make_adapter,
    test_connection,
)
from app.application.services.ai.render import to_local_date
from app.application.services.ai.safety import CATEGORY_LABELS
from app.core.database import get_db
from app.core.exceptions import (
    AppException,
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.infrastructure.database.models.ai import (
    AiConversation,
    AiFile,
    AiIncident,
    AiMessage,
)
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User
from app.presentation.middleware.auth import get_current_user, security

router = APIRouter()


class LimitReached(AppException):
    def __init__(self, detail: str = "Oylik token limiti tugagan", error_code: str = "AI_LIMIT_REACHED") -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 429


def _hotel(current_user: dict) -> UUID:
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise BadRequestException("Mehmonxona tanlanmagan", "HOTEL_REQUIRED")
    return hotel_id


def _auth(credentials: HTTPAuthorizationCredentials | None, current_user: dict) -> Auth:
    if not credentials:
        raise ForbiddenException("Token topilmadi", "AI_TOKEN_REQUIRED")
    return Auth(token=credentials.credentials, device_id=current_user.get("device_id"))


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


# ================================================================ me ==


@router.get("/me")
async def ai_me(session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    hotel_id = current_user.get("hotel_id")
    svc = AiConfigService(session)
    acc = await svc.access(hotel_id, current_user)
    cfg = await svc.get(hotel_id) if hotel_id else None
    out: dict[str, Any] = {
        "enabled": acc.enabled,
        "allowed": acc.allowed,
        "reason": acc.reason,
        "can_configure": can_configure(current_user),
        "can_manage_usage": can_manage_usage(current_user),
        "can_view_incidents": can_view_incidents(current_user),
        "provider": cfg.provider if cfg else None,
        "provider_label": PROVIDERS[cfg.provider].label if cfg and cfg.provider in PROVIDERS else None,
        "model": (cfg.model if cfg else None),
        "limits": None,
    }
    if acc.allowed and hotel_id:
        out["limits"] = await svc.limits_for(hotel_id, current_user["id"], cfg)
    return out


# ========================================================== sozlamalar ==


class SettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(default=None, max_length=500)
    clear_key: Optional[bool] = None
    default_monthly_limit: Optional[int] = None
    hotel_monthly_limit: Optional[int] = None
    price_input: Optional[float] = None
    price_output: Optional[float] = None
    mask_sensitive: Optional[bool] = None


class MemberItem(BaseModel):
    user_id: str
    allowed: Optional[bool] = None
    monthly_limit: Optional[int] = None


class MembersUpdate(BaseModel):
    items: list[MemberItem] = Field(default_factory=list, max_length=1000)


class TestRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(default=None, max_length=500)


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    assert_configure(current_user)
    hotel_id = _hotel(current_user)
    svc = AiConfigService(session)
    cfg = await svc.get(hotel_id)
    return {
        "settings": svc.serialize(cfg),
        "providers": svc.providers_catalog(),
        "members": await svc.members(hotel_id),
    }


@router.put("/settings")
async def update_settings(
    data: SettingsUpdate, session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    hotel_id = _hotel(current_user)
    svc = AiConfigService(session)
    cfg = await svc.save(hotel_id, data.model_dump(exclude_unset=True), current_user)
    return {"settings": svc.serialize(cfg)}


@router.get("/settings/members")
async def get_members(session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    assert_manage_usage(current_user)
    return {"members": await AiConfigService(session).members(_hotel(current_user))}


@router.put("/settings/members")
async def update_members(
    data: MembersUpdate, session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    assert_configure(current_user)
    hotel_id = _hotel(current_user)
    svc = AiConfigService(session)
    changed = await svc.set_members(
        hotel_id, [i.model_dump(exclude_unset=True) for i in data.items], current_user, access=True, limits=True
    )
    return {"changed": changed, "members": await svc.members(hotel_id)}


def _provider_error(exc: AiProviderError) -> dict:
    return {"ok": False, "code": exc.code, "message": exc.user_message, "detail": exc.detail[:300] or None}


@router.post("/settings/test")
async def test_settings(
    data: TestRequest, session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    assert_configure(current_user)
    hotel_id = _hotel(current_user)
    svc = AiConfigService(session)
    cfg = await svc.ensure(hotel_id)
    settings = svc.provider_settings(cfg, data.model_dump(exclude_unset=True))
    await session.commit()
    try:
        return await test_connection(settings)
    except AiProviderError as exc:
        return _provider_error(exc)


@router.post("/settings/models")
async def list_provider_models(
    data: TestRequest, session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    assert_configure(current_user)
    hotel_id = _hotel(current_user)
    svc = AiConfigService(session)
    cfg = await svc.ensure(hotel_id)
    settings = svc.provider_settings(cfg, data.model_dump(exclude_unset=True))
    await session.commit()
    try:
        models = await make_adapter(settings).list_models()
    except AiProviderError as exc:
        return {**_provider_error(exc), "models": []}
    return {"ok": True, "models": models[:300]}


# ================================================================ sarf ==


class LimitsUpdate(BaseModel):
    items: list[MemberItem] = Field(default_factory=list, max_length=1000)


def _parse_month(month: str | None) -> date | None:
    if not month:
        return None
    try:
        y, m = month.split("-", 1)
        return date(int(y), int(m), 1)
    except (ValueError, TypeError) as exc:
        raise BadRequestException("Oy noto'g'ri (YYYY-MM)", "AI_MONTH_INVALID") from exc


@router.get("/usage")
async def usage(
    month: str | None = Query(default=None, description="YYYY-MM (mahalliy oy)"),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    assert_manage_usage(current_user)
    hotel_id = _hotel(current_user)
    day = _parse_month(month)
    svc = AiConfigService(session)
    cfg = await svc.get(hotel_id)
    members = await svc.members(hotel_id)
    by_user = await svc.usage_by_user(hotel_id, day)
    start, end, first = month_bounds(day)
    # Ro'yxatda bo'lmagan, lekin ishlatganlar (sozlovchi, tizim ma'muri)
    listed = {m["user_id"] for m in members}
    extra_ids = [uid for uid in by_user if str(uid) not in listed]
    if extra_ids:
        users = (await session.execute(select(User).where(User.id.in_(extra_ids)))).scalars().all()
        for u in users:
            used = by_user.get(u.id, {})
            members.append({
                "user_id": str(u.id), "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username,
                "username": u.username, "user_type": u.user_type, "status": u.status, "branch_id": None,
                "allowed": True, "monthly_limit": None, "system": True,
                "month_tokens": int(used.get("tokens", 0)), "month_cost": used.get("cost"),
                "month_messages": int(used.get("messages", 0)), "last_used_at": used.get("last_used_at"),
            })
    # Mahalliy oy hisobidagi sarf (agar month berilsa — o'sha oy)
    for m in members:
        used = by_user.get(UUID(m["user_id"]), {}) if m.get("user_id") else {}
        m["month_tokens"] = int(used.get("tokens", 0))
        m["month_cost"] = used.get("cost")
        m["month_messages"] = int(used.get("messages", 0))
        m["last_used_at"] = used.get("last_used_at")
        limit = m.get("monthly_limit")
        if limit is None and not m.get("system"):
            limit = cfg.default_monthly_limit if cfg else None
        m["effective_limit"] = limit
    rows = (
        await session.execute(
            select(AiMessage.created_at, AiMessage.input_tokens, AiMessage.output_tokens, AiMessage.cost_usd)
            .where(
                AiMessage.hotel_id == hotel_id, AiMessage.role == "assistant",
                AiMessage.created_at >= start, AiMessage.created_at < end,
            )
        )
    ).all()
    daily: dict[str, dict] = {}
    total_in = total_out = 0
    total_cost = 0.0
    has_cost = False
    for created, tin, tout, cost in rows:
        d = to_local_date(created)
        key = d.isoformat() if d else "—"
        slot = daily.setdefault(key, {"date": key, "input": 0, "output": 0, "tokens": 0, "messages": 0})
        slot["input"] += int(tin or 0)
        slot["output"] += int(tout or 0)
        slot["tokens"] += int(tin or 0) + int(tout or 0)
        slot["messages"] += 1
        total_in += int(tin or 0)
        total_out += int(tout or 0)
        if cost is not None:
            total_cost += float(cost)
            has_cost = True
    members.sort(key=lambda m: (-m["month_tokens"], m["name"] or ""))
    return {
        "month": f"{first.year:04d}-{first.month:02d}",
        "totals": {
            "input": total_in, "output": total_out, "tokens": total_in + total_out,
            "messages": len(rows), "cost_usd": round(total_cost, 4) if has_cost else None,
            "active_users": sum(1 for m in members if m["month_tokens"] > 0),
        },
        "hotel_monthly_limit": cfg.hotel_monthly_limit if cfg else None,
        "default_monthly_limit": cfg.default_monthly_limit if cfg else None,
        "daily": sorted(daily.values(), key=lambda r: r["date"]),
        "members": members,
    }


@router.put("/usage/limits")
async def update_limits(
    data: LimitsUpdate, session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    assert_manage_usage(current_user)
    hotel_id = _hotel(current_user)
    items = [{"user_id": i.user_id, "monthly_limit": i.monthly_limit} for i in data.items]
    changed = await AiConfigService(session).set_members(hotel_id, items, current_user, access=False, limits=True)
    return {"changed": changed}


# ============================================================ xavfli ==


@router.get("/incidents")
async def incidents(
    reviewed: bool | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not can_view_incidents(current_user):
        raise ForbiddenException("Xavfli so'rovlar jurnalini faqat sozlovchi ko'radi", "AI_INCIDENTS_FORBIDDEN")
    hotel_id = _hotel(current_user)
    q = select(AiIncident, User).join(User, User.id == AiIncident.user_id, isouter=True).where(AiIncident.hotel_id == hotel_id)
    cq = select(func.count(AiIncident.id)).where(AiIncident.hotel_id == hotel_id)
    if reviewed is not None:
        q = q.where(AiIncident.reviewed.is_(reviewed))
        cq = cq.where(AiIncident.reviewed.is_(reviewed))
    if category:
        q = q.where(AiIncident.category == category)
        cq = cq.where(AiIncident.category == category)
    total = int((await session.execute(cq)).scalar() or 0)
    unreviewed = int(
        (await session.execute(
            select(func.count(AiIncident.id)).where(AiIncident.hotel_id == hotel_id, AiIncident.reviewed.is_(False))
        )).scalar() or 0
    )
    rows = (await session.execute(q.order_by(AiIncident.created_at.desc()).limit(limit).offset(offset))).all()
    items = []
    for inc, user in rows:
        items.append({
            "id": str(inc.id),
            "category": inc.category,
            "category_label": CATEGORY_LABELS.get(inc.category, inc.category),
            "source": inc.source,
            "reason": inc.reason,
            "question": inc.question,
            "answer": inc.answer,
            "user_id": str(inc.user_id),
            "user_name": (f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username) if user else None,
            "conversation_id": str(inc.conversation_id) if inc.conversation_id else None,
            "reviewed": inc.reviewed,
            "reviewed_at": _iso(inc.reviewed_at),
            "created_at": _iso(inc.created_at),
        })
    return {"items": items, "total": total, "unreviewed": unreviewed, "categories": CATEGORY_LABELS}


class ReviewRequest(BaseModel):
    reviewed: bool = True


@router.post("/incidents/{incident_id}/review")
async def review_incident(
    data: ReviewRequest,
    incident_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not can_view_incidents(current_user):
        raise ForbiddenException("Xavfli so'rovlar jurnalini faqat sozlovchi ko'radi", "AI_INCIDENTS_FORBIDDEN")
    inc = await session.get(AiIncident, incident_id)
    if inc is None or str(inc.hotel_id) != str(_hotel(current_user)):
        raise NotFoundException("Yozuv topilmadi", "AI_INCIDENT_NOT_FOUND")
    inc.reviewed = data.reviewed
    inc.reviewed_by = current_user["id"] if data.reviewed else None
    inc.reviewed_at = datetime.now(timezone.utc) if data.reviewed else None
    await session.flush()
    return {"id": str(inc.id), "reviewed": inc.reviewed}


# ============================================================ suhbatlar ==


class ConversationCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=160)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class SendMessage(BaseModel):
    text: str = Field(default="", max_length=8000)
    page: Optional[str] = Field(default=None, max_length=200)
    pages: Optional[list[str]] = Field(default=None, max_length=80)
    #: Kartani tasdiqlagach avtomatik "davom et" (matnsiz)
    continue_action: Optional[str] = None


def _conv_dict(c: AiConversation, preview: str | None = None) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
        "preview": preview,
    }


async def _own_conversation(session: AsyncSession, conversation_id: UUID, current_user: dict) -> AiConversation:
    c = await session.get(AiConversation, conversation_id)
    if (
        c is None or c.is_deleted or str(c.user_id) != str(current_user.get("id"))
        or str(c.hotel_id) != str(current_user.get("hotel_id"))
    ):
        raise NotFoundException("Suhbat topilmadi", "AI_CONVERSATION_NOT_FOUND")
    return c


async def _require_access(session: AsyncSession, current_user: dict):
    hotel_id = _hotel(current_user)
    return hotel_id, await AiConfigService(session).assert_access(hotel_id, current_user)


@router.get("/conversations")
async def list_conversations(
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=60, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    hotel_id, _cfg = await _require_access(session, current_user)
    stmt = select(AiConversation).where(
        AiConversation.user_id == current_user["id"],
        AiConversation.hotel_id == hotel_id,
        AiConversation.is_deleted.is_(False),
    )
    if q:
        stmt = stmt.where(AiConversation.title.ilike(f"%{q.strip()}%"))
    rows = (await session.execute(stmt.order_by(AiConversation.updated_at.desc()).limit(limit))).scalars().all()
    return {"items": [_conv_dict(c) for c in rows]}


@router.post("/conversations")
async def create_conversation(
    data: ConversationCreate, session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    hotel_id, _cfg = await _require_access(session, current_user)
    now = datetime.now(timezone.utc)
    c = AiConversation(
        hotel_id=hotel_id, branch_id=current_user.get("branch_id"), user_id=current_user["id"],
        title=(data.title or "").strip() or "Yangi suhbat", is_deleted=False, created_at=now, updated_at=now,
    )
    session.add(c)
    await session.flush()
    return _conv_dict(c)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(session, current_user)
    c = await _own_conversation(session, conversation_id, current_user)
    msgs = (
        await session.execute(
            select(AiMessage).where(AiMessage.conversation_id == c.id).order_by(AiMessage.created_at).limit(400)
        )
    ).scalars().all()
    # Amal kartalarining HOZIRGI holati (tasdiqlangan/qaytarilgan bo'lishi mumkin)
    ids = [
        str((b.get("action") or {}).get("id"))
        for m in msgs for b in (m.blocks or []) if b.get("type") == "action"
    ]
    current = await AiActionService(session).by_ids(ids)
    out = []
    for m in msgs:
        d = agent.serialize_message(m)
        blocks = []
        for b in d["blocks"]:
            if b.get("type") == "action":
                aid = str((b.get("action") or {}).get("id"))
                if aid in current:
                    b = {**b, "action": current[aid]}
            blocks.append(b)
        d["blocks"] = blocks
        out.append(d)
    return {**_conv_dict(c), "messages": out, "running": agent.is_running(c.id)}


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    data: ConversationUpdate,
    conversation_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    c = await _own_conversation(session, conversation_id, current_user)
    c.title = data.title.strip()[:160]
    await session.flush()
    return _conv_dict(c)


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Suhbat xodimning ro'yxatidan olinadi (bazada qoladi: amallar va
    xavfli so'rovlar jurnali buzilmasin)."""
    c = await _own_conversation(session, conversation_id, current_user)
    if agent.is_running(c.id):
        raise ConflictException("Yordamchi hali javob bermoqda", "AI_BUSY")
    c.is_deleted = True
    await session.flush()
    return {"ok": True}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    request: Request,
    data: SendMessage,
    conversation_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    hotel_id, cfg = await _require_access(session, current_user)
    conv = await _own_conversation(session, conversation_id, current_user)
    if agent.is_running(conv.id):
        raise ConflictException("Yordamchi hali javob bermoqda — biroz kuting", "AI_BUSY")
    svc = AiConfigService(session)
    limits = await svc.limits_for(hotel_id, current_user["id"], cfg)
    if limits["blocked"]:
        raise LimitReached("Oylik token limitingiz tugagan — administratorga murojaat qiling")
    auth = _auth(credentials, current_user)

    text = (data.text or "").strip()
    blocks = None
    if data.continue_action:
        try:
            action_id = UUID(str(data.continue_action))
        except ValueError as exc:
            raise BadRequestException("Amal noto'g'ri", "AI_ACTION_INVALID") from exc
        action = await AiActionService(session).get_own(action_id, current_user)
        icon = "✓" if action.status == "executed" else "!"
        state = {"executed": "bajarildi", "failed": "bajarilmadi", "undone": "qaytarildi"}.get(action.status, action.status)
        text = f"{icon} «{action.title}» — {state}"
        blocks = [{"type": "event", "kind": "action_done", "action_id": str(action.id)}]
        typed = ""
    else:
        if not text:
            raise BadRequestException("Xabar bo'sh", "AI_EMPTY_MESSAGE")
        typed = text

    now = datetime.now(timezone.utc)
    user_msg = AiMessage(
        conversation_id=conv.id, hotel_id=hotel_id, user_id=current_user["id"], role="user",
        text=text, blocks=blocks, input_tokens=0, output_tokens=0, cached_tokens=0, created_at=now,
    )
    session.add(user_msg)
    first = (
        await session.execute(
            select(func.count(AiMessage.id)).where(AiMessage.conversation_id == conv.id, AiMessage.role == "user")
        )
    ).scalar()
    if (not first or conv.title in ("", "Yangi suhbat")) and typed:
        conv.title = title_from(typed)
    conv.updated_at = now
    await session.flush()

    user = await session.get(User, current_user["id"])
    hotel = await session.get(Hotel, hotel_id)
    branch = await session.get(Branch, current_user["branch_id"]) if current_user.get("branch_id") else None
    ctx = {
        "hotel_id": hotel_id,
        "branch_id": current_user.get("branch_id"),
        "user_id": current_user["id"],
        "name": (f"{user.first_name or ''} {user.last_name or ''}".strip() if user else None),
        "user_type": current_user.get("user_type"),
        "actual_user_type": current_user.get("actual_user_type"),
        "permissions": current_user.get("permissions") or [],
        "hotel_name": hotel.name if hotel else None,
        "branch_name": branch.name if branch else None,
    }
    inp = agent.TurnInput(
        conversation_id=conv.id,
        user_message_id=user_msg.id,
        text=text if not blocks else _continue_text(text),
        typed_text=typed,
        ctx=ctx,
        auth=auth,
        settings=svc.provider_settings(cfg),
        mask_personal=bool(cfg.mask_sensitive),
        price_input=cfg.price_input,
        price_output=cfg.price_output,
        page=(data.page or None),
        pages=[p for p in (data.pages or []) if isinstance(p, str) and p.startswith("/")][:80] or None,
        remaining=limits.get("remaining"),
    )
    user_payload = agent.serialize_message(user_msg)
    conv_payload = _conv_dict(conv)
    # Foydalanuvchi xabari saqlansin va tranzaksiya yopilsin: navbat o'z
    # sessiyalari bilan ishlaydi, ichki API so'rovlari qulf kutmaydi
    await session.commit()

    emitter = agent.start_turn(inp)

    async def stream():
        yield agent._sse("user", {"message": user_payload, "conversation": conv_payload})
        async for chunk in agent.sse_stream(emitter):
            yield chunk

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _continue_text(note: str) -> str:
    # Model uchun matn tarixdan (event_note) quriladi; bu yerda qisqa izoh
    return f"{note}\n(System note: the card above changed state — see the action result and continue.)"


@router.post("/conversations/{conversation_id}/stop")
async def stop_turn(
    conversation_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    c = await _own_conversation(session, conversation_id, current_user)
    task = agent._RUNNING.get(str(c.id))
    if task is not None and not task.done():
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}


# ============================================================== amallar ==


class ConfirmRequest(BaseModel):
    values: Optional[dict[str, Any]] = None


@router.get("/actions/{action_id}")
async def get_action(
    action_id: UUID = Path(), session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    svc = AiActionService(session)
    return svc.serialize(await svc.get_own(action_id, current_user))


@router.post("/actions/{action_id}/confirm")
async def confirm_action(
    data: ConfirmRequest,
    action_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    await _require_access(session, current_user)
    svc = AiActionService(session)
    a = await svc.get_own(action_id, current_user)
    a = await svc.confirm(a, _auth(credentials, current_user), data.values)
    return svc.serialize(a)


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: UUID = Path(), session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    svc = AiActionService(session)
    a = await svc.reject(await svc.get_own(action_id, current_user))
    return svc.serialize(a)


@router.post("/actions/{action_id}/undo")
async def undo_action(
    action_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    await _require_access(session, current_user)
    svc = AiActionService(session)
    a = await svc.get_own(action_id, current_user)
    a = await svc.undo(a, _auth(credentials, current_user), current_user)
    return svc.serialize(a)


# ============================================================== fayllar ==


@router.get("/files/{file_id}")
async def download_file(
    file_id: UUID = Path(), session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    f = await session.get(AiFile, file_id)
    if f is None or str(f.hotel_id) != str(current_user.get("hotel_id")) or (
        str(f.user_id) != str(current_user.get("id")) and not can_configure(current_user)
    ):
        raise NotFoundException("Fayl topilmadi", "AI_FILE_NOT_FOUND")
    from urllib.parse import quote

    return Response(
        content=f.content,
        media_type=f.content_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"report.xlsx\"; filename*=UTF-8''{quote(f.name)}",
            "Cache-Control": "private, no-store",
        },
    )


# ============================================================== rejalar ==


class PlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = "task"
    due_at: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class PlanUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    kind: Optional[str] = None
    due_at: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = None


@router.get("/plans")
async def list_plans(
    status: str | None = Query(default="recent"),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(session, current_user)
    rows = await AiPlanService(session).list(current_user["id"], status=status, limit=200)
    return {"items": [plan_dict(p) for p in rows]}


@router.get("/plans/due")
async def due_plans(session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Sayt ichidagi eslatma (har daqiqada so'raladi): vaqti kelgan ochiq rejalar."""
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        return {"items": []}
    acc = await AiConfigService(session).access(hotel_id, current_user)
    if not acc.allowed:
        return {"items": []}
    rows = await AiPlanService(session).due_for_user(current_user["id"])
    return {"items": [plan_dict(p) for p in rows]}


@router.post("/plans")
async def create_plan(data: PlanCreate, session: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    hotel_id, _cfg = await _require_access(session, current_user)
    plan = await AiPlanService(session).create(
        hotel_id=hotel_id, branch_id=current_user.get("branch_id"), user_id=current_user["id"],
        title=data.title, kind=data.kind, due_at=data.due_at, note=data.note, created_via="manual",
    )
    return plan_dict(plan)


@router.patch("/plans/{plan_id}")
async def update_plan(
    data: PlanUpdate,
    plan_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    svc = AiPlanService(session)
    plan = await svc.get_own(plan_id, current_user["id"])
    await svc.update(plan, data.model_dump(exclude_unset=True))
    return plan_dict(plan)


__all__ = ["LimitReached", "router"]
