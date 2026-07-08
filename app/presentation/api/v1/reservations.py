from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.branch import Branch
from app.application.services.reservation_service import ReservationService
from app.application.dto.reservation import (
    ReservationCreateRequest,
    ReservationUpdateRequest,
    ReservationCancelRequest,
    ReservationServiceAddRequest,
    ReservationResponse,
    ReservationDetailResponse,
)
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission
from app.presentation.api.v1._deps import require_active_hotel

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


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
    reason = data.reason if data else None
    return await service.cancel_reservation(reservation_id, h_id, current_user["id"], reason)


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
