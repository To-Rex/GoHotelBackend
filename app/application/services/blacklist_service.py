"""Mehmonlar qora ro'yxati.

Nojo'ya xatti-harakat qilgan — janjal ko'targan, zarar yetkazgan — mehmonni
qayta qabul qilmaslik uchun. Ro'yxatga faqat ADMINISTRATOR qo'sha oladi va
SABAB majburiy: "nega bu odam ro'yxatda?" degan savolga bir yildan keyin ham
javob bo'lishi kerak, aks holda ro'yxat ishonchini yo'qotadi va xodimlar uni
chetlab o'ta boshlaydi.

Ro'yxatdagi mehmonga xizmat ko'rsatish STANDART HOLDA taqiqlanadi. Lekin
mehmonxonalar bir xil emas: birida bu qat'iy taqiq, birida esa faqat
ogohlantirish bo'lishi kerak — shuning uchun qoida sozlamadan boshqariladi.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.user import User

BLACKLIST_SETTINGS_KEY = "blacklist_policy"

#: Standart: ro'yxatdagi mehmonga bron ochib bo'lmaydi.
#:
#: Taqiq standart bo'lgani ataylab — administrator kimnidir ro'yxatga
#: qo'shganda u xizmat ko'rsatilmasligini kutadi. Yumshoq rejimni ongli
#: ravishda yoqish kerak.
DEFAULT_BLOCK_BOOKING = True


def resolve_block_booking(hotel_settings: dict | None) -> bool:
    """Qora ro'yxatdagi mehmonga bron ochish taqiqlanganmi."""
    policy = (hotel_settings or {}).get(BLACKLIST_SETTINGS_KEY) or {}
    value = policy.get("block_booking", DEFAULT_BLOCK_BOOKING)
    return bool(value) if isinstance(value, bool) else DEFAULT_BLOCK_BOOKING


def _require_admin(current_user: dict) -> None:
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException(
            "Qora ro'yxatni faqat administrator boshqaradi", "ADMIN_ONLY"
        )


class BlacklistService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _get_guest(self, guest_id: UUID, hotel_id: UUID | None) -> Guest:
        stmt = select(Guest).where(Guest.id == guest_id)
        if hotel_id is not None:
            stmt = stmt.where(Guest.hotel_id == hotel_id)
        guest = (await self.session.execute(stmt)).scalar_one_or_none()
        if guest is None:
            raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")
        return guest

    async def add(
        self,
        guest_id: UUID,
        hotel_id: UUID | None,
        reason: str,
        current_user: dict,
    ) -> Guest:
        _require_admin(current_user)
        text = (reason or "").strip()
        if not text:
            # Sababsiz yozuv ro'yxatni foydasiz qiladi
            raise ValidationException(
                "Qora ro'yxatga qo'shish sababini yozing", "REASON_REQUIRED"
            )

        guest = await self._get_guest(guest_id, hotel_id)
        if guest.blacklisted_at is not None:
            raise ConflictException(
                "Bu mehmon allaqachon qora ro'yxatda", "ALREADY_BLACKLISTED"
            )

        guest.blacklisted_at = datetime.now(timezone.utc)
        guest.blacklist_reason = text
        guest.blacklisted_by = current_user["id"]
        await self.session.flush()
        return guest

    async def remove(
        self, guest_id: UUID, hotel_id: UUID | None, current_user: dict
    ) -> Guest:
        _require_admin(current_user)
        guest = await self._get_guest(guest_id, hotel_id)
        # Uchala maydon birga tozalanadi — yarim holat qolmasin
        guest.blacklisted_at = None
        guest.blacklist_reason = None
        guest.blacklisted_by = None
        await self.session.flush()
        return guest

    async def list_blacklisted(self, hotel_id: UUID | None) -> list[dict]:
        stmt = (
            select(Guest, User.first_name, User.last_name)
            .join(User, User.id == Guest.blacklisted_by, isouter=True)
            .where(Guest.blacklisted_at.isnot(None))
            .order_by(Guest.blacklisted_at.desc())
        )
        if hotel_id is not None:
            stmt = stmt.where(Guest.hotel_id == hotel_id)

        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "guest_id": g.id,
                "first_name": g.first_name,
                "last_name": g.last_name,
                "phone": g.phone,
                "passport_number": g.passport_number,
                "blacklisted_at": g.blacklisted_at,
                "blacklist_reason": g.blacklist_reason,
                "blacklisted_by": g.blacklisted_by,
                "blacklisted_by_name": " ".join(
                    p for p in (first, last) if p
                ).strip()
                or None,
            }
            for g, first, last in rows
        ]

    async def assert_bookable(
        self, guest_ids: list[UUID], hotel_id: UUID
    ) -> None:
        """Bu odamlarga bron ochish mumkinmi.

        Asosiy mehmon ham, hamrohlar ham tekshiriladi: qora ro'yxatdagi odam
        boshqa birovning nomiga hamroh bo'lib kirib ketmasligi kerak — aks
        holda taqiqni aylanib o'tish oson bo'lardi.

        Sozlama o'chirilgan bo'lsa tekshiruv o'tkazib yuboriladi: qoidani
        mehmonxona o'zi tanlaydi.
        """
        ids = [gid for gid in guest_ids if gid]
        if not ids:
            return

        from app.infrastructure.database.models.hotel import Hotel

        hotel = await self.session.get(Hotel, hotel_id)
        if not resolve_block_booking(hotel.settings if hotel else None):
            return

        rows = (
            await self.session.execute(
                select(Guest.first_name, Guest.last_name, Guest.blacklist_reason)
                .where(
                    Guest.id.in_(ids),
                    Guest.blacklisted_at.isnot(None),
                )
                .limit(1)
            )
        ).first()
        if rows is None:
            return

        name = " ".join(p for p in (rows[0], rows[1]) if p).strip() or "Mehmon"
        reason = (rows[2] or "").strip()
        raise ConflictException(
            f"{name} qora ro'yxatda — bron ochib bo'lmaydi."
            + (f" Sabab: {reason}" if reason else "")
            + " Administrator ro'yxatdan chiqarishi mumkin.",
            "GUEST_BLACKLISTED",
        )
