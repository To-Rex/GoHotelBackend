from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.infrastructure.storage.minio import upload_file, get_presigned_url, delete_file
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter()


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


@router.post("/upload")
async def upload_file_endpoint(
    file: UploadFile = File(),
    entity_type: str = Form(),
    entity_id: UUID = Form(),
    category: str | None = Form(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("files.upload")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
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

    url = get_presigned_url(attachment.minio_bucket, attachment.minio_path)
    return {"download_url": url}


@router.delete("/{file_id}", response_model=MessageResponse)
async def delete_file_endpoint(
    file_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("files.delete")),
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

    attachment.is_deleted = True
    await session.flush()
    return {"message": "File deleted"}
