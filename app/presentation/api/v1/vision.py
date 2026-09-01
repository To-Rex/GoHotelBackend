"""Kamera agenti va qabulxona paneli endpointlari.

Ikki mutlaqo turli mijoz bir routerda:

* **Kamera agenti** (``/vision/events``) — qurilma tokeni bilan kiradi,
  vektor yuboradi, javobda tanilgan mehmonni oladi. Bu yo'l issiq: sekundiga
  bir necha marta chaqirilishi mumkin, shuning uchun unda hech qanday og'ir
  amal yo'q — bitta indeks qidiruvi va bitta INSERT.
* **Qabulxona paneli** (``/vision/sightings``) — xodim tokeni bilan kiradi,
  oxirgi ko'rinishlarni so'raydi, tanilmaganini mehmonga biriktiradi.

Rasm serverda faqat panelda ko'rsatish uchun, qisqa muddatga saqlanadi;
tanish esa umuman rasmsiz, faqat vektor bilan bajariladi.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import anyio
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    Query,
    Response,
    UploadFile,
)
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.vision import (
    EnrollSightingRequest,
    FaceEventRequest,
    FaceEventResponse,
    FaceProfileStatus,
    MatchedGuest,
    SightingGroupListResponse,
    SightingGroupResponse,
    SightingListResponse,
    SightingResponse,
    VisionCameraResponse,
    VisionCameraUpdateRequest,
    VisionDeviceCreateRequest,
    VisionDeviceCreatedResponse,
    VisionDeviceResponse,
)
from app.application.services import guest_face_service as gfs
from app.core.database import get_db
from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.infrastructure.database.models.face_sighting import FaceSighting, VisionDevice
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.guest_face_profile import GuestFaceProfile
from app.infrastructure.database.models.vision_camera import VisionCamera
from app.infrastructure.database.models.reservation import Reservation
from app.presentation.middleware.auth import get_current_user, require_permission

logger = logging.getLogger(__name__)

router = APIRouter()

#: Ko'rinishlar shuncha vaqtdan keyin o'chadi. Qabulxona paneli uchun bir
#: necha soat yetarli; biometrik izni undan uzoq saqlashning sababi yo'q.
SIGHTING_TTL_HOURS = 12

#: Panelda ko'rsatiladigan oyna: bundan eski ko'rinish "hozir keldi" emas.
PANEL_WINDOW_MINUTES = 30

#: Panelga saqlanadigan rasm chegarasi. Agent ~4-25 KB yuboradi; chegara
#: noto'g'ri sozlangan agent bazani to'ldirib yuborishiga qarshi.
MAX_THUMBNAIL_BYTES = 96 * 1024

#: Zaxira yo'l (serverda embedding hisoblash) uchun rasm chegarasi.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: Serverda bir vaqtda nechta zaxira embedding hisoblanadi. cv2 modellari
#: global qulf ostida ishlaydi, shuning uchun cheklovsiz qo'yish navbat
#: hosil qiladi va event loop'ni ushlab turadi.
_MAX_CONCURRENT_EMBEDS = 2
_embed_limiter = anyio.Semaphore(_MAX_CONCURRENT_EMBEDS)


# ===========================================================================
# Qurilma autentifikatsiyasi
# ===========================================================================


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def require_vision_device(
    authorization: Optional[str] = Header(default=None),
    x_device_id: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> VisionDevice:
    """``Authorization: Bearer <qurilma tokeni>`` ni tekshiradi.

    Xodim JWT'si emas: agent oylab uzluksiz ishlaydi va muddatli token unga
    to'g'ri kelmaydi. Token bazada xesh ko'rinishida turadi, qidiruv esa
    indekslangan ustun bo'yicha — ya'ni bitta tez SELECT.
    """
    if not authorization:
        raise UnauthorizedException("Qurilma tokeni yo'q", "DEVICE_TOKEN_MISSING")
    parts = authorization.split(None, 1)
    token = parts[1].strip() if len(parts) == 2 else authorization.strip()
    if not token:
        raise UnauthorizedException("Qurilma tokeni bo'sh", "DEVICE_TOKEN_MISSING")

    device = (
        await session.execute(
            select(VisionDevice).where(VisionDevice.token_hash == _hash_token(token))
        )
    ).scalar_one_or_none()

    if device is None or not device.is_active:
        logger.warning("Vision qurilma tokeni rad etildi (device_id=%s)", x_device_id)
        raise UnauthorizedException(
            "Qurilma tokeni yaroqsiz yoki bekor qilingan", "DEVICE_TOKEN_INVALID"
        )

    device.last_seen_at = datetime.now(timezone.utc)
    if x_device_id and device.device_id != x_device_id:
        device.device_id = x_device_id[:128]
    return device


# ===========================================================================
# Agent yo'li
# ===========================================================================


@router.get("/health")
async def vision_health(device: VisionDevice = Depends(require_vision_device)):
    """Agent har `health_interval` da so'raydigan tiriklik tekshiruvi.

    Ataylab tokenli: agent uchun "server ishlayaptimi" degan savol
    "mening tokenim hali ham yaroqlimi" bilan bir xil. Token bekor qilinsa
    agent buni darhol biladi va navbatni to'ldirmaydi.
    """
    return {
        "status": "ok",
        "hotel_id": str(device.hotel_id),
        "device": device.name,
        "model": gfs.MODEL_NAME,
        "dim": gfs.EMBEDDING_DIM,
    }


async def _resolve_camera(
    session: AsyncSession, device: VisionDevice, event: FaceEventRequest
) -> VisionCamera | None:
    """Kamerani ro'yxatdan topadi, yo'q bo'lsa qo'shadi.

    Filial aynan shu yerdan aniqlanadi. Qurilmadagi filialga tayanish
    yetarli emas: bitta agent bir nechta kamerani boqishi mumkin va ular
    turli filiallarda bo'lishi mumkin.

    Noma'lum kamera **rad etilmaydi, ro'yxatga qo'shiladi**. Rad etish
    xavfsizroq tuyuladi, lekin amalda yangi kamera ulanganda hodisalar
    jimgina yo'qolishiga olib kelardi. Ro'yxatda paydo bo'lgani esa
    administratorga uni ko'rib, to'g'ri filialga biriktirish imkonini beradi
    — va biriktirilmagunicha uning suratlari filial bo'yicha filtrlangan
    qabulxona ro'yxatida ko'rinmaydi.
    """
    camera = (
        await session.execute(
            select(VisionCamera).where(
                VisionCamera.device_id == device.id,
                VisionCamera.camera_id == event.camera_id[:64],
            )
        )
    ).scalar_one_or_none()

    if camera is None:
        camera = VisionCamera(
            hotel_id=device.hotel_id,
            branch_id=device.branch_id,
            device_id=device.id,
            camera_id=event.camera_id[:64],
            name=(event.camera_name or None) and event.camera_name[:128],
            location=(event.location or None) and event.location[:128],
        )
        session.add(camera)
        await session.flush()
        logger.info(
            "Yangi kamera ro'yxatga olindi: %s (qurilma %s, filial %s)",
            camera.camera_id,
            device.name,
            camera.branch_id or "biriktirilmagan",
        )
    else:
        # Agent konfiguratsiyasida nom yoki joy o'zgargan bo'lsa yangilaymiz;
        # filialga tegmaymiz — u administratorning qarori.
        if event.camera_name and camera.name != event.camera_name[:128]:
            camera.name = event.camera_name[:128]
        if event.location and camera.location != event.location[:128]:
            camera.location = event.location[:128]

    camera.last_seen_at = datetime.now(timezone.utc)
    camera.sightings_count = (camera.sightings_count or 0) + 1
    return camera


async def _handle_event(
    session: AsyncSession,
    device: VisionDevice,
    event: FaceEventRequest,
    thumbnail: bytes | None,
    image_bytes: bytes | None,
) -> FaceEventResponse:
    """Bitta epizodni qayta ishlaydi — issiq yo'lning butun mantiqi shu yerda."""
    now = datetime.now(timezone.utc)

    # Takroriy yetkazish: offline navbat qayta yuborgan bo'lishi mumkin.
    # Bu tekshiruv birinchi bo'lishi kerak, aks holda har qayta urinish
    # yangi ko'rinish yaratardi va panel bir odamni takror ko'rsatardi.
    existing = (
        await session.execute(
            select(FaceSighting).where(FaceSighting.track_uid == event.track_uid)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return FaceEventResponse(
            status="duplicate",
            sighting_id=existing.id,
            similarity=existing.similarity,
            message="Bu epizod allaqachon qayd etilgan",
        )

    camera = await _resolve_camera(session, device, event)
    if camera is not None and not camera.is_active:
        # O'chirilgan kamera: tokenni bekor qilmasdan bitta kamerani
        # to'xtatish yo'li. Agent buni "yaroqsiz" deb qabul qiladi va qayta
        # urinmaydi.
        return FaceEventResponse(
            status="invalid", message="Bu kamera o'chirilgan"
        )

    quality_score = float(event.quality.score) if event.quality else 0.0

    # -- vektorni olish ---------------------------------------------------
    samples: list = []
    template: gfs.Template | None = None
    recognition = event.recognition

    if recognition and recognition.model and recognition.model != gfs.MODEL_NAME:
        # Boshqa model vektorlari solishtirib bo'lmaydi — jimgina noto'g'ri
        # natija berishdan ko'ra ochiq rad etgan yaxshi.
        raise ValidationException(
            f"Vektor modeli mos emas: {recognition.model} (kutilgan {gfs.MODEL_NAME})",
            "MODEL_MISMATCH",
        )

    if recognition:
        samples = gfs.decode_wire_list(recognition.samples)
        direct = gfs.decode_wire_embedding(recognition.template)
        if direct is not None:
            template = gfs.Template(
                vector=direct,
                sample_count=max(1, int(recognition.sample_count or len(samples) or 1)),
                dropped=int(recognition.dropped or 0),
                cohesion=float(recognition.cohesion or 1.0),
            )
        elif samples:
            template = gfs.build_template(samples)

    if template is None and image_bytes:
        # Zaxira: agent eski versiya yoki vektor dvigateli o'chirilgan.
        if not gfs.server_engine_available():
            raise ValidationException(
                "Agent vektor yubormadi va serverda tanish dvigateli yo'q",
                "NO_EMBEDDING",
            )
        try:
            async with _embed_limiter:
                vector = await anyio.to_thread.run_sync(gfs.embed_image, image_bytes)
        except ValueError as exc:
            code = str(exc)
            return await _record_and_reply(
                session, device, camera, event, thumbnail, None, None,
                status="low_quality",
                message="Yuz topilmadi" if code == "NO_FACE" else "Rasm o'qilmadi",
                now=now,
            )
        template = gfs.Template(vector=vector, sample_count=1, dropped=0, cohesion=1.0)

    if template is None:
        raise ValidationException("Vektor ham, rasm ham yuborilmadi", "NO_EMBEDDING")

    # -- qidiruv ----------------------------------------------------------
    result = await gfs.identify(session, device.hotel_id, template.vector)

    return await _record_and_reply(
        session, device, camera, event, thumbnail, template, result,
        status=result.status, message=None, now=now,
        quality_score=quality_score,
    )


async def _record_and_reply(
    session: AsyncSession,
    device: VisionDevice,
    camera: VisionCamera | None,
    event: FaceEventRequest,
    thumbnail: bytes | None,
    template: gfs.Template | None,
    result: gfs.SearchResult | None,
    *,
    status: str,
    message: str | None,
    now: datetime,
    quality_score: float = 0.0,
) -> FaceEventResponse:
    """Ko'rinishni yozadi, kerak bo'lsa o'rganadi va javobni yig'adi."""
    guest: Guest | None = None
    learned = False

    if result is not None and result.guest_id is not None:
        guest = await session.get(Guest, result.guest_id)
        if guest is not None and (guest.is_deleted or guest.hotel_id != device.hotel_id):
            # Indeks eskirgan bo'lishi mumkin — mehmon o'chirilgan bo'lsa
            # moslikni bekor qilamiz va indeksni yangilashga majburlaymiz.
            gfs.invalidate_hotel(device.hotel_id)
            guest = None
            status = "unknown"

    sighting = FaceSighting(
        hotel_id=device.hotel_id,
        # Filial KAMERAdan olinadi, qurilmadan emas: bitta agent turli
        # filiallardagi kameralarni boqishi mumkin. Kamera hali biriktirilmagan
        # bo'lsa qurilmanikiga qaytamiz — bu eski o'rnatishlarni buzmaydi.
        branch_id=(camera.branch_id if camera is not None else None) or device.branch_id,
        camera_id=event.camera_id[:64],
        camera_name=(event.camera_name or None) and event.camera_name[:128],
        location=(event.location or None) and event.location[:128],
        device_id=(event.device_id or device.device_id or None),
        track_uid=event.track_uid[:64],
        capture_id=(event.capture_id or None) and event.capture_id[:64],
        status=status,
        guest_id=guest.id if guest is not None else None,
        similarity=result.score if result else 0.0,
        margin=result.margin if result else 0.0,
        quality_score=quality_score,
        sample_count=template.sample_count if template else 0,
        cohesion=template.cohesion if template else 0.0,
        # Tanilgan odamning vektorini qayta saqlamaymiz — u profilda bor.
        # Tanilmaganniki esa keyin biriktirish uchun kerak.
        embedding=(
            gfs.pack_embedding(template.vector)
            if template is not None and guest is None
            else None
        ),
        thumbnail=thumbnail,
        seen_at=event.timestamp or now,
        expires_at=now + timedelta(hours=SIGHTING_TTL_HOURS),
    )
    session.add(sighting)

    if guest is not None and result is not None and template is not None:
        if result.profile_id is not None:
            await session.execute(
                update(GuestFaceProfile)
                .where(GuestFaceProfile.id == result.profile_id)
                .values(
                    last_matched_at=now,
                    match_count=GuestFaceProfile.match_count + 1,
                )
            )
        if status == "recognized":
            learned = await gfs.learn_from_match(
                session,
                hotel_id=device.hotel_id,
                guest_id=guest.id,
                result=result,
                template=template,
                quality=quality_score,
                camera_id=event.camera_id,
            )

    device.events_received = (device.events_received or 0) + 1
    await session.flush()

    matched: MatchedGuest | None = None
    if guest is not None:
        matched = MatchedGuest(
            guest_id=guest.id,
            name=f"{guest.first_name} {guest.last_name}".strip(),
            phone=guest.phone,
            has_active_reservation=await _has_active_reservation(session, guest.id),
        )

    return FaceEventResponse(
        status=status if status in {"recognized", "uncertain", "unknown"} else "invalid",
        sighting_id=sighting.id,
        guest=matched,
        similarity=result.score if result else None,
        margin=result.margin if result else None,
        candidates=result.candidates if result else 0,
        learned=learned,
        message=message,
    )


async def _has_active_reservation(session: AsyncSession, guest_id: UUID) -> bool:
    row = (
        await session.execute(
            select(Reservation.id)
            .where(
                Reservation.guest_id == guest_id,
                Reservation.status.in_(("CONFIRMED", "CHECKED_IN", "PENDING")),
                Reservation.is_deleted.is_(False),
            )
            .limit(1)
        )
    ).scalar()
    return row is not None


@router.post("/events", response_model=FaceEventResponse)
async def submit_face_event(
    camera_id: str = Form(...),
    capture_id: Optional[str] = Form(default=None),
    timestamp: Optional[str] = Form(default=None),
    confidence: Optional[str] = Form(default=None),
    quality_score: Optional[str] = Form(default=None),
    device_id: Optional[str] = Form(default=None),
    metadata: Optional[str] = Form(default=None),
    image: Optional[UploadFile] = File(default=None),
    device: VisionDevice = Depends(require_vision_device),
    session: AsyncSession = Depends(get_db),
):
    """Agentning asosiy yo'li: bitta epizod (multipart, rasm bilan).

    Rasm ixtiyoriy va faqat panelda ko'rsatish uchun — tanish undan emas,
    ``metadata.recognition.template`` dagi vektordan bajariladi.
    """
    payload: dict = {}
    if metadata:
        try:
            parsed = json.loads(metadata)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError as exc:
            raise ValidationException(f"metadata JSON emas: {exc}", "BAD_METADATA")

    payload.setdefault("camera_id", camera_id)
    if capture_id:
        payload.setdefault("capture_id", capture_id)
    if timestamp:
        payload.setdefault("timestamp", timestamp)
    if device_id:
        payload["device_id"] = device_id
    if confidence and "confidence" not in payload:
        try:
            payload["confidence"] = float(confidence)
        except ValueError:
            pass
    if quality_score and not payload.get("quality"):
        try:
            payload["quality"] = {"score": float(quality_score)}
        except ValueError:
            pass
    if not payload.get("track_uid"):
        # Eski agent track_uid yubormaydi — capture_id ham noyob, undan
        # foydalanamiz, shunda takrorni aniqlash baribir ishlaydi.
        payload["track_uid"] = payload.get("capture_id") or capture_id

    try:
        event = FaceEventRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValidationException(f"Hodisa maydonlari xato: {exc}", "BAD_EVENT")

    thumbnail: bytes | None = None
    raw_image: bytes | None = None
    if image is not None:
        raw_image = await image.read()
        if len(raw_image) > MAX_IMAGE_BYTES:
            raise ValidationException("Rasm juda katta", "IMAGE_TOO_LARGE")
        thumbnail = raw_image if len(raw_image) <= MAX_THUMBNAIL_BYTES else None
        if thumbnail is None:
            logger.info(
                "Kamera %s dan %d baytlik rasm keldi — panel uchun saqlanmadi",
                event.camera_id,
                len(raw_image),
            )

    return await _handle_event(session, device, event, thumbnail, raw_image)


@router.post("/sightings/{sighting_id}/thumbnail")
async def attach_thumbnail(
    sighting_id: UUID,
    image: UploadFile = File(...),
    device: VisionDevice = Depends(require_vision_device),
    session: AsyncSession = Depends(get_db),
):
    """Attach the face crop to an episode the agent already reported.

    This is the second half of the agent's ``send_image: unknown_only`` mode:
    the vector goes first and the picture follows only when the server could
    not place the person — which is exactly when somebody has to look at it.
    A recognised regular then costs 512 bytes instead of 15 KB, every time
    they walk past a camera.

    Idempotent and forgiving: a repeat call simply overwrites, and a sighting
    that expired in between is a no-op rather than an error, because the agent
    treats this as best-effort and will not retry.
    """
    sighting = await session.get(FaceSighting, sighting_id)
    if sighting is None or sighting.hotel_id != device.hotel_id:
        raise NotFoundException("Ko'rinish topilmadi")

    payload = await image.read()
    if not payload:
        raise ValidationException("Bo'sh rasm", "BAD_IMAGE")
    if len(payload) > MAX_THUMBNAIL_BYTES:
        raise ValidationException(
            f"Rasm juda katta ({len(payload)} bayt, chegara {MAX_THUMBNAIL_BYTES})",
            "IMAGE_TOO_LARGE",
        )

    sighting.thumbnail = payload
    await session.flush()
    return {"stored": True, "bytes": len(payload)}


@router.post("/events/json", response_model=FaceEventResponse)
async def submit_face_event_json(
    event: FaceEventRequest = Body(...),
    device: VisionDevice = Depends(require_vision_device),
    session: AsyncSession = Depends(get_db),
):
    """Rasmsiz, faqat vektor rejimi — eng tejamli yo'l.

    Trafik ~700 bayt. Panelda yuz surati ko'rinmaydi, shuning uchun bu rejim
    tarmoq tor bo'lgan filiallar uchun; qabulxona paneli suratsiz ham ishlaydi
    (mehmon ismi va o'xshashlik bali ko'rsatiladi).
    """
    return await _handle_event(session, device, event, None, None)


# ===========================================================================
# Qabulxona paneli
# ===========================================================================


def _hotel_id(current_user: dict) -> UUID:
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


@router.get("/sightings", response_model=SightingListResponse)
async def list_sightings(
    minutes: int = Query(default=PANEL_WINDOW_MINUTES, ge=1, le=720),
    limit: int = Query(default=20, ge=1, le=100),
    include_acknowledged: bool = Query(default=False),
    only_matched: bool = Query(default=False),
    only_unmatched: bool = Query(
        default=False,
        description="Faqat tanilmaganlar — yangi mehmonga yuz biriktirish uchun",
    ),
    branch_id: Optional[UUID] = Query(
        default=None,
        description=(
            "Faqat shu filial kameralaridan kelgan suratlar. Yangi mehmon "
            "qo'shishda MAJBURIY: xodim boshqa filialning odamini tasodifan "
            "biriktirmasligi kerak."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.view")),
):
    """Panel shu endpointni qisqa oraliqda so'rab turadi.

    WebSocket ataylab ishlatilmadi: panel bir necha soniya kechikishga bardosh
    beradi, polling esa mavjud TanStack Query bilan bir qatorda ishlaydi va
    hech qanday yangi infratuzilma talab qilmaydi. So'rov indekslangan
    ``(hotel_id, seen_at)`` bo'yicha ketadi va og'ir rasm ustuni tanlanmaydi.
    """
    hotel_id = _hotel_id(current_user)
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    conditions = [FaceSighting.hotel_id == hotel_id, FaceSighting.seen_at >= since]
    if not include_acknowledged:
        conditions.append(FaceSighting.acknowledged_at.is_(None))
    if only_matched:
        conditions.append(FaceSighting.guest_id.is_not(None))
    if only_unmatched:
        conditions.append(FaceSighting.guest_id.is_(None))
    if branch_id is not None:
        # Filial mansubligi kameradan keladi. Filiali biriktirilmagan kamera
        # (branch_id NULL) hech qaysi filial ro'yxatiga tushmaydi — bu ataylab:
        # noaniq joydagi kamerani ko'rsatgandan ko'ra ko'rsatmagan yaxshi.
        conditions.append(FaceSighting.branch_id == branch_id)

    rows = (
        await session.execute(
            select(
                FaceSighting.id,
                FaceSighting.status,
                FaceSighting.camera_id,
                FaceSighting.camera_name,
                FaceSighting.location,
                FaceSighting.seen_at,
                FaceSighting.similarity,
                FaceSighting.margin,
                FaceSighting.quality_score,
                FaceSighting.guest_id,
                FaceSighting.branch_id,
                FaceSighting.acknowledged_at,
                FaceSighting.embedding.is_not(None).label("has_embedding"),
                FaceSighting.thumbnail.is_not(None).label("has_thumbnail"),
                Guest.first_name,
                Guest.last_name,
                Guest.phone,
            )
            .outerjoin(Guest, Guest.id == FaceSighting.guest_id)
            .where(*conditions)
            .order_by(FaceSighting.seen_at.desc())
            .limit(limit)
        )
    ).all()

    guest_ids = {row.guest_id for row in rows if row.guest_id}
    visits: dict[UUID, tuple[int, datetime | None]] = {}
    if guest_ids:
        stats = (
            await session.execute(
                select(
                    Reservation.guest_id,
                    func.count(Reservation.id),
                    func.max(Reservation.created_at),
                )
                .where(
                    Reservation.guest_id.in_(guest_ids),
                    Reservation.is_deleted.is_(False),
                )
                .group_by(Reservation.guest_id)
            )
        ).all()
        visits = {gid: (int(count), last) for gid, count, last in stats}

    items = []
    for row in rows:
        count, last = visits.get(row.guest_id, (0, None)) if row.guest_id else (0, None)
        name = None
        if row.first_name or row.last_name:
            name = f"{row.first_name or ''} {row.last_name or ''}".strip()
        items.append(
            SightingResponse(
                id=row.id,
                status=row.status,
                camera_id=row.camera_id,
                camera_name=row.camera_name,
                location=row.location,
                seen_at=row.seen_at,
                similarity=row.similarity,
                margin=row.margin,
                quality_score=row.quality_score,
                guest_id=row.guest_id,
                guest_name=name,
                guest_phone=row.phone,
                last_stay_at=last,
                visits=count,
                branch_id=row.branch_id,
                has_thumbnail=bool(row.has_thumbnail),
                # Biriktirish faqat vektori saqlangan, tanilmagan ko'rinish
                # uchun mantiqiy.
                can_enroll=bool(row.has_embedding) and row.guest_id is None,
                acknowledged=row.acknowledged_at is not None,
            )
        )

    # Same scope as the list itself: a badge that counted people standing in
    # another branch would send the receptionist looking for somebody who is
    # not there.
    pending_conditions = [
        FaceSighting.hotel_id == hotel_id,
        FaceSighting.seen_at >= since,
        FaceSighting.acknowledged_at.is_(None),
    ]
    if branch_id is not None:
        pending_conditions.append(FaceSighting.branch_id == branch_id)
    pending = (
        await session.execute(
            select(func.count(FaceSighting.id)).where(*pending_conditions)
        )
    ).scalar() or 0

    return SightingListResponse(items=items, unacknowledged=int(pending))


@router.get("/sightings/groups", response_model=SightingGroupListResponse)
async def list_sighting_groups(
    minutes: int = Query(default=PANEL_WINDOW_MINUTES, ge=1, le=720),
    limit: int = Query(default=24, ge=1, le=100),
    branch_id: Optional[UUID] = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.view")),
):
    """Tanilmagan ko'rinishlarni odamlar bo'yicha guruhlab qaytaradi.

    Bir odam kamera oldidan uch marta o'tsa uchta epizod yoziladi. Ularni
    alohida ko'rsatish ikki xato tug'diradi: xodim "qaysi biri?" deb
    o'ylaydi, va biriktirilmagan qolgan ikkitasi ro'yxatda abadiy qoladi.
    Guruhlangani esa aniqroq ham — biriktirishda uchala vektor birga
    o'rtachalanadi.

    Guruhlash har so'rovda qaytadan hisoblanadi va saqlanmaydi. Oyna kichik
    (odatda o'nlab ko'rinish), hisob esa arzon; saqlansa esa har yangi
    ko'rinish kelganda eskilarini qayta ko'rib chiqish kerak bo'lardi.
    """
    hotel_id = _hotel_id(current_user)
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    conditions = [
        FaceSighting.hotel_id == hotel_id,
        FaceSighting.seen_at >= since,
        # Faqat hali hech kimga tegishli bo'lmaganlar: biriktirilgan yuzni
        # ikkinchi marta biriktirish bitta odamni ikki mehmon qilib qo'yardi.
        FaceSighting.guest_id.is_(None),
        FaceSighting.embedding.is_not(None),
    ]
    if branch_id is not None:
        conditions.append(FaceSighting.branch_id == branch_id)

    rows = (
        await session.execute(
            select(
                FaceSighting.id,
                FaceSighting.embedding,
                FaceSighting.camera_id,
                FaceSighting.camera_name,
                FaceSighting.location,
                FaceSighting.branch_id,
                FaceSighting.seen_at,
                FaceSighting.quality_score,
                FaceSighting.thumbnail.is_not(None).label("has_thumbnail"),
            )
            .where(*conditions)
            # Guruhlash uchun hammasi kerak, shuning uchun oyna ichidagi
            # barchasi olinadi; `limit` guruhlarga qo'llanadi, qatorlarga emas.
            .order_by(FaceSighting.seen_at.desc())
            .limit(500)
        )
    ).all()

    vectors: list = []
    kept: list = []
    ungrouped = 0
    for row in rows:
        vector = gfs.unpack_embedding(row.embedding)
        if vector is None:
            ungrouped += 1
            continue
        vectors.append(vector)
        kept.append(row)

    if not vectors:
        return SightingGroupListResponse(items=[], ungrouped=ungrouped)

    # Eng sifatlisi birinchi: birinchi a'zo guruhning boshlang'ich markazi
    # bo'ladi, va sifatsiz kadrdan boshlangan guruh keyingilarini noto'g'ri
    # tortadi.
    order = sorted(range(len(kept)), key=lambda i: -float(kept[i].quality_score or 0))
    groups = gfs.group_embeddings(vectors, order=order)

    items: list[SightingGroupResponse] = []
    for members in groups:
        best = max(members, key=lambda i: float(kept[i].quality_score or 0))
        seen = [kept[i].seen_at for i in members]
        items.append(
            SightingGroupResponse(
                sighting_ids=[kept[i].id for i in members],
                best_sighting_id=kept[best].id,
                count=len(members),
                camera_id=kept[best].camera_id,
                camera_name=kept[best].camera_name,
                location=kept[best].location,
                branch_id=kept[best].branch_id,
                first_seen_at=min(seen),
                last_seen_at=max(seen),
                quality_score=float(kept[best].quality_score or 0),
                cohesion=gfs.group_cohesion(vectors, members),
                has_thumbnail=bool(kept[best].has_thumbnail),
            )
        )

    # Eng yaqinda ko'ringan odam tepada: qabulxonaga hozir kelgan mehmon
    # kerak, ikki soat oldin o'tgani emas.
    items.sort(key=lambda g: g.last_seen_at, reverse=True)
    return SightingGroupListResponse(items=items[:limit], ungrouped=ungrouped)


@router.get("/sightings/{sighting_id}/image")
async def sighting_image(
    sighting_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.view")),
):
    """Panelda ko'rsatiladigan yuz surati."""
    hotel_id = _hotel_id(current_user)
    row = (
        await session.execute(
            select(FaceSighting.thumbnail).where(
                FaceSighting.id == sighting_id,
                FaceSighting.hotel_id == hotel_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise NotFoundException("Surat topilmadi")
    return Response(
        content=bytes(row),
        media_type="image/jpeg",
        # Surat o'zgarmaydi va qisqa umr ko'radi — brauzer keshlasin.
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/sightings/{sighting_id}/ack")
async def acknowledge_sighting(
    sighting_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.view")),
):
    """Xodim ko'rinishni ko'rib chiqdi — paneldan olib tashlanadi."""
    hotel_id = _hotel_id(current_user)
    sighting = await session.get(FaceSighting, sighting_id)
    if sighting is None or sighting.hotel_id != hotel_id:
        raise NotFoundException("Ko'rinish topilmadi")
    if sighting.acknowledged_at is None:
        sighting.acknowledged_at = datetime.now(timezone.utc)
        sighting.acknowledged_by = current_user["id"]
        await session.flush()
    return {"acknowledged": True}


@router.post("/sightings/{sighting_id}/enroll", response_model=FaceProfileStatus)
async def enroll_sighting(
    sighting_id: UUID,
    payload: EnrollSightingRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.update")),
):
    """Tanilmagan ko'rinishni mehmonga biriktiradi.

    Bu odatiy oqim: mehmon birinchi marta keladi, qabulxona uni ro'yxatdan
    o'tkazadi va panelda turgan suratni bosib biriktiradi. Keyingi tashrifda
    u avtomatik tanaladi — mehmonni qayta suratga olish shart emas.
    """
    hotel_id = _hotel_id(current_user)
    sighting = await session.get(FaceSighting, sighting_id)
    if sighting is None or sighting.hotel_id != hotel_id:
        raise NotFoundException("Ko'rinish topilmadi")
    if not sighting.embedding:
        raise ValidationException(
            "Bu ko'rinishda saqlangan yuz vektori yo'q", "NO_EMBEDDING"
        )

    guest = await session.get(Guest, payload.guest_id)
    if guest is None or guest.is_deleted or guest.hotel_id != hotel_id:
        raise NotFoundException("Mehmon topilmadi")

    if not payload.consent:
        raise ValidationException(
            "Mehmonning biometrik ma'lumot saqlashga roziligi tasdiqlanmagan",
            "CONSENT_REQUIRED",
        )

    # Guruhning qolgan a'zolari. Bir odamning uch epizodi bo'lsa uchalasi
    # ham shu mehmonga yoziladi — aks holda qolgan ikkitasi "tanilmagan"
    # bo'lib ro'yxatda qolaverardi va xodim ularni qayta ko'rardi.
    extra_ids = [sid for sid in payload.sighting_ids if sid != sighting_id]
    siblings: list[FaceSighting] = []
    if extra_ids:
        siblings = list(
            (
                await session.execute(
                    select(FaceSighting).where(
                        FaceSighting.id.in_(extra_ids),
                        FaceSighting.hotel_id == hotel_id,
                        # Boshqa mehmonga tegishlisini tortib olmaymiz.
                        FaceSighting.guest_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    vectors = []
    for member in (sighting, *siblings):
        vector = gfs.unpack_embedding(member.embedding)
        if vector is not None:
            vectors.append(vector)
    if not vectors:
        raise ValidationException("Saqlangan vektor buzuq", "BAD_EMBEDDING")

    # Bir necha epizoddan yig'ilgan shablon bittasidan aniqroq. build_template
    # bir vaqtning o'zida himoya ham: guruhga tasodifan tushib qolgan boshqa
    # odamning vektori bu yerda chetlatiladi.
    template = gfs.build_template(vectors)
    if template is None:
        raise ValidationException("Shablon yasab bo'lmadi", "BAD_EMBEDDING")

    await gfs.enroll(
        session,
        hotel_id=hotel_id,
        guest_id=guest.id,
        template=template,
        quality=sighting.quality_score,
        source="manual",
        camera_id=sighting.camera_id,
        created_by=current_user["id"],
    )

    if guest.face_consent_at is None:
        guest.face_consent_at = datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)
    for member in (sighting, *siblings):
        member.guest_id = guest.id
        member.status = "recognized"
        if member.acknowledged_at is None:
            member.acknowledged_at = now
            member.acknowledged_by = current_user["id"]
        # Biriktirilgandan keyin vektor profilda — ko'rinishda nusxasi
        # saqlanib qolishining sababi yo'q.
        member.embedding = None
    await session.flush()

    logger.info(
        "Mehmon %s ga %d ta ko'rinishdan yuz biriktirildi (shablon: %d kadr, "
        "%d chetlatildi)",
        guest.id,
        len(vectors),
        template.sample_count,
        template.dropped,
    )
    return await _profile_status(session, guest)


@router.get("/guests/{guest_id}/face", response_model=FaceProfileStatus)
async def guest_face_status(
    guest_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.view")),
):
    """Mehmonning yuz profili holati."""
    hotel_id = _hotel_id(current_user)
    guest = await session.get(Guest, guest_id)
    if guest is None or guest.is_deleted or guest.hotel_id != hotel_id:
        raise NotFoundException("Mehmon topilmadi")
    return await _profile_status(session, guest)


@router.delete("/guests/{guest_id}/face", response_model=FaceProfileStatus)
async def delete_guest_face(
    guest_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.update")),
):
    """Mehmonning biometrik ma'lumotlarini butunlay o'chiradi.

    Rozilikni qaytarib olish huquqi — bu tugma bo'lishi shart, va u haqiqatan
    ham hamma narsani o'chirishi kerak: shablonlar ham, ko'rinishlardagi
    vektor va suratlar ham.
    """
    hotel_id = _hotel_id(current_user)
    guest = await session.get(Guest, guest_id)
    if guest is None or guest.is_deleted or guest.hotel_id != hotel_id:
        raise NotFoundException("Mehmon topilmadi")

    removed = await gfs.forget_guest(session, hotel_id=hotel_id, guest_id=guest_id)
    guest.face_consent_at = None
    await session.flush()
    logger.info(
        "Mehmon %s biometriyasi o'chirildi (%d shablon), xodim %s",
        guest_id,
        removed,
        current_user["id"],
    )
    return await _profile_status(session, guest)


async def _profile_status(session: AsyncSession, guest: Guest) -> FaceProfileStatus:
    row = (
        await session.execute(
            select(
                func.count(GuestFaceProfile.id),
                func.max(GuestFaceProfile.last_matched_at),
            ).where(GuestFaceProfile.guest_id == guest.id)
        )
    ).one()
    count = int(row[0] or 0)
    return FaceProfileStatus(
        guest_id=guest.id,
        enrolled=count > 0,
        profiles=count,
        consent_at=guest.face_consent_at,
        last_matched_at=row[1],
    )


# ===========================================================================
# Qurilmalarni boshqarish
# ===========================================================================


@router.get("/devices", response_model=list[VisionDeviceResponse])
async def list_devices(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("employee.manage")),
):
    hotel_id = _hotel_id(current_user)
    devices = (
        (
            await session.execute(
                select(VisionDevice)
                .where(VisionDevice.hotel_id == hotel_id)
                .order_by(VisionDevice.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [VisionDeviceResponse.model_validate(d) for d in devices]


@router.post("/devices", response_model=VisionDeviceCreatedResponse, status_code=201)
async def create_device(
    payload: VisionDeviceCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("employee.manage")),
):
    """Yangi kamera agenti uchun token yaratadi.

    Token javobda BIR MARTA ochiq ko'rsatiladi va bazada faqat xeshi qoladi.
    Yo'qolsa qayta ko'rsatib bo'lmaydi — yangisini yaratish kerak.
    """
    hotel_id = _hotel_id(current_user)
    token = secrets.token_urlsafe(32)
    device = VisionDevice(
        hotel_id=hotel_id,
        branch_id=payload.branch_id,
        name=payload.name,
        token_hash=_hash_token(token),
        token_hint=token[-4:],
        created_by=current_user["id"],
    )
    session.add(device)
    await session.flush()
    logger.info("Vision qurilmasi yaratildi: %s (mehmonxona %s)", device.name, hotel_id)
    return VisionDeviceCreatedResponse(
        **VisionDeviceResponse.model_validate(device).model_dump(), token=token
    )


@router.delete("/devices/{device_id}")
async def revoke_device(
    device_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("employee.manage")),
):
    """Qurilma tokenini bekor qiladi (o'chirmaydi — tarix qoladi)."""
    hotel_id = _hotel_id(current_user)
    device = await session.get(VisionDevice, device_id)
    if device is None or device.hotel_id != hotel_id:
        raise NotFoundException("Qurilma topilmadi")
    device.is_active = False
    await session.flush()
    return {"revoked": True}


# ===========================================================================
# Kameralar — qaysi kamera qaysi filialda
# ===========================================================================


@router.get("/cameras", response_model=list[VisionCameraResponse])
async def list_cameras(
    branch_id: Optional[UUID] = Query(default=None),
    unassigned_only: bool = Query(
        default=False, description="Faqat filialga biriktirilmagan kameralar"
    ),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.view")),
):
    """Mehmonxonaning barcha kameralari va ular qaysi filialda ekani.

    Kameralar birinchi hodisada o'z-o'zidan paydo bo'ladi, shuning uchun bu
    ro'yxat "nima ulangan" degan savolga ham javob beradi — administrator
    yangi kamerani ko'radi va filialga biriktiradi.
    """
    hotel_id = _hotel_id(current_user)
    from app.infrastructure.database.models.branch import Branch

    conditions = [VisionCamera.hotel_id == hotel_id]
    if branch_id is not None:
        conditions.append(VisionCamera.branch_id == branch_id)
    if unassigned_only:
        conditions.append(VisionCamera.branch_id.is_(None))

    rows = (
        await session.execute(
            select(VisionCamera, Branch.name, VisionDevice.name)
            .outerjoin(Branch, Branch.id == VisionCamera.branch_id)
            .outerjoin(VisionDevice, VisionDevice.id == VisionCamera.device_id)
            .where(*conditions)
            .order_by(VisionCamera.last_seen_at.desc().nullslast())
        )
    ).all()

    return [
        VisionCameraResponse(
            **{
                **VisionCameraResponse.model_validate(camera).model_dump(),
                "branch_name": branch_name,
                "device_name": device_name,
            }
        )
        for camera, branch_name, device_name in rows
    ]


@router.patch("/cameras/{camera_pk}", response_model=VisionCameraResponse)
async def update_camera(
    camera_pk: UUID,
    payload: VisionCameraUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("employee.manage")),
):
    """Kamerani filialga biriktiradi, nomlaydi yoki vaqtincha o'chiradi.

    Bu qabulxona uchun eng muhim sozlama: yangi mehmonga yuz biriktirishda
    xodim FAQAT o'z filialining kameralaridan kelgan suratlarni ko'radi, va
    surat qaysi filialga tegishli ekani aynan shu yerda hal bo'ladi.
    """
    hotel_id = _hotel_id(current_user)
    camera = await session.get(VisionCamera, camera_pk)
    if camera is None or camera.hotel_id != hotel_id:
        raise NotFoundException("Kamera topilmadi")

    # `branch_id: null` — "biriktirishni bekor qil", maydon umuman
    # yuborilmagani esa "tegma". Ikkalasi ham kerak, shuning uchun
    # yuborilgan maydonlar bo'yicha ishlaymiz.
    fields = payload.model_dump(exclude_unset=True)

    if "branch_id" in fields:
        new_branch = fields["branch_id"]
        if new_branch is not None:
            from app.infrastructure.database.models.branch import Branch

            branch = await session.get(Branch, new_branch)
            if branch is None or branch.hotel_id != hotel_id:
                raise NotFoundException("Filial topilmadi")
        camera.branch_id = new_branch
    if "name" in fields:
        camera.name = fields["name"]
    if "is_active" in fields:
        camera.is_active = bool(fields["is_active"])

    await session.flush()
    logger.info(
        "Kamera %s yangilandi: filial=%s faol=%s (xodim %s)",
        camera.camera_id,
        camera.branch_id,
        camera.is_active,
        current_user["id"],
    )
    return VisionCameraResponse.model_validate(camera)


@router.get("/stats")
async def vision_stats(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.view")),
):
    """Diagnostika: indeks holati, shablonlar va kameralar soni."""
    hotel_id = _hotel_id(current_user)
    profiles = (
        await session.execute(
            select(func.count(GuestFaceProfile.id)).where(
                GuestFaceProfile.hotel_id == hotel_id
            )
        )
    ).scalar() or 0
    guests_with_face = (
        await session.execute(
            select(func.count(func.distinct(GuestFaceProfile.guest_id))).where(
                GuestFaceProfile.hotel_id == hotel_id
            )
        )
    ).scalar() or 0
    devices = (
        await session.execute(
            select(func.count(VisionDevice.id)).where(
                VisionDevice.hotel_id == hotel_id, VisionDevice.is_active.is_(True)
            )
        )
    ).scalar() or 0
    return {
        "profiles": int(profiles),
        "guests_with_face": int(guests_with_face),
        "active_devices": int(devices),
        "model": gfs.MODEL_NAME,
        "match_threshold": gfs.MATCH_THRESHOLD,
        "match_margin": gfs.MATCH_MARGIN,
        "index": gfs.index_stats(hotel_id),
    }
