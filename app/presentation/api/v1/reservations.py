from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.branch import Branch
from app.application.services.reservation_service import (
    ReservationService,
    RESERVATION_EDIT_KEY,
    DEFAULT_EDIT_WINDOW_MINUTES,
    resolve_edit_window_minutes,
)
from app.application.dto.reservation import (
    ReservationCreateRequest,
    ReservationUpdateRequest,
    ReservationCancelRequest,
    ReservationExtendRequest,
    ReservationServiceAddRequest,
    ReservationResponse,
    ReservationDetailResponse,
    MoveRoomRequest,
    SettlePaymentRequest,
)
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission
from app.presentation.api.v1._deps import require_active_hotel, require_open_shift

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


# DIQQAT: literal marshrutlar /{reservation_id} dan OLDIN turishi shart
@router.get("/edit-window-settings")
async def get_edit_window_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bron tahriri (xona almashtirish) vaqt oynasi — daqiqalarda, 0 = cheklovsiz."""
    from app.infrastructure.database.models.hotel import Hotel

    h_id = _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    return {
        "window_minutes": resolve_edit_window_minutes(hotel.settings if hotel else None),
        "default_minutes": DEFAULT_EDIT_WINDOW_MINUTES,
    }


@router.put("/edit-window-settings")
async def save_edit_window_settings(
    window_minutes: int = Query(ge=0, le=1440),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Vaqt oynasini saqlash — faqat ADMIN/SUPER_ADMIN."""
    from app.infrastructure.database.models.hotel import Hotel

    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException("Faqat administrator o'zgartira oladi")
    h_id = _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    if not hotel:
        raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
    # JSONB YANGI dict bilan almashtiriladi — o'zgarish sezilishi uchun
    new_settings = dict(hotel.settings or {})
    new_settings[RESERVATION_EDIT_KEY] = {"window_minutes": int(window_minutes)}
    hotel.settings = new_settings
    await session.flush()
    return {
        "window_minutes": int(window_minutes),
        "default_minutes": DEFAULT_EDIT_WINDOW_MINUTES,
    }


@router.get("/cancellation-settings")
async def get_cancellation_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bekor qilishda ushlab qolinadigan foiz."""
    from app.infrastructure.database.models.hotel import Hotel
    from app.application.services.reservation_service import (
        DEFAULT_CANCELLATION_FEE_PERCENT,
        resolve_cancellation_fee_percent,
    )

    h_id = (
        current_user.get("hotel_id")
        if current_user["user_type"] == "SUPER_ADMIN"
        else _get_hotel_id(current_user)
    )
    hotel = await session.get(Hotel, h_id) if h_id else None
    return {
        "fee_percent": resolve_cancellation_fee_percent(hotel.settings if hotel else None),
        "default_percent": DEFAULT_CANCELLATION_FEE_PERCENT,
    }


@router.put("/cancellation-settings")
async def save_cancellation_settings(
    fee_percent: float = Query(ge=0, le=100),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Foizni saqlash — faqat ADMIN/SUPER_ADMIN."""
    from app.infrastructure.database.models.hotel import Hotel
    from app.application.services.reservation_service import (
        CANCELLATION_POLICY_KEY,
        DEFAULT_CANCELLATION_FEE_PERCENT,
    )

    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException("Faqat administrator o'zgartira oladi")
    h_id = _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    if not hotel:
        raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
    # JSONB YANGI dict bilan almashtiriladi — o'zgarish sezilishi uchun
    new_settings = dict(hotel.settings or {})
    new_settings[CANCELLATION_POLICY_KEY] = {"fee_percent": float(fee_percent)}
    hotel.settings = new_settings
    await session.flush()
    return {
        "fee_percent": float(fee_percent),
        "default_percent": DEFAULT_CANCELLATION_FEE_PERCENT,
    }


@router.get("/", response_model=list[ReservationResponse])
async def list_reservations(
    status: str | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = ReservationService(session)
    return await service.get_reservations(
        h_id,
        skip=skip,
        limit=limit,
        status=status,
        branch_id=branch_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("/", response_model=ReservationResponse)
async def create_reservation(
    data: ReservationCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.create")),
    _shift: None = Depends(require_open_shift),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            branch = await session.get(Branch, data.branch_id)
            if not branch:
                raise NotFoundException("Branch not found", "BRANCH_NOT_FOUND")
            h_id = branch.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.create_reservation(
        h_id, data.branch_id, data.model_dump(), current_user["id"]
    )


@router.get("/calendar")
async def get_calendar(
    view: str = Query(default="daily", pattern=r"^(daily|weekly|monthly)$"),
    date_param: date = Query(alias="date"),
    branch_id: UUID | None = Query(default=None),
    room_type_id: UUID | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = ReservationService(session)
    return await service.get_calendar(
        h_id, view, date_param, branch_id, room_type_id, skip, limit
    )


@router.get("/availability")
async def check_availability(
    check_in: date = Query(),
    check_out: date = Query(),
    branch_id: UUID | None = Query(default=None),
    room_type_id: UUID | None = Query(default=None),
    adults: int = Query(default=1, ge=1),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = ReservationService(session)
    return await service.check_availability(
        h_id, check_in, check_out, branch_id, room_type_id
    )


@router.get("/{reservation_id}", response_model=ReservationResponse)
async def get_reservation(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = ReservationService(session)
    return await service.get_reservation(reservation_id, h_id)


@router.put("/{reservation_id}", response_model=ReservationResponse)
async def update_reservation(
    reservation_id: UUID = Path(),
    data: ReservationUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.update")),
):
    # Bron tafsilotlarini TAHRIRLASH — faqat menejer (shift.force_close) yoki
    # administrator. Qabulxona xodimi tahrirlay olmaydi (xonani almashtirish
    # esa /move-room orqali vaqt oynasi ichida mumkin).
    if current_user["user_type"] == "EMPLOYEE" and "shift.force_close" not in (
        current_user.get("permissions") or []
    ):
        raise ForbiddenException(
            "Bron tafsilotlarini tahrirlash faqat menejer yoki administrator uchun",
            "FORBIDDEN",
        )
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.update_reservation(
        reservation_id, h_id, data.model_dump(exclude_none=True)
    )


@router.post("/{reservation_id}/move-room", response_model=ReservationResponse)
async def move_room(
    reservation_id: UUID = Path(),
    data: MoveRoomRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.update")),
):
    """Bronni boshqa xonaga ko'chirish (vaqt oynasi va bandlik tekshiruvi bilan)."""
    h_id = _get_hotel_id(current_user)
    service = ReservationService(session)
    return await service.move_room(h_id, reservation_id, data.new_room_id, current_user)


def _extend_hotel_id(current_user: dict, hotel_id: UUID | None) -> UUID:
    """Muddatni o'zgartirish faqat ADMINISTRATOR qo'lida.

    Menejer ham, qabulxona xodimi ham o'zgartira olmaydi: bu pul
    masalasiga tegadi — cho'zish qo'shimcha haqsiz beriladi, qisqartirish
    esa mijoz to'lagan muddatni kamaytiradi. Kim qaror qilishini
    mehmonxona egasi hal qiladi.
    """
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException(
            "Bronni cho'zish faqat administrator uchun", "ADMIN_ONLY"
        )
    h_id = hotel_id if current_user["user_type"] == "SUPER_ADMIN" else None
    h_id = h_id or current_user.get("hotel_id")
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


@router.get("/{reservation_id}/extension-limit")
async def extension_limit(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Muddat chegaralari: `limit` — qachongacha cho'zish mumkin (`null` —
    cheklovsiz), `floor` — qachongacha qisqartirish mumkin."""
    h_id = _extend_hotel_id(current_user, hotel_id)
    return await ReservationService(session).extension_limit(reservation_id, h_id)


@router.post("/{reservation_id}/extend", response_model=ReservationResponse)
async def extend_reservation(
    reservation_id: UUID = Path(),
    data: ReservationExtendRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bron tugash vaqtini suradi: cho'zadi yoki qisqartiradi.

    Yuqori chegara — keyingi bron, quyi chegara — bronning boshlanishi.
    Ikkala yo'nalishda ham pul o'zgarmaydi.
    """
    h_id = _extend_hotel_id(current_user, hotel_id)
    return await ReservationService(session).extend_reservation(
        reservation_id, h_id, data.check_out
    )


@router.post("/{reservation_id}/settle-payment", response_model=ReservationResponse)
async def settle_payment(
    reservation_id: UUID = Path(),
    data: SettlePaymentRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("finance.payment.create")),
    _shift: None = Depends(require_open_shift),
):
    """Bron balansi bo'yicha hisob-kitob: qo'shimcha to'lov (PAY, qisman ham
    mumkin) yoki ortiqcha to'langanni qaytarish (REFUND) — asosan xona
    almashtirishdan keyingi narx farqini yopish uchun."""
    h_id = _get_hotel_id(current_user)
    service = ReservationService(session)
    return await service.settle_payment(
        h_id,
        reservation_id,
        data.amount,
        data.payment_method,
        data.direction,
        UUID(str(current_user["id"])),
    )


@router.post("/{reservation_id}/check-in", response_model=ReservationResponse)
async def check_in(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.check_in(reservation_id, h_id, current_user["id"])


@router.post("/{reservation_id}/check-out")
async def check_out(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.check_out(reservation_id, h_id, current_user["id"])


@router.post("/{reservation_id}/request-checkout", response_model=ReservationResponse)
async def request_checkout(
    reservation_id: UUID = Path(),
    self_assign: bool = Query(default=False),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Chiqish jarayonini boshlash: farroshga tozalash vazifasi boradi,
    farrosh yakunlagach bron avtomatik CHECKED_OUT bo'ladi.

    Resepsiya/menejer (reservation.update) ham, farroshning o'zi
    (housekeeping.task.update) ham chaqira oladi. self_assign=true bo'lsa
    vazifa chaqiruvchining o'ziga biriktiriladi (farrosh tugmasi).
    """
    perms = current_user.get("permissions", [])
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN") and not (
        "reservation.update" in perms or "housekeeping.task.update" in perms
    ):
        raise ForbiddenException("Permission required to request checkout")

    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.request_checkout(
        reservation_id,
        h_id,
        current_user["id"],
        assign_to=current_user["id"] if self_assign else None,
    )


@router.post("/{reservation_id}/cancel", response_model=ReservationResponse)
async def cancel_reservation(
    reservation_id: UUID = Path(),
    data: ReservationCancelRequest | None = None,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.cancel")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.cancel_reservation(
        reservation_id,
        h_id,
        current_user["id"],
        data.reason if data else None,
        refund_amount=data.refund_amount if data else None,
        refund_method=data.refund_method if data else None,
    )


@router.get("/{reservation_id}/cancellation-quote")
async def get_cancellation_quote(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.cancel")),
):
    """Bekor qilinsa qancha qaytariladi — tasdiqlashdan oldin ko'rsatish uchun."""
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return await ReservationService(session).cancellation_quote(reservation_id, h_id)


@router.post("/{reservation_id}/no-show", response_model=ReservationResponse)
async def mark_no_show(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.mark_no_show(reservation_id, h_id, current_user["id"])


@router.get("/{reservation_id}/services")
async def get_reservation_services(
    reservation_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = ReservationService(session)
    return await service.get_reservation_services(reservation_id, h_id)


@router.post("/{reservation_id}/services")
async def add_service(
    reservation_id: UUID = Path(),
    data: ReservationServiceAddRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    return await service.add_service(reservation_id, h_id, data.model_dump())


@router.delete("/{reservation_id}/services/{service_id}", response_model=MessageResponse)
async def remove_service(
    reservation_id: UUID = Path(),
    service_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("reservation.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            reservation = await session.get(Reservation, reservation_id)
            if not reservation:
                raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
            h_id = reservation.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReservationService(session)
    await service.remove_service(service_id, reservation_id, h_id)
    return {"message": "Service removed from reservation"}
