"""Xodim hisobotiga kim kira oladi.

Shaxsiy hisobot (`GET /reports/my-summary`) har kimga O'ZINIKI uchun
ochiq. Boshqa xodimning hisobotini administrator (ADMIN/SUPER_ADMIN,
mehmonxona tanlagan sozlovchi ham) va menejer — moliya yoki kassa
nazorati huquqi bor xodim (`finance.view`, `shift.force_close`,
`report.view`) ko'radi. Bu `/finance/by-staff` bilan bir xil doira
(`finance_summary_service.can_view_staff_report`): u yerda ham xodimlar
qancha pul olgani ochiladi, shuning uchun qoida bitta.

Tanlanadigan xodimlar ro'yxati (`GET /reports/staff`) — shu
mehmonxonaning (joriy filialning) xodimlari. O'chirilganlar chiqmaydi;
faol bo'lmaganlar ro'yxat oxirida — ularning ham o'tgan davr hisoboti
kerak bo'lib qoladi. So'rovchining o'zi doim ro'yxatda va birinchi.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.finance_summary_service import can_view_staff_report
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.user import User
from app.infrastructure.tenant.branch_scope import users_in_branch

FORBIDDEN_CODE = "STAFF_REPORT_FORBIDDEN"
ACTIVE = "ACTIVE"


def _as_uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def user_brief(user: User, me: UUID | None = None) -> dict:
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    return {
        "id": str(user.id),
        "first_name": first,
        "last_name": last,
        "name": f"{first} {last}".strip() or user.username,
        "user_type": user.user_type,
        "status": user.status,
        "is_me": me is not None and user.id == me,
    }


async def resolve_report_user(
    session: AsyncSession,
    current_user: dict,
    hotel_id: UUID | None,
    requested: UUID | None,
) -> tuple[UUID, User | None]:
    """Hisobot kimniki bo'lishini aniqlaydi.

    `requested` bo'sh yoki so'rovchining o'zi — o'z hisoboti, ruxsat
    kerak emas. Boshqa xodim — faqat administrator/menejer (403), va u
    shu mehmonxonaning tirik xodimi bo'lishi kerak (404).
    """
    me = _as_uuid(current_user["id"])
    if requested is None or requested == me:
        return me, await session.get(User, me)
    if not can_view_staff_report(current_user):
        raise ForbiddenException(
            "Boshqa xodimning hisobotini faqat administrator yoki menejer ko'ra oladi",
            FORBIDDEN_CODE,
        )
    target = await session.get(User, requested)
    if (
        target is None
        or target.is_deleted
        or (hotel_id is not None and target.hotel_id != hotel_id)
    ):
        raise NotFoundException("Xodim topilmadi", "USER_NOT_FOUND")
    return requested, target


async def staff_options(
    session: AsyncSession, current_user: dict, hotel_id: UUID | None
) -> list[dict]:
    """Hisobotini ko'rish mumkin bo'lgan xodimlar — administrator va
    menejer uchun; boshqalarga 403."""
    if not can_view_staff_report(current_user):
        raise ForbiddenException(
            "Xodimlar hisobotini faqat administrator yoki menejer ko'ra oladi",
            FORBIDDEN_CODE,
        )
    me = _as_uuid(current_user["id"])
    stmt = select(User).where(User.is_deleted.is_(False))
    if hotel_id is not None:
        stmt = stmt.where(User.hotel_id == hotel_id)
    # Joriy filial xodimlari + mehmonxona administratorlari (filial chegarasi)
    scope = users_in_branch(User, session, hotel_id)
    if scope is not None:
        stmt = stmt.where(scope)
    rows = list((await session.execute(stmt)).scalars().all())
    # So'rovchining o'zi doim ro'yxatda — boshqa filialda yoki mehmonxonasiz
    # tizim ma'muri bo'lsa ham
    if all(row.id != me for row in rows):
        mine = await session.get(User, me)
        if mine is not None:
            rows.append(mine)
    rows.sort(
        key=lambda u: (
            u.id != me,
            u.status != ACTIVE,
            (u.first_name or "").casefold(),
            (u.last_name or "").casefold(),
        )
    )
    return [user_brief(u, me) for u in rows]
