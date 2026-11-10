import re
from datetime import datetime, timezone
from uuid import UUID

from typing import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.repositories.base import TenantBaseRepository


class GuestRepository(TenantBaseRepository[Guest]):
    model = Guest

    async def get_all(
        self, hotel_id: UUID, skip: int = 0, limit: int = 100, **filters
    ) -> Sequence[Guest]:
        stmt = select(Guest).where(Guest.hotel_id == hotel_id)
        for key, value in filters.items():
            if value is not None and hasattr(Guest, key):
                stmt = stmt.where(getattr(Guest, key) == value)
        stmt = stmt.order_by(Guest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all_global(
        self, skip: int = 0, limit: int = 100
    ) -> Sequence[Guest]:
        """Global ro'yxat: mehmonxonadan qat'i nazar, yangilari birinchi."""
        stmt = (
            select(Guest)
            .order_by(Guest.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search(
        self, hotel_id: UUID | None, query: str, skip: int = 0, limit: int = 100
    ) -> list[Guest]:
        conditions = [
            Guest.first_name.ilike(f"%{query}%"),
            Guest.last_name.ilike(f"%{query}%"),
            Guest.phone.ilike(f"%{query}%"),
            Guest.email.ilike(f"%{query}%"),
            Guest.passport_number.ilike(f"%{query}%"),
            Guest.id_document_number.ilike(f"%{query}%"),
        ]
        digits = re.sub(r"\D", "", query)
        if len(digits) >= 7:
            stored = func.regexp_replace(func.coalesce(Guest.phone, ""), r"\D", "", "g")
            if len(digits) >= 9:
                conditions.append(func.right(stored, 9) == digits[-9:])
            else:
                conditions.append(stored == digits)
        stmt = select(Guest).where(Guest.is_deleted.is_(False), or_(*conditions))
        if hotel_id is not None:
            stmt = stmt.where(Guest.hotel_id == hotel_id)
        stmt = stmt.order_by(Guest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def normalize_passport(passport_number: str | None) -> str:
        """Passport: faqat harf va raqamlar, bosh harfda ("ad 446-9339" → "AD4469339")."""
        return re.sub(r"[^A-Z0-9]", "", (passport_number or "").upper())

    @staticmethod
    def normalize_phone(phone: str | None) -> str:
        """Telefon: faqat raqamlar ("+998 88 153-44-33" → "998881534433")."""
        return re.sub(r"\D", "", phone or "")

    async def find_duplicates(
        self, passport_number: str | None, phone: str | None, limit: int = 5
    ) -> list[tuple[Guest, str]]:
        """Passport yoki telefon bo'yicha mavjud mehmonlar (global), har biri
        nima bo'yicha mos kelgani bilan: ("passport" | "phone").

        Formatga chidamli: passport harflari/raqamlaridan boshqa belgilar,
        telefonning esa raqamlardan boshqa belgilari e'tiborga olinmaydi.
        Telefon oxirgi 9 raqami bo'yicha solishtiriladi — +998 kod bilan
        yoki kodsiz yozilgani farq qilmaydi. Passport mosliklari birinchi,
        bir mehmon bir marta.
        """
        found: list[tuple[Guest, str]] = []
        seen: set = set()

        norm = self.normalize_passport(passport_number)
        if len(norm) >= 5:
            stmt = (
                select(Guest)
                .where(
                    Guest.is_deleted.is_(False),
                    func.upper(
                        func.regexp_replace(
                            func.coalesce(Guest.passport_number, ""),
                            "[^A-Za-z0-9]",
                            "",
                            "g",
                        )
                    )
                    == norm,
                )
                .order_by(Guest.created_at.desc())
                .limit(limit)
            )
            for guest in (await self.session.execute(stmt)).scalars().all():
                if guest.id not in seen:
                    seen.add(guest.id)
                    found.append((guest, "passport"))

        digits = self.normalize_phone(phone)
        if len(digits) >= 7 and len(found) < limit:
            tail = digits[-9:]
            stored = func.regexp_replace(
                func.coalesce(Guest.phone, ""), r"\D", "", "g"
            )
            cond = (
                func.right(stored, 9) == tail
                if len(digits) >= 9
                else stored == digits
            )
            stmt = (
                select(Guest)
                .where(
                    Guest.is_deleted.is_(False),
                    func.length(stored) >= 7,
                    cond,
                )
                .order_by(Guest.created_at.desc())
                .limit(limit)
            )
            for guest in (await self.session.execute(stmt)).scalars().all():
                if guest.id not in seen and len(found) < limit:
                    seen.add(guest.id)
                    found.append((guest, "phone"))
        return found

    async def find_duplicate(
        self, passport_number: str | None, phone: str | None
    ) -> Guest | None:
        """Birinchi moslik: avval passport, keyin telefon (find_duplicates)."""
        matches = await self.find_duplicates(passport_number, phone, limit=1)
        return matches[0][0] if matches else None

    async def get_by_passport(
        self, hotel_id: UUID, passport_number: str
    ) -> Guest | None:
        stmt = select(Guest).where(
            Guest.hotel_id == hotel_id,
            Guest.passport_number == passport_number,
            Guest.is_deleted.is_(False),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(self, guest_id: UUID, hotel_id: UUID) -> Guest | None:
        guest = await self.get_by_id(guest_id, hotel_id)
        if guest:
            guest.is_deleted = True
            guest.deleted_at = datetime.now(timezone.utc)
            await self.session.flush()
        return guest
