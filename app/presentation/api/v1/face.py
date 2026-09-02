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
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
