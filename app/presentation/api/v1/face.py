"""Yuz bilan kirish endpointlari.

Umumiy terminal stsenariysi: bitta kompyuter/planshetda turli xodimlar
kameraga qarab O'Z hisoblariga kirishadi (1:N identifikatsiya).

- availability — ochiq: dvigatel bormi va kamida bitta yuz biriktirilganmi
  (frontend "Yuz bilan kirish" tugmasini faqat shu holatda ko'rsatadi);
- login — ochiq: kadr yuboriladi, mos xodim topilsa token beriladi;
- enroll/status/delete — tizimga kirgan xodim o'z yuzini boshqaradi.

Rasm saqlanmaydi — faqat embedding. Har kirish last_used_at'da iz qoldiradi.
"""
import logging
from uuid import UUID
from datetime import datetime, timezone

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import face_service
from app.application.services.auth_service import AuthService
from app.core.database import get_db
from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.infrastructure.database.models.user import User
from app.infrastructure.database.models.user_face_profile import UserFaceProfile
from app.presentation.middleware.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# Har bir xodim uchun ko'pi bilan nechta yuz namunasi saqlanadi (turli
# burchak/yorug'lik uchun bir nechtasi aniqlikni oshiradi)
MAX_PROFILES_PER_USER = 3

# Yuklanadigan kadr uchun oqilona chegara
MAX_IMAGE_BYTES = 8 * 1024 * 1024


async def _read_image(file: UploadFile) -> bytes:
    content = await file.read()
    if not content:
        raise ValidationException("Empty image", "BAD_IMAGE")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValidationException("Image too large", "IMAGE_TOO_LARGE")
    return content


def _require_engine() -> None:
    if not face_service.engine_importable():
        raise HTTPException(
            status_code=503,
            detail="Face login is not available on this server",
        )


@router.get("/availability")
async def face_availability(session: AsyncSession = Depends(get_db)):
    """Ochiq endpoint: yuz tekshiruvi dvigateli ishlaydimi.

    Ikkinchi bosqich shu bo'yicha ishlaydi: dvigatel yo'q serverda yuz
    tekshiruvi so'ralmaydi va parol yetarli bo'lib qoladi. Xodimlar soni bu
    yerda ahamiyatsiz — tekshiruv aynan bitta xodimning profillari bo'yicha
    bo'ladi, ro'yxat bo'yicha emas.
    """
    if not face_service.engine_importable():
        return {"available": False}
    count = (
        await session.execute(select(func.count(UserFaceProfile.id)))
    ).scalar() or 0
    return {"available": True, "enrolled_users": int(count)}


@router.post("/verify-login")
async def verify_login(
    request: Request,
    face_token: str = Form(),
    file: UploadFile = File(),
    session: AsyncSession = Depends(get_db),
):
    """Ikkinchi bosqich: login-parol to'g'ri kelgach yuzni tekshirish.

    Ilgari bu yerda to'g'ridan-to'g'ri yuz bilan kirish bor edi: kadr barcha
    xodimlar bilan solishtirilib, mos kelgani uchun token berilardi. Endi
    unday emas va bu ataylab:

      - kirish PAROLDAN boshlanadi, yuz esa ikkinchi bosqich. Yuzning o'zi
        bilan kirib bo'lmaydi;

      - kadr faqat `face_token` ko'rsatgan xodimning O'Z profillari bilan
        solishtiriladi. Ilgari boshqa xodimning yuzi ham qabul qilinardi —
        parolini bilgan odam yonidagi hamkasbining yuzi bilan kirib ketishi
        mumkin edi. Endi bunday urinish rad etiladi.
    """
    _require_engine()
    auth = AuthService(session)
    user = await auth.resolve_face_challenge(face_token)

    content = await _read_image(file)
    # CPU-og'ir hisob event loop'ni bloklamasligi uchun thread'da
    try:
        embedding = await anyio.to_thread.run_sync(
            face_service.compute_embedding, content
        )
    except ValueError as e:
        code = str(e)
        raise ValidationException(
            "Yuz aniqlanmadi — kameraga to'g'ri qarab qayta uriring"
            if code == "NO_FACE"
            else "Rasm o'qilmadi",
            code,
        )

    profiles = (
        (
            await session.execute(
                select(UserFaceProfile).where(UserFaceProfile.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    if not profiles:
        # Token berilgandan keyin yuz o'chirilgan bo'lsa — parol yetarli
        logger.info("Yuz profili topilmadi, parol bilan kiritildi: %s", user.username)
        return await auth.issue_tokens(
            user,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            device_id=request.headers.get("x-device-id"),
        )

    best = 0.0
    best_profile = None
    for profile in profiles:
        stored = face_service.parse_embedding(profile.embedding)
        if not stored:
            continue
        score = face_service.cosine_similarity(embedding, stored)
        if score > best:
            best = score
            best_profile = profile

    if best_profile is None or best < face_service.MATCH_THRESHOLD:
        logger.warning(
            "Yuz tekshiruvi rad etildi: %s (eng yaxshi o'xshashlik: %.3f)",
            user.username,
            best,
        )
        raise UnauthorizedException(
            "Yuz mos kelmadi. Bu hisobga faqat uning egasi kira oladi — "
            "kameraga to'g'ri qarab qayta urining",
            "FACE_MISMATCH",
        )

    best_profile.last_used_at = datetime.now(timezone.utc)
    logger.info("Yuz tekshiruvi o'tdi: %s (o'xshashlik: %.3f)", user.username, best)
    return await auth.issue_tokens(
        user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        device_id=request.headers.get("x-device-id"),
    )


@router.get("/status")
async def face_status(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Joriy xodimning yuz profili holati (sozlash dialogi uchun)."""
    count = (
        await session.execute(
            select(func.count(UserFaceProfile.id)).where(
                UserFaceProfile.user_id == current_user["id"]
            )
        )
    ).scalar() or 0
    return {
        "engine_available": face_service.engine_importable(),
        "enrolled": count > 0,
        "count": count,
    }


@router.post("/enroll")
async def face_enroll(
    file: UploadFile = File(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Joriy xodim o'z yuzini biriktiradi (bir nechta namunagacha)."""
    _require_engine()
    content = await _read_image(file)
    try:
        embedding = await anyio.to_thread.run_sync(
            face_service.compute_embedding, content
        )
    except ValueError as e:
        code = str(e)
        raise ValidationException(
            "Yuz aniqlanmadi — kameraga to'g'ri qarab qayta uriring"
            if code == "NO_FACE"
            else "Rasm o'qilmadi",
            code,
        )

    profile = UserFaceProfile(
        user_id=current_user["id"],
        embedding=face_service.serialize_embedding(embedding),
        device_label=None,
    )
    session.add(profile)
    await session.flush()

    # Limitdan oshsa eng eskilari o'chiriladi
    existing = (
        (
            await session.execute(
                select(UserFaceProfile)
                .where(UserFaceProfile.user_id == current_user["id"])
                .order_by(UserFaceProfile.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for old in existing[MAX_PROFILES_PER_USER:]:
        await session.delete(old)
    await session.flush()

    return {"enrolled": True, "count": min(len(existing), MAX_PROFILES_PER_USER)}


@router.delete("/enroll")
async def face_unenroll(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Joriy xodimning barcha yuz profillarini o'chirish."""
    await session.execute(
        delete(UserFaceProfile).where(UserFaceProfile.user_id == current_user["id"])
    )
    await session.flush()
    return {"enrolled": False, "count": 0}


#: Boshqa xodimning yuzini o'chira oladiganlar.
#:
#: Menejer belgisi — `shift.force_close` ruxsati (loyihada menejer aynan shu
#: bilan ajratiladi). Administrator ham kiradi.
def _may_reset_others(current_user: dict) -> bool:
    if current_user["user_type"] in ("ADMIN", "SUPER_ADMIN"):
        return True
    return "shift.force_close" in (current_user.get("permissions") or [])


@router.get("/users")
async def face_users(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Xodimlar va ularning yuz holati — menejer ko'rishi uchun."""
    if not _may_reset_others(current_user):
        raise ForbiddenException(
            "Bu ro'yxatni menejer yoki administrator ko'radi", "MANAGER_ONLY"
        )

    hotel_id = current_user.get("hotel_id")
    stmt = (
        select(
            User.id,
            User.first_name,
            User.last_name,
            User.username,
            User.user_type,
            func.count(UserFaceProfile.id).label("faces"),
        )
        .join(UserFaceProfile, UserFaceProfile.user_id == User.id, isouter=True)
        .where(User.is_deleted.is_(False), User.status == "ACTIVE")
        .group_by(User.id, User.first_name, User.last_name, User.username, User.user_type)
        .order_by(User.first_name, User.last_name)
    )
    if hotel_id is not None:
        stmt = stmt.where(User.hotel_id == hotel_id)

    rows = (await session.execute(stmt)).all()
    return [
        {
            "user_id": r.id,
            "name": " ".join(p for p in (r.first_name, r.last_name) if p).strip()
            or r.username,
            "username": r.username,
            "user_type": r.user_type,
            "face_count": int(r.faces or 0),
            "enrolled": int(r.faces or 0) > 0,
        }
        for r in rows
    ]


@router.delete("/enroll/{user_id}")
async def face_unenroll_user(
    user_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Boshqa xodimning yuzini o'chirish — menejer yoki administrator.

    Nega kerak: yuz tanilmay qolsa (soqol, ko'zoynak, jarohat yoki shunchaki
    sifatsiz kadr) xodim tizimga umuman kira olmaydi — kirish uchun yuz
    kerak, yuzni almashtirish uchun esa kirish kerak. Bu yopiq halqani
    faqat tashqaridan uzish mumkin.

    O'chirilgandan keyin xodim parol bilan kiradi va tizim undan yangi yuz
    biriktirishni so'raydi.
    """
    if not _may_reset_others(current_user):
        raise ForbiddenException(
            "Boshqa xodimning yuzini menejer yoki administrator o'chiradi",
            "MANAGER_ONLY",
        )

    target = await session.get(User, user_id)
    if target is None or target.is_deleted:
        raise NotFoundException("User not found", "USER_NOT_FOUND")
    # Boshqa mehmonxona xodimiga tegib bo'lmaydi
    hotel_id = current_user.get("hotel_id")
    if hotel_id is not None and target.hotel_id != hotel_id:
        raise NotFoundException("User not found", "USER_NOT_FOUND")

    await session.execute(
        delete(UserFaceProfile).where(UserFaceProfile.user_id == user_id)
    )
    await session.flush()
    logger.info(
        "Yuz o'chirildi: %s (bajardi: %s)", target.username, current_user["id"]
    )
    return {"user_id": str(user_id), "enrolled": False, "count": 0}
