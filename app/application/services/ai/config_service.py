"""AI sozlamasi, kim ishlata olishi, limitlar va token sarfi.

Huquqlar:

* SOZLASH (provider, model, kalit, kim ishlatadi) — faqat sozlovchi va
  tizim ma'muri (`can_configure`).
* TOKEN SARFINI KO'RISH va XODIMGA LIMIT qo'yish — administrator, tizim
  ma'muri va sozlovchi (`can_manage_usage`).
* XAVFLI SO'ROVLAR jurnali — faqat tizim ma'muri va sozlovchi.
* ISHLATISH — sozlovchi ro'yxatga qo'shgan xodimlar (administrator ham
  ro'yxatda bo'lmasa — yo'q). Sozlovchi va tizim ma'murining o'zi —
  sozlash va sinash uchun — doim ishlata oladi.

Limitlar oy bo'yicha (mahalliy oy): xodimning shaxsiy limiti, bo'lmasa
mehmonxonaning standart limiti; ustiga mehmonxona bo'yicha jami limit.
Token — kiruvchi + chiquvchi.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.ai import crypto
from app.application.services.ai.providers import PROVIDERS, ProviderSettings
from app.application.services.configurator_access import (
    CONFIGURATOR_TYPE,
    SUPER_ADMIN_TYPE,
    actual_user_type,
)
from app.core.exceptions import ForbiddenException, ValidationException
from app.infrastructure.database.models.ai import AiConfig, AiMember, AiMessage
from app.infrastructure.database.models.user import User
from app.shared.utils import hotel_today, local_day_bounds

SYSTEM_TYPES = (CONFIGURATOR_TYPE, SUPER_ADMIN_TYPE)
MAX_LIMIT = 2_000_000_000


def can_configure(current_user: dict | None) -> bool:
    return actual_user_type(current_user) in SYSTEM_TYPES


def can_manage_usage(current_user: dict | None) -> bool:
    """Administrator (sozlovchi ham mehmonxonada ADMIN) va tizim ma'muri."""
    return bool(current_user) and current_user.get("user_type") in ("ADMIN", "SUPER_ADMIN")


def can_view_incidents(current_user: dict | None) -> bool:
    return actual_user_type(current_user) in SYSTEM_TYPES


def assert_configure(current_user: dict) -> None:
    if not can_configure(current_user):
        raise ForbiddenException("AI sozlamasini faqat sozlovchi o'zgartira oladi", "AI_CONFIG_FORBIDDEN")


def assert_manage_usage(current_user: dict) -> None:
    if not can_manage_usage(current_user):
        raise ForbiddenException(
            "Token sarfini faqat administrator yoki sozlovchi ko'radi", "AI_USAGE_FORBIDDEN"
        )


def month_bounds(day: date | None = None) -> tuple[datetime, datetime, date]:
    """Mahalliy oyning UTC chegaralari [start, end) va oyning birinchi kuni."""
    today = day or hotel_today()
    first = today.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    start, end = local_day_bounds(first, nxt - timedelta(days=1))
    return start, end, first


@dataclass
class Access:
    enabled: bool
    allowed: bool
    #: not_configured | disabled | not_member | ok
    reason: str


def _clean_limit(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationException("Limit butun son bo'lishi kerak", "AI_LIMIT_INVALID") from exc
    if n < 0 or n > MAX_LIMIT:
        raise ValidationException("Limit noto'g'ri", "AI_LIMIT_INVALID")
    return n


def _clean_price(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationException("Narx son bo'lishi kerak", "AI_PRICE_INVALID") from exc
    if n < 0 or n > 10_000:
        raise ValidationException("Narx noto'g'ri", "AI_PRICE_INVALID")
    return n


def _clean_base_url(value: str | None) -> str | None:
    text = (value or "").strip().rstrip("/")
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValidationException("Manzil https:// bilan boshlanishi kerak", "AI_BASE_URL_INVALID")
    return text[:300]


class AiConfigService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- sozlama --

    async def get(self, hotel_id: UUID) -> AiConfig | None:
        return await self.session.get(AiConfig, hotel_id)

    async def ensure(self, hotel_id: UUID) -> AiConfig:
        cfg = await self.get(hotel_id)
        if cfg is None:
            cfg = AiConfig(hotel_id=hotel_id, enabled=False, provider="openai", mask_sensitive=True)
            self.session.add(cfg)
            await self.session.flush()
        return cfg

    @staticmethod
    def serialize(cfg: AiConfig | None) -> dict:
        if cfg is None:
            return {
                "enabled": False, "provider": "openai", "model": PROVIDERS["openai"].default_model,
                "base_url": None, "has_key": False, "key_hint": None, "default_monthly_limit": None,
                "hotel_monthly_limit": None, "price_input": None, "price_output": None,
                "mask_sensitive": True, "updated_at": None,
            }
        return {
            "enabled": bool(cfg.enabled),
            "provider": cfg.provider,
            "model": cfg.model or (PROVIDERS.get(cfg.provider).default_model if PROVIDERS.get(cfg.provider) else None),
            "base_url": cfg.base_url,
            "has_key": bool(cfg.api_key_enc),
            "key_hint": cfg.api_key_hint,
            "default_monthly_limit": cfg.default_monthly_limit,
            "hotel_monthly_limit": cfg.hotel_monthly_limit,
            "price_input": cfg.price_input,
            "price_output": cfg.price_output,
            "mask_sensitive": bool(cfg.mask_sensitive),
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        }

    @staticmethod
    def providers_catalog() -> list[dict]:
        return [
            {
                "key": p.key, "label": p.label, "base_url": p.base_url,
                "default_model": p.default_model, "models": list(p.models),
                "needs_base_url": p.needs_base_url,
            }
            for p in PROVIDERS.values()
        ]

    async def save(self, hotel_id: UUID, data: dict, current_user: dict) -> AiConfig:
        assert_configure(current_user)
        cfg = await self.ensure(hotel_id)
        if "provider" in data:
            provider = (data.get("provider") or "").strip()
            if provider not in PROVIDERS:
                raise ValidationException("Noma'lum provider", "AI_PROVIDER_INVALID")
            if provider != cfg.provider:
                cfg.provider = provider
                # Boshqa provayder — eski model va manzil mos emas
                cfg.model = PROVIDERS[provider].default_model
                cfg.base_url = None
        if "model" in data:
            model = (data.get("model") or "").strip()
            cfg.model = model[:120] or PROVIDERS[cfg.provider].default_model
        if "base_url" in data:
            cfg.base_url = _clean_base_url(data.get("base_url"))
        if data.get("clear_key"):
            cfg.api_key_enc = None
            cfg.api_key_hint = None
        key = (data.get("api_key") or "").strip()
        if key:
            if len(key) > 500:
                raise ValidationException("Kalit juda uzun", "AI_KEY_INVALID")
            cfg.api_key_enc = crypto.encrypt(key)
            cfg.api_key_hint = crypto.hint(key)
        for name in ("default_monthly_limit", "hotel_monthly_limit"):
            if name in data:
                setattr(cfg, name, _clean_limit(data.get(name)))
        for name in ("price_input", "price_output"):
            if name in data:
                setattr(cfg, name, _clean_price(data.get(name)))
        if "mask_sensitive" in data:
            cfg.mask_sensitive = bool(data.get("mask_sensitive"))
        if "enabled" in data:
            enabled = bool(data.get("enabled"))
            if enabled and not cfg.api_key_enc:
                raise ValidationException("Avval API kalitini kiriting", "AI_KEY_REQUIRED")
            if enabled and PROVIDERS[cfg.provider].needs_base_url and not cfg.base_url:
                raise ValidationException("Bu provider uchun manzil kerak", "AI_BASE_URL_REQUIRED")
            cfg.enabled = enabled
        cfg.updated_by = current_user.get("id")
        await self.session.flush()
        return cfg

    def provider_settings(self, cfg: AiConfig, override: dict | None = None) -> ProviderSettings:
        """Provayderga ulanish ma'lumotlari (kalit ochiladi — faqat server ichida)."""
        o = override or {}
        provider = o.get("provider") or cfg.provider
        info = PROVIDERS.get(provider) or PROVIDERS["openai"]
        key = (o.get("api_key") or "").strip() or crypto.decrypt(cfg.api_key_enc) or ""
        model = (o.get("model") or "").strip() or (cfg.model if provider == cfg.provider else None) or info.default_model or ""
        base_url = _clean_base_url(o.get("base_url")) if o.get("base_url") else (
            cfg.base_url if provider == cfg.provider else None
        )
        return ProviderSettings(provider, model, key, base_url or info.base_url)

    # ------------------------------------------------------- a'zolar --

    async def _member(self, hotel_id: UUID, user_id) -> AiMember | None:
        return (
            await self.session.execute(
                select(AiMember).where(AiMember.hotel_id == hotel_id, AiMember.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def members(self, hotel_id: UUID) -> list[dict]:
        """Mehmonxona xodimlari: ruxsat, limit va shu oydagi sarf."""
        users = (
            await self.session.execute(
                select(User)
                .where(
                    User.hotel_id == hotel_id,
                    User.is_deleted.is_(False),
                    User.user_type.notin_(SYSTEM_TYPES),
                )
                .order_by(User.first_name, User.last_name)
            )
        ).scalars().all()
        rows = {
            m.user_id: m
            for m in (
                await self.session.execute(select(AiMember).where(AiMember.hotel_id == hotel_id))
            ).scalars()
        }
        usage = await self.usage_by_user(hotel_id)
        out = []
        for u in users:
            m = rows.get(u.id)
            used = usage.get(u.id, {})
            out.append(
                {
                    "user_id": str(u.id),
                    "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username,
                    "username": u.username,
                    "user_type": u.user_type,
                    "status": u.status,
                    "branch_id": str(u.branch_id) if u.branch_id else None,
                    "allowed": bool(m.allowed) if m else False,
                    "monthly_limit": m.monthly_limit if m else None,
                    "month_tokens": int(used.get("tokens", 0)),
                    "month_cost": used.get("cost"),
                    "month_messages": int(used.get("messages", 0)),
                    "last_used_at": used.get("last_used_at"),
                }
            )
        return out

    async def set_members(
        self, hotel_id: UUID, items: list[dict], current_user: dict, *, access: bool, limits: bool
    ) -> int:
        """`access` — ruxsatni o'zgartirish (sozlovchi); `limits` — limitni
        (administrator ham). Mehmonxonaning o'z xodimlari bilan cheklanadi."""
        if access:
            assert_configure(current_user)
        if limits:
            assert_manage_usage(current_user)
        ids = []
        for item in items:
            try:
                ids.append(UUID(str(item.get("user_id"))))
            except (TypeError, ValueError) as exc:
                raise ValidationException("Xodim noto'g'ri", "AI_MEMBER_INVALID") from exc
        valid = set(
            (
                await self.session.execute(
                    select(User.id).where(
                        User.id.in_(ids), User.hotel_id == hotel_id, User.user_type.notin_(SYSTEM_TYPES)
                    )
                )
            ).scalars()
        ) if ids else set()
        changed = 0
        for item, user_id in zip(items, ids, strict=True):
            if user_id not in valid:
                continue
            m = await self._member(hotel_id, user_id)
            if m is None:
                m = AiMember(hotel_id=hotel_id, user_id=user_id, allowed=False)
                self.session.add(m)
            if access and "allowed" in item:
                m.allowed = bool(item.get("allowed"))
            if limits and "monthly_limit" in item:
                m.monthly_limit = _clean_limit(item.get("monthly_limit"))
            m.updated_by = current_user.get("id")
            changed += 1
        await self.session.flush()
        return changed

    # ----------------------------------------------------------- kirish --

    async def access(self, hotel_id: UUID | None, current_user: dict) -> Access:
        if hotel_id is None:
            return Access(False, False, "not_configured")
        cfg = await self.get(hotel_id)
        if cfg is None or not cfg.api_key_enc:
            return Access(False, False, "not_configured")
        if not cfg.enabled:
            return Access(False, False, "disabled")
        if can_configure(current_user):
            return Access(True, True, "ok")
        m = await self._member(hotel_id, current_user.get("id"))
        if m is None or not m.allowed:
            return Access(True, False, "not_member")
        return Access(True, True, "ok")

    async def assert_access(self, hotel_id: UUID | None, current_user: dict) -> AiConfig:
        acc = await self.access(hotel_id, current_user)
        if not acc.allowed:
            raise ForbiddenException(
                "Yordamchi sizga ochilmagan — sozlovchiga murojaat qiling", "AI_ACCESS_DENIED"
            )
        return await self.get(hotel_id)

    # ------------------------------------------------------------ sarf --

    async def usage_by_user(self, hotel_id: UUID, day: date | None = None) -> dict:
        start, end, _first = month_bounds(day)
        rows = (
            await self.session.execute(
                select(
                    AiMessage.user_id,
                    func.coalesce(func.sum(AiMessage.input_tokens + AiMessage.output_tokens), 0),
                    func.sum(AiMessage.cost_usd),
                    func.count(AiMessage.id),
                    func.max(AiMessage.created_at),
                )
                .where(
                    AiMessage.hotel_id == hotel_id,
                    AiMessage.role == "assistant",
                    AiMessage.created_at >= start,
                    AiMessage.created_at < end,
                )
                .group_by(AiMessage.user_id)
            )
        ).all()
        return {
            uid: {
                "tokens": int(tokens or 0),
                "cost": round(float(cost), 4) if cost is not None else None,
                "messages": int(count or 0),
                "last_used_at": last.isoformat() if last else None,
            }
            for uid, tokens, cost, count, last in rows
        }

    async def month_tokens(self, hotel_id: UUID, user_id=None) -> int:
        start, end, _ = month_bounds()
        q = select(func.coalesce(func.sum(AiMessage.input_tokens + AiMessage.output_tokens), 0)).where(
            AiMessage.hotel_id == hotel_id,
            AiMessage.role == "assistant",
            AiMessage.created_at >= start,
            AiMessage.created_at < end,
        )
        if user_id is not None:
            q = q.where(AiMessage.user_id == user_id)
        return int((await self.session.execute(q)).scalar() or 0)

    async def limits_for(self, hotel_id: UUID, user_id, cfg: AiConfig | None = None) -> dict:
        cfg = cfg or await self.get(hotel_id)
        m = await self._member(hotel_id, user_id)
        user_limit = m.monthly_limit if (m and m.monthly_limit is not None) else (
            cfg.default_monthly_limit if cfg else None
        )
        used = await self.month_tokens(hotel_id, user_id)
        hotel_limit = cfg.hotel_monthly_limit if cfg else None
        hotel_used = await self.month_tokens(hotel_id) if hotel_limit is not None else None
        remaining = None
        if user_limit is not None:
            remaining = max(user_limit - used, 0)
        if hotel_limit is not None:
            hotel_left = max(hotel_limit - (hotel_used or 0), 0)
            remaining = hotel_left if remaining is None else min(remaining, hotel_left)
        return {
            "month_tokens": used,
            "monthly_limit": user_limit,
            "hotel_monthly_limit": hotel_limit,
            "hotel_month_tokens": hotel_used,
            "remaining": remaining,
            "blocked": remaining is not None and remaining <= 0,
        }

    @staticmethod
    def cost(cfg: AiConfig | None, input_tokens: int, output_tokens: int) -> float | None:
        if cfg is None or (cfg.price_input is None and cfg.price_output is None):
            return None
        return round(
            input_tokens / 1_000_000 * float(cfg.price_input or 0)
            + output_tokens / 1_000_000 * float(cfg.price_output or 0),
            6,
        )


__all__ = [
    "Access", "AiConfigService", "assert_configure", "assert_manage_usage", "can_configure",
    "can_manage_usage", "can_view_incidents", "month_bounds",
]
