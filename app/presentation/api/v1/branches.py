from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.application.services.branch_service import BranchService
from app.application.services import sms_service
from app.infrastructure.database.models.branch import Branch
from pydantic import BaseModel, Field
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


# --- Filial SMS sozlamalari (Xabarchi) ---------------------------------
#
# Har filialga alohida API kalit: kalit bazada shifrlangan saqlanadi,
# javobda faqat niqoblangan ko'rinishi qaytadi. Boshqarish "branch.update"
# ruxsati bilan — filialni tahrirlay oladigan xodim kalitni ham boshqaradi.


class SmsKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)


class SmsTestRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=20)


async def _sms_branch(
    session: AsyncSession, branch_id: UUID, current_user: dict
) -> Branch:
    branch = await session.get(Branch, branch_id)
    if not branch or getattr(branch, "deleted_at", None):
        raise NotFoundException("Branch not found", "BRANCH_NOT_FOUND")
    if current_user["user_type"] != "SUPER_ADMIN":
        hotel_id = current_user.get("hotel_id")
        if not hotel_id or str(branch.hotel_id) != str(hotel_id):
            raise ForbiddenException("Branch belongs to another hotel")
    return branch


@router.get("/{branch_id}/sms")
async def get_branch_sms(
    branch_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("branch.update")),
):
    branch = await _sms_branch(session, branch_id, current_user)
    key = sms_service.decrypt_key(branch.sms_api_key) if branch.sms_api_key else None
    return {
        "configured": bool(key),
        "key_hint": sms_service.mask_key(key) if key else None,
    }


@router.put("/{branch_id}/sms")
async def save_branch_sms(
    data: SmsKeyRequest,
    branch_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("branch.update")),
):
    branch = await _sms_branch(session, branch_id, current_user)
    key = data.api_key.strip()
    if not key:
        raise ValidationException("API kalit bo'sh bo'lishi mumkin emas", "EMPTY_KEY")
    branch.sms_api_key = sms_service.encrypt_key(key)
    await session.flush()
    return {"configured": True, "key_hint": sms_service.mask_key(key)}


@router.delete("/{branch_id}/sms")
async def delete_branch_sms(
    branch_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("branch.update")),
):
    branch = await _sms_branch(session, branch_id, current_user)
    branch.sms_api_key = None
    await session.flush()
    return {"configured": False, "key_hint": None}


@router.post("/{branch_id}/sms/test")
async def test_branch_sms(
    data: SmsTestRequest,
    branch_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("branch.update")),
):
    """Saqlangan kalit bilan sinov SMS'i — natija DARHOL qaytadi (fonda
    emas): xodim kalit ishlayotganini shu yerning o'zida ko'radi."""
    branch = await _sms_branch(session, branch_id, current_user)
    key = sms_service.decrypt_key(branch.sms_api_key) if branch.sms_api_key else None
    if not key:
        raise ValidationException(
            "Avval API kalitni saqlang", "SMS_KEY_NOT_SET"
        )
    phone = sms_service.normalize_phone(data.phone)
    if not phone:
        raise ValidationException(
            "Telefon raqami noto'g'ri — +998 XX XXX XX XX ko'rinishida kiriting",
            "BAD_PHONE",
        )
    try:
        await sms_service.send_sms(
            key,
            phone,
            f"{branch.name}: SMS sozlamasi tekshiruvi — ulanish ishlayapti.",
        )
    except Exception as exc:  # noqa: BLE001 — sababi xodimga ko'rsatiladi
        raise ValidationException(f"SMS yuborilmadi: {exc}", "SMS_SEND_FAILED")
    return {"ok": True, "phone": phone}
