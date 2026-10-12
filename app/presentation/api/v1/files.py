from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.document_scan import DocumentScan
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.infrastructure.database.models.guest_feedback import GuestFeedback
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.problem import Problem
from app.infrastructure.storage.minio import upload_file, get_presigned_url, delete_file
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter()

#: Fayl egalari filialga bo'lingan bo'lsa — fayl ham faqat o'sha filialda
#: ko'rinadi. "task_report" fayllari ham vazifaga (task id) bog'lanadi.
_SCOPED_FILE_ENTITIES = {
    "task": HousekeepingTask,
    "task_report": HousekeepingTask,
    "problem": Problem,
    "expense": Expense,
    "document_scan": DocumentScan,
    "feedback": GuestFeedback,
}


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


async def _assert_entity_visible(
    session: AsyncSession,
    entity_type: str,
    entity_id: UUID,
    hotel_id: UUID | None,
    current_user: dict,
    *,
    allow_missing: bool = False,
) -> None:
    """Fayl bog'lanadigan obyekt so'rov filialida bormi.

    Filialga bo'lingan obyekt (vazifa, muammo, xarajat, hujjat skani,
    feedback) boshqa filialda bo'lsa — 404, borligi ham oshkor bo'lmaydi.
    Filial filtri `session.get` orqali avtomatik qo'llanadi. Global
    obyektlar (mehmon, xodim) va tizim ma'muri (SUPER_ADMIN) avvalgidek
    qoladi. `allow_missing` — o'chirish uchun: egasi allaqachon o'chgan
    bo'lsa orphan faylni tozalashga ruxsat.
    """
    if current_user.get("user_type") == "SUPER_ADMIN":
        return
    model = _SCOPED_FILE_ENTITIES.get((entity_type or "").strip().lower())
    if model is None:
        return
    row = await session.get(model, entity_id)
    if row is None:
        if allow_missing:
            return
        raise NotFoundException("Entity not found", "ENTITY_NOT_FOUND")
    row_hotel = getattr(row, "hotel_id", None)
    if hotel_id is not None and row_hotel is not None and str(row_hotel) != str(hotel_id):
        raise NotFoundException("Entity not found", "ENTITY_NOT_FOUND")


@router.post("/upload")
async def upload_file_endpoint(
    file: UploadFile = File(),
    entity_type: str = Form(),
    entity_id: UUID = Form(),
    category: str | None = Form(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("file.upload")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    # Fayl boshqa filial obyektiga biriktirilmasin (topilmasa — 404)
    await _assert_entity_visible(session, entity_type, entity_id, h_id, current_user)
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"

    bucket = "hotel-documents"
    object_path = f"{h_id}/{entity_type}/{entity_id}/{file.filename}"

    await upload_file(bucket, object_path, content, content_type)

    attachment = FileAttachment(
        hotel_id=h_id,
        entity_type=entity_type,
        entity_id=entity_id,
        file_name=file.filename,
        original_name=file.filename,
        mime_type=content_type,
        file_size=len(content),
        minio_bucket=bucket,
        minio_path=object_path,
        category=category,
        uploaded_by=current_user["id"],
    )
    session.add(attachment)
    await session.flush()

    return {
        "id": str(attachment.id),
        "file_name": attachment.file_name,
        "original_name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "entity_type": attachment.entity_type,
        "entity_id": str(attachment.entity_id),
        "category": attachment.category,
        "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
    }


@router.get("/by-entity")
async def list_entity_files(
    entity_type: str = Query(),
    entity_id: UUID | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Obyekt(lar)ga biriktirilgan fayllar ro'yxati yuklab olish havolalari
    bilan — masalan, xodim suratlari (entity_type="user", category="photo").
    entity_id berilmasa mehmonxonaning shu turdagi barcha fayllari qaytadi
    (eng yangilari birinchi) — ro'yxatlarda avatarlar uchun bitta so'rov kifoya.
    """
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    model = _SCOPED_FILE_ENTITIES.get((entity_type or "").strip().lower())
    if entity_id is not None:
        # Boshqa filial obyekti so'ralsa — bo'sh/noma'lum javob emas, 404
        await _assert_entity_visible(session, entity_type, entity_id, h_id, current_user)
    stmt = select(FileAttachment).where(
        FileAttachment.entity_type == entity_type,
        FileAttachment.is_deleted.is_(False),
    )
    if h_id is not None:
        stmt = stmt.where(FileAttachment.hotel_id == h_id)
    if entity_id is not None:
        stmt = stmt.where(FileAttachment.entity_id == entity_id)
    elif model is not None and current_user["user_type"] != "SUPER_ADMIN":
        # Egasi filialga bo'lingan tur: boshqa filial fayllari ro'yxatga
        # tushmasligi uchun egasiga JOIN (filial filtri avtomatik qo'shiladi)
        stmt = stmt.join(model, model.id == FileAttachment.entity_id)
    if category:
        stmt = stmt.where(FileAttachment.category == category)
    stmt = stmt.order_by(FileAttachment.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(a.id),
            "entity_id": str(a.entity_id),
            "category": a.category,
            "mime_type": a.mime_type,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "download_url": get_presigned_url(a.minio_bucket, a.minio_path),
        }
        for a in rows
    ]


@router.get("/{file_id}")
async def get_file(
    file_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    stmt = select(FileAttachment).where(
        FileAttachment.id == file_id,
        FileAttachment.is_deleted.is_(False),
    )
    if h_id is not None:
        stmt = stmt.where(FileAttachment.hotel_id == h_id)
    result = await session.execute(stmt)
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise NotFoundException("File not found", "FILE_NOT_FOUND")
    # Egasi boshqa filialda bo'lsa — fayl ham ko'rinmaydi
    await _assert_entity_visible(
        session, attachment.entity_type, attachment.entity_id, h_id, current_user
    )

    return {
        "id": str(attachment.id),
        "file_name": attachment.file_name,
        "original_name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "entity_type": attachment.entity_type,
        "entity_id": str(attachment.entity_id),
        "category": attachment.category,
        "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
    }


@router.get("/{file_id}/download")
async def download_file(
    file_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    stmt = select(FileAttachment).where(
        FileAttachment.id == file_id,
        FileAttachment.is_deleted.is_(False),
    )
    if h_id is not None:
        stmt = stmt.where(FileAttachment.hotel_id == h_id)
    result = await session.execute(stmt)
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise NotFoundException("File not found", "FILE_NOT_FOUND")
    # Egasi boshqa filialda bo'lsa — yuklab olish ham mumkin emas
    await _assert_entity_visible(
        session, attachment.entity_type, attachment.entity_id, h_id, current_user
    )

    url = get_presigned_url(attachment.minio_bucket, attachment.minio_path)
    return {"download_url": url}


@router.delete("/{file_id}", response_model=MessageResponse)
async def delete_file_endpoint(
    file_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("file.delete")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    stmt = select(FileAttachment).where(FileAttachment.id == file_id)
    if h_id is not None:
        stmt = stmt.where(FileAttachment.hotel_id == h_id)
    result = await session.execute(stmt)
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise NotFoundException("File not found", "FILE_NOT_FOUND")
    # Egasi o'chgan bo'lsa — orphan faylni tozalashga ruxsat (allow_missing)
    await _assert_entity_visible(
        session,
        attachment.entity_type,
        attachment.entity_id,
        h_id,
        current_user,
        allow_missing=True,
    )

    attachment.is_deleted = True
    await session.flush()
    return {"message": "File deleted"}
