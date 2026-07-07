from uuid import UUID

from fastapi import APIRouter, Depends, Query, UploadFile, File, Form, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.problems_service import ProblemsService
from app.application.dto.mobile import ProblemCreateRequest, ProblemResponse
from app.infrastructure.storage.minio import upload_file
from app.infrastructure.database.models.file_attachment import FileAttachment
from app.presentation.middleware.auth import get_current_user

router = APIRouter(tags=["Mobile Problems"])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


@router.post("", response_model=ProblemResponse)
async def create_problem(
    category: str = Form(),
    description: str = Form(),
    photos: list[UploadFile] = File(default=[]),
    task_id: str | None = Form(default=None),
    room_number: str | None = Form(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")

    tid = UUID(task_id) if task_id else None
    service = ProblemsService(session)
    problem = await service.create_problem(
        hotel_id=h_id,
        reported_by=current_user["id"],
        category=category,
        description=description,
        task_id=tid,
        room_number=room_number,
    )

    for photo in photos:
        if photo.filename:
            content = await photo.read()
            content_type = photo.content_type or "application/octet-stream"
            bucket = "hotel-documents"
            object_path = f"{h_id}/problem/{problem.id}/{photo.filename}"

            await upload_file(bucket, object_path, content, content_type)

            attachment = FileAttachment(
                hotel_id=h_id,
                entity_type="problem",
                entity_id=problem.id,
                file_name=photo.filename,
                original_name=photo.filename,
                mime_type=content_type,
                file_size=len(content),
                minio_bucket=bucket,
                minio_path=object_path,
                category="problem_photo",
                uploaded_by=current_user["id"],
            )
            session.add(attachment)

    await session.flush()

    return {
        "success": True,
        "message": "Muammo qabul qilindi",
        "report_id": str(problem.id),
    }
