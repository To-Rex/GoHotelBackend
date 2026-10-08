"""Filial sozlamalari — har filial o'z sozlamalarini yuritadi.

Avval barcha sozlamalar (bron, kassa, smena, jarima, chegirma, farrosh
taqsimoti, skaner, chek dizayni...) `hotels.settings` JSON'ida — butun
mehmonxona uchun bitta edi. Filiallar to'liq ajratilgach ular
`branches.settings` da turadi: migratsiya (a6b7c8d9e0f1) har filialga
mehmonxona sozlamalarini nusxaladi, keyin har filial o'zinikini
o'zgartiradi.

`settings_owner()` sozlamalar EGASINI qaytaradi — `.settings` o'qiladi
va yoziladi (`owner.settings = {...}`), xuddi avvalgi `hotel.settings`
kabi. Shuning uchun servislardagi `resolve_*_settings(owner.settings)`
chaqiruvlari o'zgarmadi — faqat `Hotel` o'rniga filial keladi.

Qaysi filial: berilgani -> so'rov filiali (branch_scope) -> asosiy filial.
Fon vazifalari yozuvning o'z filialini beradi (bron, vazifa...).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.tenant.branch_scope import scoped_branch_id


async def main_branch(session: AsyncSession, hotel_id: UUID) -> Branch | None:
    """Mehmonxonaning asosiy (bo'lmasa eng birinchi) filiali."""
    return (
        await session.execute(
            select(Branch)
            .where(Branch.hotel_id == hotel_id)
            .order_by(Branch.is_main_branch.desc(), Branch.created_at, Branch.id)
            .limit(1)
        )
    ).scalars().first()


async def settings_owner(
    session: AsyncSession,
    hotel_id: UUID | None,
    branch_id: UUID | None = None,
) -> Branch | Hotel | None:
    """Sozlamalar egasi — filial (`.settings` o'qiladi va yoziladi).

    Filiali yo'q mehmonxona (migratsiyadan keyin bo'lmaydi) — mehmonxonaning
    o'zi qaytadi, avvalgi tartib buzilmaydi.
    """
    if hotel_id is None:
        return None
    wanted = branch_id or scoped_branch_id(session, hotel_id)
    branch = await session.get(Branch, wanted) if wanted else None
    if branch is not None and str(branch.hotel_id) != str(hotel_id):
        branch = None
    if branch is None:
        branch = await main_branch(session, hotel_id)
    if branch is None:
        return await session.get(Hotel, hotel_id)
    if branch.settings is None:
        # Yangi filial: mehmonxona sozlamalaridan boshlanadi (bazaga faqat
        # o'zgartirilganda yoziladi)
        hotel = await session.get(Hotel, hotel_id)
        set_committed_value(branch, "settings", dict((hotel.settings if hotel else None) or {}))
    return branch


async def branch_settings(
    session: AsyncSession,
    hotel_id: UUID | None,
    branch_id: UUID | None = None,
) -> dict:
    """Faqat o'qish uchun: filial sozlamalari (bo'sh bo'lsa `{}`)."""
    owner = await settings_owner(session, hotel_id, branch_id)
    return dict((owner.settings if owner is not None else None) or {})
