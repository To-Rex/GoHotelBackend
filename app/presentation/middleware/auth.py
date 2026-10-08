from uuid import UUID
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.services.configurator_access import (
    CONFIGURATOR_TYPE,
    effective_user_type,
)
from app.application.services.work_hours_access import assert_within_work_hours
from app.core.database import get_db
from app.infrastructure.auth.jwt import decode_token
from app.infrastructure.database.repositories.user_repo import UserRepository, SessionRepository
from app.infrastructure.tenant.branch_scope import set_branch_scope
from sqlalchemy.ext.asyncio import AsyncSession

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    def _safe_uuid(value):
        try:
            return UUID(value) if value else None
        except (ValueError, TypeError, AttributeError):
            return None

    user_type = payload.get("user_type", "")
    hotel_id = _safe_uuid(payload.get("hotel_id"))

    # --- Sozlovchi: sessiya hamon ochiqmi ---
    #
    # Sozlovchi istalgan mehmonxonaga kira oladi, shuning uchun uning tokeni
    # boshqalarnikidan qat'iyroq tekshiriladi: panelda to'xtatilsa/o'chirilsa
    # yoki boshqa mehmonxonaga o'tsa (eski sessiya yopiladi), eski token
    # muddati tugashini kutmay DARHOL ishlamay qoladi. Bitta indeksli so'rov,
    # faqat sozlovchi uchun.
    if user_type == CONFIGURATOR_TYPE:
        live = await SessionRepository(session).get_by_jti(payload.get("jti") or "")
        if (
            live is None
            or live.revoked_at is not None
            or str(live.user_id) != str(user_id)
        ):
            raise HTTPException(status_code=401, detail="Session revoked")

    # --- Qurilma hamon ruxsat etilganmi ---
    #
    # Faqat kirishda tekshirish yetarli emas: administrator qurilmani
    # taqiqlasa yoki o'chirsa, unda allaqachon kirgan xodim token
    # muddatigacha (refresh bilan esa undan uzoq) ishlab yuraverardi.
    #
    # Qurilma tokenning o'zidan olinadi — u imzolangan va o'zgartirib
    # bo'lmaydi. Eski, qurilmasiz tokenlar uchun sarlavhaga tushamiz:
    # ular tabiiy ravishda tugaydi, lekin shu oraliqda ham tekshiruv
    # ishlashi kerak.
    device_id = payload.get("device_id") or request.headers.get("x-device-id")
    if device_id and hotel_id:
        from app.application.services.device_service import DeviceService

        await DeviceService(session).assert_session_allowed(
            hotel_id, device_id, user_type
        )

    # --- Filial: so'rov faqat shu filial ichida ishlaydi ---
    #
    # Xodim — HAR DOIM o'z yozuvidagi filial (administrator uni boshqa
    # filialga o'tkazsa, token yangilanishini kutmay darhol kuchga kiradi).
    # Administrator / sozlovchi — tokendagi tanlov. Bo'lmasa — asosiy filial.
    # Shundan keyin sessiyadagi barcha so'rovlar shu filial bilan
    # cheklanadi (app/infrastructure/tenant/branch_scope.py).
    branch_id = await resolve_request_branch(
        session,
        _safe_uuid(user_id),
        hotel_id,
        None if user_type == "EMPLOYEE" else _safe_uuid(payload.get("branch_id")),
    )
    set_branch_scope(session, hotel_id, branch_id)

    current_user = {
        "id": _safe_uuid(user_id),
        # Mehmonxona tanlagan sozlovchi shu mehmonxonada administrator kabi
        # ishlaydi; haqiqiy turi `actual_user_type` da (configurator_access)
        "user_type": effective_user_type(user_type, hotel_id),
        "actual_user_type": user_type,
        "hotel_id": hotel_id,
        "branch_id": branch_id,
        "permissions": payload.get("permissions", []),
        "jti": payload.get("jti", ""),
        "device_id": device_id,
    }

    # --- Ish vaqtidan tashqarida xodim ishlamaydi (sozlama yoqilgan bo'lsa) ---
    #
    # Shu yerda, chunki faqat bu nuqta BARCHA autentifikatsiyali so'rovlarni
    # qamraydi. Administrator, ruxsat belgisi bor xodim va ochiq smenadagi
    # qabulxona to'silmaydi; mehmonxona to'xtatilgan bo'lsa HOTEL_* ustun.
    # Batafsil: app/application/services/work_hours_access.py
    #
    # Yo'l `scope["path"]` dan olinadi — marshrutlash aynan shuni solishtiradi.
    # `request.url.path` URL'ni qayta tahlil qiladi: `%3F`/`%23` (dekodlangan
    # `?`/`#`) dan keyingi qismni kesib tashlaydi, shunda
    # `/notifications/register-device%3F/...` kabi so'rov ochiq yo'l bo'lib
    # ko'rinib, to'siqni chetlab o'tardi.
    await assert_within_work_hours(
        session, current_user, request.scope.get("path") or request.url.path
    )

    return current_user


async def resolve_request_branch(
    session: AsyncSession,
    user_id: UUID | None,
    hotel_id: UUID | None,
    token_branch_id: UUID | None,
) -> UUID | None:
    """So'rov filiali: tokendagi (shu mehmonxonaniki bo'lsa), aks holda
    xodim yozuvidagi, u ham bo'lmasa — mehmonxonaning asosiy filiali.

    Filial yozuvi sessiyaga (identity map) yuklanadi — keyin shu so'rovdagi
    sozlama o'qishlari (branch_settings) bazaga qayta bormaydi.
    """
    if hotel_id is None:
        return None
    from sqlalchemy import select

    from app.infrastructure.database.models.branch import Branch
    from app.infrastructure.database.models.user import User

    if token_branch_id is not None:
        branch = await session.get(Branch, token_branch_id)
        if branch is not None and str(branch.hotel_id) == str(hotel_id):
            return branch.id
    if user_id is not None:
        own = (
            await session.execute(
                select(Branch)
                .join(User, User.branch_id == Branch.id)
                .where(User.id == user_id, Branch.hotel_id == hotel_id)
            )
        ).scalars().first()
        if own is not None:
            return own.id
    main = (
        await session.execute(
            select(Branch)
            .where(Branch.hotel_id == hotel_id)
            .order_by(Branch.is_main_branch.desc(), Branch.created_at, Branch.id)
            .limit(1)
        )
    ).scalars().first()
    return main.id if main is not None else None


def require_permission(permission_code: str):
    async def permission_checker(
        current_user: dict = Depends(get_current_user),
    ) -> dict:
        user_type = current_user.get("user_type", "")
        if user_type == "SUPER_ADMIN":
            return current_user
        if user_type == "ADMIN":
            return current_user
        permissions = current_user.get("permissions", [])
        if permission_code not in permissions:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission: {permission_code}",
            )
        return current_user

    return permission_checker
