from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.reporting_service import ReportingService
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


@router.get("/")
async def list_saved_reports(
    report_type: str | None = Query(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = ReportingService(session)
    reports = await service.get_saved_reports(h_id, report_type=report_type)
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "report_type": r.report_type,
            "parameters": r.parameters,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]


@router.post("/generate")
async def generate_report(
    data: dict,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("report.generate")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = ReportingService(session)
    report_type = data.get("report_type", "OCCUPANCY")
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    branch_id = data.get("branch_id")

    result = None
    if report_type == "OCCUPANCY":
        result = await service.get_occupancy_report(
            h_id, start_date, end_date, branch_id
        )
    elif report_type == "REVENUE":
        result = await service.get_revenue_report(
            h_id, start_date, end_date, branch_id
        )

    report_data = result or {}
    report = await service.save_report(
        h_id,
        name=data.get("name", f"{report_type} Report"),
        report_type=report_type,
        parameters={"start_date": str(start_date), "end_date": str(end_date)},
        user_id=current_user["id"],
        result_data=report_data,
    )

    return {
        "id": str(report.id),
        "name": report.name,
        "report_type": report.report_type,
        "data": report_data,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
    }


@router.get("/{report_id}")
async def get_report(
    report_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = ReportingService(session)
    reports = await service.get_saved_reports(h_id)
    for r in reports:
        if r.id == report_id:
            return {
                "id": str(r.id),
                "name": r.name,
                "report_type": r.report_type,
                "parameters": r.parameters,
                "result_data": r.result_data,
                "generated_at": r.generated_at.isoformat() if r.generated_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
    from app.core.exceptions import NotFoundException
    raise NotFoundException("Report not found", "REPORT_NOT_FOUND")
