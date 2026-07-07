from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.mobile_tasks_service import MobileTasksService
from app.application.services.notification_service import NotificationService
from app.application.dto.mobile import (
    MobileTaskResponse,
    ProgressUpdateRequest,
    ReportSubmitResponse,
)
from app.infrastructure.storage.minio import upload_file, get_presigned_url, download_file
from app.core.exceptions import NotFoundException
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.presentation.middleware.auth import get_current_user

router = APIRouter(tags=["Mobile Tasks"])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


def _resolve_hotel_id(current_user: dict, query_hotel_id: UUID | None) -> UUID:
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = query_hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


@router.get("", response_model=list[MobileTaskResponse])
async def list_tasks(
    status: str | None = Query(default=None),
    date: str | None = Query(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)
    service = MobileTasksService(session)
    return await service.get_tasks(h_id, current_user["id"], status=status, date=date)


@router.get("/{task_id}", response_model=MobileTaskResponse)
async def get_task(
    task_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)
    service = MobileTasksService(session)
    return await service.get_task_by_id(task_id, h_id)


@router.put("/{task_id}/start", response_model=MobileTaskResponse)
async def start_task(
    task_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)
    service = MobileTasksService(session)
    return await service.start_task(task_id, h_id, current_user["id"])


@router.put("/{task_id}/progress", response_model=MobileTaskResponse)
async def update_progress(
    task_id: UUID = Path(),
    data: ProgressUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)
    service = MobileTasksService(session)
    return await service.update_progress(task_id, h_id, data.progress)


@router.put("/{task_id}/checklist/{item_id}/toggle", response_model=MobileTaskResponse)
async def toggle_checklist(
    task_id: UUID = Path(),
    item_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)
    service = MobileTasksService(session)
    return await service.toggle_checklist_item(task_id, item_id)


@router.post("/{task_id}/report", response_model=ReportSubmitResponse)
async def submit_report(
    task_id: UUID = Path(),
    photos: list[UploadFile] = File(),
    comment: str | None = Form(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)
    service = MobileTasksService(session)

    task = await service.get_task_by_id(task_id, h_id)

    for photo in photos:
        content = await photo.read()
        content_type = photo.content_type or "application/octet-stream"
        bucket = "hotel-documents"
        object_path = f"{h_id}/task_report/{task_id}/{photo.filename}"

        await upload_file(bucket, object_path, content, content_type)

        attachment = FileAttachment(
            hotel_id=h_id,
            entity_type="task_report",
            entity_id=task_id,
            file_name=photo.filename,
            original_name=photo.filename,
            mime_type=content_type,
            file_size=len(content),
            minio_bucket=bucket,
            minio_path=object_path,
            category="photo_report",
            uploaded_by=current_user["id"],
        )
        session.add(attachment)

    await session.flush()

    if comment:
        notif_service = NotificationService(session)
        await notif_service.notify_broadcast(
            h_id,
            "Foto hisobot",
            f"Task {task_id} uchun izoh: {comment}",
            entity_type="task",
            entity_id=task_id,
        )

    return {
        "success": True,
        "message": "Foto hisobot qabul qilindi",
        "task": task,
    }


@router.get("/{task_id}/photos")
async def get_task_photos(
    task_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)

    stmt = (
        sa_select(FileAttachment)
        .where(
            FileAttachment.entity_type == "task_report",
            FileAttachment.entity_id == task_id,
            FileAttachment.is_deleted == False,
        )
        .order_by(FileAttachment.created_at.desc())
    )
    if h_id:
        stmt = stmt.where(FileAttachment.hotel_id == h_id)

    result = await session.execute(stmt)
    attachments = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "file_name": a.file_name,
            "mime_type": a.mime_type,
            "file_size": a.file_size,
            "uploaded_by": str(a.uploaded_by),
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "download_url": get_presigned_url(a.minio_bucket, a.minio_path),
        }
        for a in attachments
    ]


@router.get("/{task_id}/photos/{photo_id}/view")
async def view_task_photo(
    task_id: UUID = Path(),
    photo_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    stmt = sa_select(FileAttachment).where(
        FileAttachment.id == photo_id,
        FileAttachment.entity_type == "task_report",
        FileAttachment.entity_id == task_id,
        FileAttachment.is_deleted == False,
    )

    result = await session.execute(stmt)
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise NotFoundException("Photo not found", "PHOTO_NOT_FOUND")

    content = await download_file(attachment.minio_bucket, attachment.minio_path)
    return Response(content=content, media_type=attachment.mime_type)
