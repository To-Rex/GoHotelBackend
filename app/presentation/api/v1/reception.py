"""Qabulxona mobil endpointlari.

Resepsiya xodimi telefondan ishlaydi va unga qisqa, tayyor javob kerak.
Shuning uchun bu yerdagi ro'yxat boyitilgan holda qaytadi — mobil ilova
mehmon bilan xonani alohida so'rab o'tirmaydi.

Ruxsat: bron ko'rish huquqi bo'lgan xodim (`reservation.read`) yoki
administrator. Farrosh bu bo'limni ko'rmaydi.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.incoming_call_service import (
    DEFAULT_WINDOW_MINUTES,
    IncomingCallService,
)
from app.application.services.document_ocr import intake
from app.application.services.document_scan_service import (
    DEFAULT_WINDOW_MINUTES as SCAN_WINDOW_MINUTES,
    DocumentScanService,
)
from app.application.services.reception_service import ReceptionService
from app.presentation.api.v1._deps import require_active_hotel
from app.presentation.middleware.auth import get_current_user

router = APIRouter(dependencies=[Depends(require_active_hotel)])

#: Qabulxona bo'limini ko'rish uchun yetarli ruxsatlar. Bittasi bo'lsa
#: kifoya — mehmonxonalarda ruxsat to'plamlari har xil nomlanadi.
RECEPTION_CODES = (
    "reservation.read",
    "reservation.create",
    "reservation.update",
)


def _hotel_id(current_user: dict) -> UUID:
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


def _require_reception(current_user: dict) -> UUID:
    """Qabulxona bo'limi bron bilan ishlaydigan xodim uchun.

    Farrosh yoki usta bu ma'lumotni ko'rmaydi: unda mehmon ismi va
    telefon raqami bor, ya'ni bu shaxsiy ma'lumot.
    """
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        codes = current_user.get("permissions") or []
        if not any(code in codes for code in RECEPTION_CODES):
            raise ForbiddenException(
                "Bu bo'lim qabulxona xodimi uchun", "RECEPTION_ONLY"
            )
    return _hotel_id(current_user)


def _local_today() -> date:
    """Mehmonxonaning mahalliy kuni.

    Server UTC da ishlaydi — tungi soatlarda `date.today()` mahalliy
    kundan bir kun orqada qolib ketardi.
    """
    return (
        datetime.now(timezone.utc)
        + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
    ).date()


@router.get("/bookings")
async def list_bookings(
    day: date | None = Query(default=None, alias="date"),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    include_cancelled: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Tanlangan kunga tegishli bronlar: kelayotgan, turgan va chiqayotgan.

    `date` berilmasa bugungi kun olinadi. Har qatorda `kind` bor —
    `arrival` / `inhouse` / `departure`.
    """
    hotel_id = _require_reception(current_user)
    # Qabulxona ilovasi ochildi — tez orada hujjat skanerlanishi mumkin.
    # Modellar hozirdan fonda yuklansa, birinchi skan kutib turmaydi.
    intake.start_warm_up()
    return await ReceptionService(session).bookings(
        hotel_id,
        day or _local_today(),
        status=status,
        search=search,
        include_cancelled=include_cancelled,
        limit=limit,
    )


# ------------------------------------------- kiruvchi qo'ng'iroqlar --


class IncomingCallRequest(BaseModel):
    """Qurilma xabar bergan kiruvchi qo'ng'iroq."""

    phone: str = Field(min_length=3, max_length=32)
    #: Qaysi qurilma xabar berdi — bir nechta telefon bo'lsa ajratish uchun
    device_id: str | None = Field(default=None, max_length=128)


@router.post("/calls")
async def report_incoming_call(
    data: IncomingCallRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Kiruvchi qo'ng'iroqni qayd etadi va topilgan mehmonni qaytaradi.

    Qurilma qo'ng'iroq kelishi bilan chaqiradi. Javobdagi `matched`
    mehmon topilganini bildiradi; topilmagan qo'ng'iroq ham yoziladi —
    yangi mijoz bo'lishi mumkin va raqami bron ochishda asqotadi.
    """
    hotel_id = _require_reception(current_user)
    return await IncomingCallService(session).record(
        hotel_id,
        data.phone,
        reported_by=current_user.get("id"),
        device_id=data.device_id,
        today=_local_today(),
    )


@router.get("/calls")
async def list_incoming_calls(
    minutes: int = Query(default=DEFAULT_WINDOW_MINUTES, ge=1, le=1440),
    include_acknowledged: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Oxirgi qo'ng'iroqlar — veb ekranidagi menyu shuni o'qiydi."""
    hotel_id = _require_reception(current_user)
    return await IncomingCallService(session).recent(
        hotel_id,
        minutes=minutes,
        include_acknowledged=include_acknowledged,
        limit=limit,
    )


@router.post("/calls/{call_id}/ack")
async def acknowledge_call(
    call_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Qo'ng'iroqni ko'rib chiqilgan deb belgilaydi — menyudan chiqadi."""
    hotel_id = _require_reception(current_user)
    return await IncomingCallService(session).acknowledge(
        call_id, hotel_id, current_user["id"]
    )


# ------------------------------------------- hujjat skaneri (telefon) --


@router.post("/scans")
async def submit_document_scan(
    document_type: Literal["ID_CARD", "PASSPORT"] = Form(default="PASSPORT"),
    front: UploadFile | None = File(default=None),
    back: UploadFile | None = File(default=None),
    device_id: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Telefonda olingan hujjat rasmini o'qiydi va qabulxonaga uzatadi.

    Telefonda OCR ishlamaydi — rasm shu yerga keladi, server uni o'qiydi.
    Natija saqlanadi va veb ekrani uni ko'rib yangi bandlov oynasini o'zi
    ochadi: mehmon bazada bo'lsa tanlangan holda, bo'lmasa maydonlari
    to'ldirilgan holda.

    ID karta uchun IKKALA tomonni bitta so'rovda yuborish kerak: faqat
    shundagina old tomondagi bosma ma'lumot orqadagi MRZ bilan
    solishtiriladi. Passport uchun bitta sahifa yetarli.

    Rasm saqlanmaydi — javob qaytgach yo'qoladi.
    """
    hotel_id = _require_reception(current_user)
    intake.require_server_ocr()

    images: dict[str, bytes] = {}
    front_bytes = await intake.read_image(front, "Old tomon")
    back_bytes = await intake.read_image(back, "Orqa tomon")
    if front_bytes:
        images["passport" if document_type == "PASSPORT" else "front"] = front_bytes
    if back_bytes:
        images["back"] = back_bytes

    document = await intake.run_scan(images, document_type)
    return await DocumentScanService(session).record(
        hotel_id,
        document,
        scanned_by=current_user.get("id"),
        device_id=device_id or current_user.get("device_id"),
    )


@router.get("/scans")
async def list_document_scans(
    minutes: int = Query(default=SCAN_WINDOW_MINUTES, ge=1, le=1440),
    include_acknowledged: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Oxirgi skanerlar — veb ekranidagi kuzatuvchi shuni o'qiydi."""
    hotel_id = _require_reception(current_user)
    return await DocumentScanService(session).recent(
        hotel_id,
        minutes=minutes,
        include_acknowledged=include_acknowledged,
        limit=limit,
    )


@router.post("/scans/{scan_id}/ack")
async def acknowledge_document_scan(
    scan_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Skanerni ko'rib chiqilgan deb belgilaydi — ro'yxatdan chiqadi."""
    hotel_id = _require_reception(current_user)
    return await DocumentScanService(session).acknowledge(
        scan_id, hotel_id, current_user.get("id")
    )
