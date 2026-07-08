from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.branch_service import BranchService
from app.application.services.room_service import RoomService
from app.application.dto.branch import BranchCreateRequest, BranchUpdateRequest, BranchResponse
from app.application.dto.room import FloorResponse
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


@router.get("/", response_model=list[BranchResponse])
async def list_branches(
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
    service = BranchService(session)
    return await service.get_branches(h_id, skip=skip, limit=limit)


@router.post("/", response_model=BranchResponse)
async def create_branch(
    data: BranchCreateRequest,
    hotel_id: UUID | None = None,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("branch.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id and not data.model_dump().get("hotel_id"):
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id or data.model_dump().get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = BranchService(session)
    return await service.create_branch(h_id, data.model_dump())


@router.get("/{branch_id}", response_model=BranchResponse)
async def get_branch(
    branch_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = BranchService(session)
    return await service.get_branch(branch_id, h_id)


@router.put("/{branch_id}", response_model=BranchResponse)
async def update_branch(
    branch_id: UUID = Path(),
    data: BranchUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("branch.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = BranchService(session)
    return await service.update_branch(branch_id, h_id, data.model_dump(exclude_none=True))


@router.get("/{branch_id}/floors", response_model=list[FloorResponse])
async def get_branch_floors(
    branch_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = RoomService(session)
    return await service.get_floors(h_id, branch_id=branch_id)
