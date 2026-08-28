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


def pick_open_session(sessions, *, active_only: bool = False):
    """Xodimning "joriy" sessiyasini tanlaydi: faol ustun, teng holatda eng yangi.

    Bu qoida ATAYLAB bitta joyda turadi. Ilgari u ikki marta — kassa holatini
    ko'rsatishda va topshirish summasini hisoblashda — alohida yozilgan edi va
    ikkalasi boshqa-boshqa sessiyani tanlashi mumkin edi: ekranda boshlang'ich
    kassa bir sessiyadan, topshiriladigan summa esa boshqasidan olinib,
    xodimning tushumi yo'qolgandek ko'rinardi.

    `active_only` — faqat ishlayotgan sessiya kerak bo'lgan joylar uchun
    (topshirilgan smenani qayta yopib bo'lmaydi).
    """
    active = [s for s in sessions if s.status == "ACTIVE"]
    pool = active if (active or active_only) else [
        s for s in sessions if s.status == "PENDING_HANDOVER"
    ]
    return max(pool, key=lambda s: s.started_at) if pool else None


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
            # Tuzatishlar tarixi — tahrirlangani va avvalgi qiymatlar ko'rinadi
            data["corrections"] = s.corrections or []
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

        sessions = await self._open_sessions(hotel_id, branch_uuid)
        users = {s.id: u for s, u in sessions}
        # Kassa holatini ko'rsatishda va topshirish summasini hisoblashda
        # AYNAN bir sessiya tanlanishi shart — shuning uchun qoida umumiy.
        my_obj = pick_open_session([s for s, _ in sessions if s.user_id == user_id])
        for s, u in sessions:
            if s.user_id == user_id:
                continue
            if state["blocking_session"] is None:
                # Boshqa xodimning yopilmagan sessiyasi — qattiq blok manbai
                blocked = self._serialize(s, u)
                # Topshirilayotgan smenada sanab topshirilgan summa qabul
                # qiluvchiga ko'rsatiladi — u qancha pul olayotganini bilishi
                # va sanab tekshirishi kerak (bu uning boshlang'ich kassasi)
                if s.status == "PENDING_HANDOVER" and s.counted_cash is not None:
                    blocked["counted_cash"] = float(s.counted_cash)
                state["blocking_session"] = blocked

        if my_obj is not None:
            state["my_session"] = self._serialize(my_obj, users.get(my_obj.id))

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
        """Xodimning faol sessiyasi — HAR DOIM eng oxirgisi.

        Tartib berilmasa baza istalgan qatorni qaytarishi mumkin edi. Xodimda
        (nosozlik tufayli) ikkita faol sessiya paydo bo'lsa, kassa ekrani bir
        sessiyani, topshirish summasi esa boshqasini ko'rsatardi: boshlang'ich
        to'g'ri, topshiriladigan summa esa bir sessiya orqadagi qiymat bo'lib
        qolardi. Eng yangisini tanlash ikkala hisobni bitta sessiyaga bog'laydi.
        """
        stmt = (
            select(ShiftSession)
            .where(
                ShiftSession.hotel_id == hotel_id,
                ShiftSession.user_id == user_id,
                ShiftSession.status == "ACTIVE",
            )
            .order_by(ShiftSession.started_at.desc())
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

    async def cash_breakdown(self, s: ShiftSession) -> dict:
        """Sessiya kassasi tarkibi: boshlang'ich + naqd tushumlar - naqd chiqimlar.

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

        # Do'kon naqd savdolari (to'lov sessiya oynasida olingan).
        # Bo'lib to'lashda (payments ro'yxati bor, method "MIXED") kassaga
        # faqat NAQD bo'laklar tushadi — jami emas
        shop_rows = (
            await self.session.execute(
                select(
                    ShopSale.total_amount,
                    ShopSale.payment_method,
                    ShopSale.payments,
                ).where(
                    ShopSale.hotel_id == s.hotel_id,
                    ShopSale.created_by == s.user_id,
                    ShopSale.status == "PAID",
                    ShopSale.paid_at >= start,
                    ShopSale.paid_at <= end,
                )
            )
        ).all()
        shop_in = Decimal("0")
        for sale_total, sale_method, sale_parts in shop_rows:
            if sale_parts:
                for part in sale_parts:
                    if part.get("payment_method") == "CASH":
                        shop_in += _dec(part.get("amount") or 0)
            elif sale_method == "CASH":
                shop_in += _dec(sale_total)

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

        opening = _dec(s.opening_cash)
        return {
            "opening_cash": opening,
            "payments_cash": cash_in,
            "shop_cash": shop_in,
            "expenses_cash": cash_out,
            "expected_cash": opening + cash_in + shop_in - cash_out,
        }

    async def compute_expected_cash(self, s: ShiftSession) -> Decimal:
        return (await self.cash_breakdown(s))["expected_cash"]

    async def my_expected_cash(self, hotel_id: UUID, current: dict) -> dict:
        """Joriy xodimning FAOL sessiyasi uchun kassada bo'lishi kerak bo'lgan
        summa (tarkibi bilan) — topshirish dialogida ko'rsatiladi."""
        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        s = await self._my_active(hotel_id, user_id)
        return {k: float(v) for k, v in (await self.cash_breakdown(s)).items()}

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
        # Yopilish YANGI sessiyadan OLDIN yozilishi shart: "bir xodim — bitta
        # faol sessiya" cheklovi bazada indeks bilan qo'riqlanadi, SQLAlchemy
        # esa odatda qo'shishni yangilashdan oldin yuboradi.
        await self.session.flush()

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
        (to'rt ko'z tamoyili).

        Kassadagi pul JISMONAN qabul qiluvchiga o'tadi — shuning uchun avvalgi
        xodim sanab topshirgan summa (counted_cash) yangi sessiyaning
        BOSHLANG'ICH kassasi bo'ladi. Shunda keyingi topshirishda kutilgan
        summa = qabul qilingan pul + o'z tushumlari bo'lib to'g'ri chiqadi."""
        user_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        user = await self.session.get(User, user_id)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedException("Parol noto'g'ri", "INVALID_PASSWORD")

        branch_id = current.get("branch_id")
        branch_uuid = UUID(branch_id) if isinstance(branch_id, str) else branch_id
        sessions = await self._open_sessions(hotel_id, branch_uuid)

        # Qabul qiluvchida ochiq sessiya BO'LMASLIGI shart. `open_session` buni
        # tekshiradi, bu yerda esa tekshirilmagani uchun bitta xodimda ikkita
        # faol sessiya paydo bo'lishi mumkin edi — o'shanda kassa ko'rsatkichi
        # bir sessiyadan, topshiriladigan summa boshqasidan olinib, tushum
        # yo'qolgandek ko'rinardi.
        mine = next((s for s, _ in sessions if s.user_id == user_id), None)
        if mine is not None:
            raise ConflictException(
                "Sizda ochiq smena bor — avval uni yakunlang",
                "SHIFT_ALREADY_OPEN",
            )

        pending = next(
            (s for s, _ in sessions if s.status == "PENDING_HANDOVER" and s.user_id != user_id),
            None,
        )
        if not pending:
            raise NotFoundException("Qabul qilinadigan smena topilmadi", "NO_PENDING_SHIFT")

        pending.status = "CLOSED"
        pending.accepted_by = user_id
        pending.accepted_at = datetime.now(timezone.utc)

        # Qabul qiluvchining yangi sessiyasi — topshirilgan kassa bilan boshlanadi
        new_s = ShiftSession(
            hotel_id=hotel_id,
            branch_id=branch_uuid,
            user_id=user_id,
            status="ACTIVE",
            started_at=datetime.now(timezone.utc),
            opening_cash=_dec(pending.counted_cash or 0),
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

    async def correct_session(
        self,
        hotel_id: UUID,
        current: dict,
        session_id: UUID,
        counted_cash: float,
        note: str,
    ) -> dict:
        """Yopilgan sessiyadagi sanalgan summani tuzatish (admin/menejer).

        Audit: eski qiymat o'chirilmaydi — har bir tuzatish (eski/yangi,
        kim, qachon, izoh) corrections ro'yxatiga qo'shib boriladi.
        """
        is_admin = current["user_type"] in ("ADMIN", "SUPER_ADMIN")
        has_perm = "shift.force_close" in (current.get("permissions") or [])
        if not (is_admin or has_perm):
            raise ForbiddenException("Tuzatish huquqi yo'q", "FORBIDDEN")

        s = await self.session.get(ShiftSession, session_id)
        if not s or s.hotel_id != hotel_id:
            raise NotFoundException("Sessiya topilmadi", "SHIFT_NOT_FOUND")
        if s.status != "CLOSED":
            raise ConflictException(
                "Faqat yopilgan sessiyani tuzatish mumkin", "SHIFT_NOT_CLOSED"
            )

        corrector_id = UUID(current["id"]) if isinstance(current["id"], str) else current["id"]
        corrector = await self.session.get(User, corrector_id)

        old_counted = float(s.counted_cash) if s.counted_cash is not None else None
        old_diff = float(s.cash_diff) if s.cash_diff is not None else None
        s.counted_cash = _dec(counted_cash)
        s.cash_diff = _dec(counted_cash) - _dec(s.expected_cash or 0)

        entry = {
            "old_counted_cash": old_counted,
            "new_counted_cash": float(counted_cash),
            "old_diff": old_diff,
            "new_diff": float(s.cash_diff),
            "corrected_by": str(corrector_id),
            "corrected_by_name": (
                f"{corrector.first_name} {corrector.last_name}" if corrector else None
            ),
            "corrected_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
        }
        # YANGI ro'yxat — JSONB o'zgarishini SQLAlchemy sezishi uchun
        s.corrections = [*(s.corrections or []), entry]
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
        rows = result.all()

        # Qabul qilgan / yopgan shaxslarning ism-familiyalari (bitta so'rovda)
        extra_ids = {s.accepted_by for s, _ in rows if s.accepted_by} | {
            s.closed_by for s, _ in rows if s.closed_by
        }
        names: dict = {}
        if extra_ids:
            ures = await self.session.execute(select(User).where(User.id.in_(extra_ids)))
            names = {u.id: f"{u.first_name} {u.last_name}" for u in ures.scalars()}

        out = []
        for s, u in rows:
            # Tarixda kassa raqamlari ochiq — sessiya allaqachon yopilgan
            d = self._serialize(s, u, include_cash=s.status == "CLOSED")
            d["accepted_at"] = s.accepted_at.isoformat() if s.accepted_at else None
            d["accepted_by_name"] = names.get(s.accepted_by)
            d["closed_by_name"] = names.get(s.closed_by)
            out.append(d)
        return out
