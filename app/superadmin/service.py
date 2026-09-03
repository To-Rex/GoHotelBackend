"""Panelga kirish va panel foydalanuvchilarini boshqarish."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.superadmin import security
from app.superadmin.models import PanelUser


def _clean_password(password: str) -> str:
    text = password or ""
    if len(text) < security.MIN_PASSWORD_LENGTH:
        raise ValidationException(
            f"Parol kamida {security.MIN_PASSWORD_LENGTH} belgidan iborat bo'lsin",
            "PASSWORD_TOO_SHORT",
        )
    return text


class PanelAuthService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --------------------------------------------------------- kirish --

    async def _root_row(self) -> PanelUser | None:
        """Ildiz hisobning bazadagi yozuvi (paroli o'zgartirilgan bo'lsa)."""
        return (
            await self.session.execute(
                select(PanelUser).where(PanelUser.is_root.is_(True))
            )
        ).scalars().first()

    async def _by_email(self, email: str) -> PanelUser | None:
        return (
            await self.session.execute(
                select(PanelUser).where(
                    PanelUser.email == security.normalize_email(email)
                )
            )
        ).scalars().first()

    async def login(self, email: str, password: str) -> dict:
        """Panelga kirish.

        Ildiz hisob avval tekshiriladi: uning paroli bazada bo'lishi ham,
        bo'lmasligi ham mumkin. Bazada yo'q bo'lsa kodagi hash
        ishlatiladi — bo'sh bazali tizimga ham egasi kira olishi kerak.
        """
        if security.is_root_email(email):
            row = await self._root_row()
            expected = row.password_hash if row else security.ROOT_PASSWORD_HASH
            if not security.verify_password(password, expected):
                raise UnauthorizedException("Login yoki parol noto'g'ri")
            if row is None:
                # Birinchi muvaffaqiyatli kirish — yozuv shu paytda
                # ochiladi, keyingi parol o'zgarishi shu yerga tushadi
                row = PanelUser(
                    email=None,
                    email_sha256=security.ROOT_EMAIL_SHA256,
                    password_hash=security.ROOT_PASSWORD_HASH,
                    label=security.ROOT_LABEL,
                    is_root=True,
                    is_active=True,
                )
                self.session.add(row)
                await self.session.flush()
            row.last_login_at = datetime.now(timezone.utc)
            await self.session.flush()
            return self._session_for(row, email)

        row = await self._by_email(email)
        if row is None or not row.is_active:
            raise UnauthorizedException("Login yoki parol noto'g'ri")
        if not security.verify_password(password, row.password_hash):
            raise UnauthorizedException("Login yoki parol noto'g'ri")
        row.last_login_at = datetime.now(timezone.utc)
        await self.session.flush()
        return self._session_for(row, row.email or email)

    @staticmethod
    def _session_for(row: PanelUser, email: str) -> dict:
        return {
            "access_token": security.create_token(
                str(row.id), email, bool(row.is_root)
            ),
            "token_type": "bearer",
            "user": {
                "id": str(row.id),
                "email": email,
                "label": row.label or security.ROOT_LABEL,
                "is_root": bool(row.is_root),
            },
        }

    async def current(self, user_id: UUID) -> PanelUser:
        row = await self.session.get(PanelUser, user_id)
        if row is None or not row.is_active:
            raise UnauthorizedException("Sessiya tugadi")
        return row

    # ------------------------------------------ panel foydalanuvchilari --

    async def list_users(self) -> list[dict]:
        rows = (
            await self.session.execute(
                select(PanelUser).order_by(
                    PanelUser.is_root.desc(), PanelUser.created_at
                )
            )
        ).scalars().all()
        return [self._as_dict(row) for row in rows]

    async def create_user(
        self, actor: PanelUser, email: str, password: str, label: str
    ) -> dict:
        """Panelga yangi odam qo'shish — faqat ildiz hisob qila oladi."""
        self._require_root(actor)
        clean = security.normalize_email(email)
        if "@" not in clean or len(clean) < 5:
            raise ValidationException("Pochta manzili noto'g'ri", "INVALID_EMAIL")
        if security.is_root_email(clean):
            # Egasining manzili bilan ikkinchi hisob ochilmaydi
            raise ConflictException(
                "Bu manzil allaqachon band", "EMAIL_TAKEN"
            )
        if await self._by_email(clean) is not None:
            raise ConflictException("Bu manzil allaqachon band", "EMAIL_TAKEN")

        row = PanelUser(
            email=clean,
            email_sha256=security.email_fingerprint(clean),
            password_hash=security.hash_password(_clean_password(password)),
            label=(label or "").strip() or clean,
            is_root=False,
            is_active=True,
            created_by=actor.id,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return self._as_dict(row)

    async def set_active(
        self, actor: PanelUser, user_id: UUID, active: bool
    ) -> dict:
        self._require_root(actor)
        row = await self._get(user_id)
        if row.is_root:
            raise ForbiddenException(
                "Tizim egasining hisobini o'chirib bo'lmaydi", "ROOT_PROTECTED"
            )
        row.is_active = active
        await self.session.flush()
        return self._as_dict(row)

    async def delete_user(self, actor: PanelUser, user_id: UUID) -> None:
        self._require_root(actor)
        row = await self._get(user_id)
        if row.is_root:
            raise ForbiddenException(
                "Tizim egasining hisobini o'chirib bo'lmaydi", "ROOT_PROTECTED"
            )
        await self.session.delete(row)
        await self.session.flush()

    async def reset_password(
        self, actor: PanelUser, user_id: UUID, password: str
    ) -> dict:
        """Boshqa foydalanuvchining parolini almashtirish."""
        self._require_root(actor)
        row = await self._get(user_id)
        if row.is_root and row.id != actor.id:
            raise ForbiddenException("Ruxsat yo'q", "ROOT_PROTECTED")
        row.password_hash = security.hash_password(_clean_password(password))
        await self.session.flush()
        return self._as_dict(row)

    async def change_own_password(
        self, actor: PanelUser, current_password: str, new_password: str
    ) -> dict:
        """O'z parolini almashtirish — eskisini bilish shart."""
        if not security.verify_password(current_password, actor.password_hash):
            raise UnauthorizedException("Joriy parol noto'g'ri")
        new_hash = security.hash_password(_clean_password(new_password))
        if security.verify_password(new_password, actor.password_hash):
            raise ValidationException(
                "Yangi parol eskisidan farq qilishi kerak", "SAME_PASSWORD"
            )
        actor.password_hash = new_hash
        await self.session.flush()
        return {"changed": True}

    # ------------------------------------------------------ yordamchi --

    async def _get(self, user_id: UUID) -> PanelUser:
        row = await self.session.get(PanelUser, user_id)
        if row is None:
            raise NotFoundException("Foydalanuvchi topilmadi", "PANEL_USER_NOT_FOUND")
        return row

    @staticmethod
    def _require_root(actor: PanelUser) -> None:
        if not actor.is_root:
            raise ForbiddenException(
                "Bu amalni faqat tizim egasi bajaradi", "ROOT_ONLY"
            )

    @staticmethod
    def _as_dict(row: PanelUser) -> dict:
        return {
            "id": str(row.id),
            # Ildiz hisobning manzili ochiq matnda saqlanmaydi
            "email": row.email,
            "label": row.label or security.ROOT_LABEL,
            "is_root": bool(row.is_root),
            "is_active": bool(row.is_active),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_login_at": (
                row.last_login_at.isoformat() if row.last_login_at else None
            ),
        }
