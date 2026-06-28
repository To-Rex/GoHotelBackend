from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MAX_PAGE_SIZE
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.audit_service import AuditService
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter()


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


@router.get("/")
async def query_audit_logs(
    entity_type: str | None = Query(default=None),
    entity_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("audit_logs.view")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    skip = (page - 1) * page_size
    service = AuditService(session)
    logs = await service.get_logs(
        h_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        from_date=from_date,
        to_date=to_date,
        skip=skip,
        limit=page_size,
    )
    return [
        {
            "id": str(log.id),
            "hotel_id": str(log.hotel_id),
            "user_id": str(log.user_id) if log.user_id else None,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": str(log.entity_id),
            "old_values": log.old_values,
            "new_values": log.new_values,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
