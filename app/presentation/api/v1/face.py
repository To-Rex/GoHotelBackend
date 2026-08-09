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
from datetime import datetime, timezone

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import face_service
from app.application.services.auth_service import AuthService
from app.core.database import get_db
from app.core.exceptions import UnauthorizedException, ValidationException
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
    """Ochiq endpoint: yuz-kirish umuman mavjudmi.

    Frontend tugmani faqat dvigatel ishlaydigan VA kamida bitta xodim yuz
    biriktirgan holatda ko'rsatadi — yuzi yo'q tizimda so'ralmaydi.
    """
    if not face_service.engine_importable():
        return {"available": False}
    count = (
        await session.execute(select(func.count(UserFaceProfile.id)))
    ).scalar() or 0
    return {"available": count > 0}


@router.post("/login")
async def face_login(
    request: Request,
    file: UploadFile = File(),
    session: AsyncSession = Depends(get_db),
):
    """Kameradan olingan kadr bo'yicha xodimni topib, token beradi."""
    _require_engine()
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

    # Faol xodimlarning barcha yuz profillari bilan solishtiramiz
    rows = (
        await session.execute(
            select(UserFaceProfile, User)
            .join(User, User.id == UserFaceProfile.user_id)
            .where(User.is_deleted.is_(False), User.status == "ACTIVE")
        )
    ).all()

    best_profile = None
    best_user = None
    best_score = 0.0
    for profile, user in rows:
        stored = face_service.parse_embedding(profile.embedding)
        if not stored:
            continue
        score = face_service.cosine_similarity(embedding, stored)
        if score > best_score:
            best_score = score
            best_profile = profile
            best_user = user

    if best_user is None or best_score < face_service.MATCH_THRESHOLD:
        logger.warning(
            "Yuz-kirish rad etildi: BEGONA shaxs urinishi (eng yaxshi o'xshashlik: %.3f)",
            best_score,
        )
        raise UnauthorizedException(
            "Begona shaxs: bu yuz tizimda ro'yxatdan o'tmagan. "
            "Parol bilan kiring yoki avval tizimga kirib yuzingizni biriktiring",
            "FACE_NOT_RECOGNIZED",
        )

    best_profile.last_used_at = datetime.now(timezone.utc)
    logger.info(
        "Yuz-kirish: %s (o'xshashlik: %.3f)", best_user.username, best_score
    )
    return await AuthService(session).issue_tokens(
        best_user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
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
