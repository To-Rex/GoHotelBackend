"""Smena va kassa sessiyalari xizmati.

Qoidalar (jahon amaliyoti: Opera PMS / r_keeper uslubida):
  1. Har kassir — o'z sessiyasi: pul kassaning emas, sessiyaning hisobida.
  2. "Ko'r sanash": yopishda xodim avval haqiqiy pulni kiritadi, kutilgan
     summa hisoblanib keyin ko'rsatiladi.
  3. Farq (kamomad ham, ortiqcha ham) har doim sessiya egasiga yoziladi.
  4. Qattiq blok: filialda yopilmagan/topshirilmagan sessiya bor ekan boshqa
     xodim yangi sessiya ocholmaydi — qabul qilib oladi (parol bilan) yoki
     admin/menejer majburiy yopadi.
  5. Rejim va kunlik kesim vaqti mehmonxona sozlamalarida (hotels.settings).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)
from app.infrastructure.auth.password import verify_password
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.shift import ShiftSession
from app.infrastructure.database.models.shop import ShopSale
from app.infrastructure.database.models.user import User

# hotels.settings JSONB ichidagi kalit va standart qiymatlar
SHIFT_SETTINGS_KEY = "shift"
SHIFT_DEFAULTS = {"mode": "simple", "day_close": "00:00"}


def resolve_shift_settings(hotel_settings: dict | None) -> dict:
    """Mehmonxona sozlamalaridan smena rejimini o'qiydi (standartlar bilan)."""
    raw = (hotel_settings or {}).get(SHIFT_SETTINGS_KEY) or {}
    mode = raw.get("mode")
    day_close = raw.get("day_close")
    return {
        "mode": mode if mode in ("simple", "cash") else SHIFT_DEFAULTS["mode"],
        "day_close": day_close if isinstance(day_close, str) and len(day_close) == 5 else SHIFT_DEFAULTS["day_close"],
    }


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


class ShiftService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------ util

    async def _get_hotel(self, hotel_id: UUID) -> Hotel:
        hotel = await self.session.get(Hotel, hotel_id)
        if not hotel:
            raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
        return hotel

    async def get_settings(self, hotel_id: UUID) -> dict:
        hotel = await self._get_hotel(hotel_id)
        return resolve_shift_settings(hotel.settings)

    async def save_settings(self, hotel_id: UUID, mode: str, day_close: str) -> dict:
        hotel = await self._get_hotel(hotel_id)
        # JSONB YANGI dict bilan almashtiriladi — SQLAlchemy o'zgarishni sezishi uchun
        new_settings = dict(hotel.settings or {})
        new_settings[SHIFT_SETTINGS_KEY] = {"mode": mode, "day_close": day_close}
        hotel.settings = new_settings
        await self.session.flush()
        return resolve_shift_settings(new_settings)

    def _serialize(self, s: ShiftSession, user: User | None = None, include_cash: bool = False) -> dict:
        """Sessiyani frontend uchun tayyorlaydi.

        include_cash=False bo'lsa kutilgan summa YASHIRILADI — "ko'r sanash"
        qoidasi: xodim avval haqiqiy pulni kiritishi kerak.
        """
        data = {
            "id": str(s.id),
            "user_id": str(s.user_id),
            "user_name": f"{user.first_name} {user.last_name}" if user else None,
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "opening_cash": float(s.opening_cash or 0),
            "continue_after_end": s.continue_after_end,
            "force_closed": s.force_closed,
            "notes": s.notes,
        }
        if include_cash:
            data["expected_cash"] = float(s.expected_cash) if s.expected_cash is not None else None
            data["counted_cash"] = float(s.counted_cash) if s.counted_cash is not None else None
            data["cash_diff"] = float(s.cash_diff) if s.cash_diff is not None else None
        return data

    async def _open_sessions(self, hotel_id: UUID, branch_id: UUID | None) -> list[tuple[ShiftSession, User]]:
        """Filialdagi (yo'q bo'lsa mehmonxonadagi) yopilmagan sessiyalar."""
        stmt = (
            select(ShiftSession, User)
            .join(User, User.id == ShiftSession.user_id)
            .where(
                ShiftSession.hotel_id == hotel_id,
                ShiftSession.status.in_(["ACTIVE", "PENDING_HANDOVER"]),
            )
        )
        if branch_id:
            stmt = stmt.where(
                (ShiftSession.branch_id == branch_id) | (ShiftSession.branch_id.is_(None))
            )
        result = await self.session.execute(stmt.order_by(ShiftSession.started_at))
        return [(row[0], row[1]) for row in result.all()]

    # ------------------------------------------------------------------ state

    async def get_state(self, hotel_id: UUID, current: dict) -> dict:
        """Joriy foydalanuvchi uchun to'liq smena holati (frontend guard uchun)."""
        settings = await self.get_settings(hotel_id)
        state: dict = {
            **settings,
            "my_session": None,
            "blocking_session": None,
            "accepted_session": None,
        }
        if settings["mode"] != "cash":
            return state

        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        branch_id = current.get("branch_id")
        branch_uuid = UUID(branch_id) if isinstance(branch_id, str) else branch_id

        my_obj: ShiftSession | None = None
        sessions = await self._open_sessions(hotel_id, branch_uuid)
        for s, u in sessions:
            if s.user_id == user_id:
                state["my_session"] = self._serialize(s, u)
                my_obj = s
            elif state["blocking_session"] is None:
                # Boshqa xodimning yopilmagan sessiyasi — qattiq blok manbai
                state["blocking_session"] = self._serialize(s, u)

        # Men QABUL QILIB OLGAN avvalgi smena — joriy sessiyam davomida
        # hisobot sifatida ko'rsatiladi (summalar avvalgi xodim hisobida).
        if my_obj is not None:
            stmt = (
                select(ShiftSession, User)
                .join(User, User.id == ShiftSession.user_id)
                .where(
                    ShiftSession.hotel_id == hotel_id,
                    ShiftSession.accepted_by == user_id,
                    ShiftSession.status == "CLOSED",
                    # Faqat joriy sessiyam ochilishi bilan qabul qilingani
                    ShiftSession.accepted_at >= my_obj.started_at - timedelta(minutes=2),
                )
                .order_by(ShiftSession.accepted_at.desc())
                .limit(1)
            )
            row = (await self.session.execute(stmt)).first()
            if row:
                prev = self._serialize(row[0], row[1], include_cash=True)
                prev["accepted_at"] = (
                    row[0].accepted_at.isoformat() if row[0].accepted_at else None
                )
                state["accepted_session"] = prev
        return state

    # ------------------------------------------------------------------ actions

    async def open_session(
        self, hotel_id: UUID, current: dict, opening_cash: float = 0
    ) -> dict:
        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        branch_id = current.get("branch_id")
        branch_uuid = UUID(branch_id) if isinstance(branch_id, str) else branch_id

        sessions = await self._open_sessions(hotel_id, branch_uuid)
        for s, u in sessions:
            if s.user_id == user_id:
                raise ConflictException("Sizda ochiq smena bor", "SHIFT_ALREADY_OPEN")
            raise ConflictException(
                f"{u.first_name} {u.last_name} smenasi hali yopilmagan — "
                "qabul qilib oling yoki menejerga murojaat qiling",
                "SHIFT_BLOCKED",
            )

        s = ShiftSession(
            hotel_id=hotel_id,
            branch_id=branch_uuid,
            user_id=user_id,
            status="ACTIVE",
            started_at=datetime.now(timezone.utc),
            opening_cash=_dec(opening_cash),
        )
        self.session.add(s)
        await self.session.flush()
        return self._serialize(s)

    async def _my_active(self, hotel_id: UUID, user_id: UUID) -> ShiftSession:
        stmt = select(ShiftSession).where(
            ShiftSession.hotel_id == hotel_id,
            ShiftSession.user_id == user_id,
            ShiftSession.status == "ACTIVE",
        )
        result = await self.session.execute(stmt)
        s = result.scalars().first()
        if not s:
            raise NotFoundException("Ochiq smena topilmadi", "SHIFT_NOT_FOUND")
        return s

    async def continue_shift(self, hotel_id: UUID, current: dict) -> dict:
        """Keyingi xodim kelmadi — ish vaqti tugasa ham davom etish."""
        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        s = await self._my_active(hotel_id, user_id)
        s.continue_after_end = True
        await self.session.flush()
        return self._serialize(s)

    async def compute_expected_cash(self, s: ShiftSession) -> Decimal:
        """Sessiya davomidagi naqd harakat: boshlang'ich + tushum - chiqim.

        Faqat SESSIYA EGASI bajargan amallar hisoblanadi (created_by) — har
        kim o'z pullariga javob beradi.
        """
        start = s.started_at
        end = s.ended_at or datetime.now(timezone.utc)

        # Naqd to'lovlar (bron to'lovlari)
        pay = await self.session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.hotel_id == s.hotel_id,
                Payment.created_by == s.user_id,
                Payment.payment_method == "CASH",
                Payment.created_at >= start,
                Payment.created_at <= end,
            )
        )
        cash_in = _dec(pay.scalar() or 0)

        # Do'kon naqd savdolari (to'lov sessiya oynasida olingan)
        shop = await self.session.execute(
            select(func.coalesce(func.sum(ShopSale.total_amount), 0)).where(
                ShopSale.hotel_id == s.hotel_id,
                ShopSale.created_by == s.user_id,
                ShopSale.status == "PAID",
                ShopSale.payment_method == "CASH",
                ShopSale.paid_at >= start,
                ShopSale.paid_at <= end,
            )
        )
        shop_in = _dec(shop.scalar() or 0)

        # Naqd xarajatlar
        exp = await self.session.execute(
            select(func.coalesce(func.sum(Expense.amount), 0)).where(
                Expense.hotel_id == s.hotel_id,
                Expense.created_by == s.user_id,
                Expense.payment_method == "CASH",
                Expense.created_at >= start,
                Expense.created_at <= end,
            )
        )
        cash_out = _dec(exp.scalar() or 0)

        return _dec(s.opening_cash) + cash_in + shop_in - cash_out

    async def _close_with_count(
        self, s: ShiftSession, counted_cash: float, notes: str | None, closed_by: UUID
    ) -> None:
        s.ended_at = datetime.now(timezone.utc)
        expected = await self.compute_expected_cash(s)
        s.expected_cash = expected
        s.counted_cash = _dec(counted_cash)
        s.cash_diff = _dec(counted_cash) - expected
        s.closed_by = closed_by
        if notes:
            s.notes = notes

    async def close_cash(
        self, hotel_id: UUID, current: dict, counted_cash: float, notes: str | None = None
    ) -> dict:
        """Kassani topshirish: sessiya yopiladi, xodim ishda davom etsa yangi
        sessiya 0 dan ochiladi (kunlik kesimda ham shu ishlatiladi)."""
        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        s = await self._my_active(hotel_id, user_id)
        continue_flag = s.continue_after_end
        await self._close_with_count(s, counted_cash, notes, user_id)
        s.status = "CLOSED"

        # Yangi sessiya — xodim ishlashda davom etadi, kassa 0 dan
        new_s = ShiftSession(
            hotel_id=s.hotel_id,
            branch_id=s.branch_id,
            user_id=user_id,
            status="ACTIVE",
            started_at=datetime.now(timezone.utc),
            opening_cash=Decimal("0"),
            continue_after_end=continue_flag,
        )
        self.session.add(new_s)
        await self.session.flush()
        report = self._serialize(s, include_cash=True)
        report["new_session"] = self._serialize(new_s)
        return report

    async def end_shift(
        self, hotel_id: UUID, current: dict, counted_cash: float, notes: str | None = None
    ) -> dict:
        """Smenani tugallash: kassa yopiladi, sessiya keyingi xodim qabul
        qilishini kutadi (PENDING_HANDOVER)."""
        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        s = await self._my_active(hotel_id, user_id)
        await self._close_with_count(s, counted_cash, notes, user_id)
        s.status = "PENDING_HANDOVER"
        await self.session.flush()
        return self._serialize(s, include_cash=True)

    async def accept_shift(self, hotel_id: UUID, current: dict, password: str) -> dict:
        """Keyingi xodim topshirilgan smenani O'Z PAROLI bilan qabul qiladi
        (to'rt ko'z tamoyili) va 0 so'mlik yangi sessiya ochiladi."""
        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        user = await self.session.get(User, user_id)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedException("Parol noto'g'ri", "INVALID_PASSWORD")

        branch_id = current.get("branch_id")
        branch_uuid = UUID(branch_id) if isinstance(branch_id, str) else branch_id
        sessions = await self._open_sessions(hotel_id, branch_uuid)

        pending = next(
            (s for s, _ in sessions if s.status == "PENDING_HANDOVER" and s.user_id != user_id),
            None,
        )
        if not pending:
            raise NotFoundException("Qabul qilinadigan smena topilmadi", "NO_PENDING_SHIFT")

        pending.status = "CLOSED"
        pending.accepted_by = user_id
        pending.accepted_at = datetime.now(timezone.utc)

        # Qabul qiluvchining yangi sessiyasi — 0 dan boshlanadi
        new_s = ShiftSession(
            hotel_id=hotel_id,
            branch_id=branch_uuid,
            user_id=user_id,
            status="ACTIVE",
            started_at=datetime.now(timezone.utc),
            opening_cash=Decimal("0"),
        )
        self.session.add(new_s)
        await self.session.flush()
        return self._serialize(new_s)

    async def force_close(
        self,
        hotel_id: UUID,
        current: dict,
        session_id: UUID,
        counted_cash: float | None = None,
        notes: str | None = None,
    ) -> dict:
        """Admin/menejer majburiy yopishi. counted_cash berilmasa kutilgan
        summa bo'yicha yopiladi (farq 0). Farq bo'lsa sessiya egasiga yoziladi."""
        is_admin = current["user_type"] in ("ADMIN", "SUPER_ADMIN")
        has_perm = "shift.force_close" in (current.get("permissions") or [])
        if not (is_admin or has_perm):
            raise ForbiddenException("Majburiy yopish huquqi yo'q", "FORBIDDEN")

        s = await self.session.get(ShiftSession, session_id)
        if not s or s.hotel_id != hotel_id or s.status == "CLOSED":
            raise NotFoundException("Sessiya topilmadi", "SHIFT_NOT_FOUND")

        closer_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        s.ended_at = s.ended_at or datetime.now(timezone.utc)
        if s.expected_cash is None:
            s.expected_cash = await self.compute_expected_cash(s)
        if counted_cash is not None:
            s.counted_cash = _dec(counted_cash)
        elif s.counted_cash is None:
            # Sanamasdan yopish — kutilgan bo'yicha, farq 0
            s.counted_cash = s.expected_cash
        s.cash_diff = _dec(s.counted_cash) - _dec(s.expected_cash)
        s.status = "CLOSED"
        s.force_closed = True
        s.closed_by = closer_id
        note = f"Majburiy yopildi ({current.get('user_type', '')})"
        s.notes = f"{s.notes}\n{note}" if s.notes else note
        if notes:
            s.notes += f": {notes}"
        await self.session.flush()
        return self._serialize(s, include_cash=True)

    async def get_history(
        self, hotel_id: UUID, current: dict, limit: int = 50
    ) -> list[dict]:
        """Smenalar tarixi: admin/menejer hammasini, xodim faqat o'zinikini ko'radi."""
        is_admin = current["user_type"] in ("ADMIN", "SUPER_ADMIN")
        has_perm = "shift.force_close" in (current.get("permissions") or [])
        stmt = (
            select(ShiftSession, User)
            .join(User, User.id == ShiftSession.user_id)
            .where(ShiftSession.hotel_id == hotel_id)
        )
        if not (is_admin or has_perm):
            user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
            stmt = stmt.where(ShiftSession.user_id == user_id)
        result = await self.session.execute(
            stmt.order_by(ShiftSession.started_at.desc()).limit(limit)
        )
        out = []
        for s, u in result.all():
            # Tarixda kassa raqamlari ochiq — sessiya allaqachon yopilgan
            out.append(self._serialize(s, u, include_cash=s.status == "CLOSED"))
        return out
