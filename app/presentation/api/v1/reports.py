from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.personal_report_service import PersonalReportService
from app.application.services.report_access import (
    resolve_report_user,
    staff_options,
    user_brief,
)
from app.application.services.reporting_service import ReportingService
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.presentation.api.v1._deps import require_active_hotel
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


@router.get("/my-summary")
async def my_summary(
    date_from: date = Query(...),
    date_to: date = Query(...),
    user_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Xodimning tanlangan kunlardagi shaxsiy hisoboti.

    `user_id` siz — so'rovchining o'zi, ruxsat talab qilinmaydi. `user_id`
    bilan boshqa xodimning hisoboti — faqat administrator va menejer
    (`finance.view` / `shift.force_close` / `report.view`), xodim shu
    mehmonxonadan bo'lishi kerak (report_access). Javobda `user` — kimning
    hisoboti, `is_self` — so'rovchining o'ziniki.

    Pul har doim to'lovning o'zidan olinadi (kim qabul qilgan), bronlar esa
    mualliflik ko'rsatkichi sifatida alohida turadi. Sabab uchun
    `personal_report_service` modulining izohiga qarang.
    """
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    # Oqilona chegara: hisobot sahifasi eng ko'pi bilan bir yilni so'raydi
    if (date_to - date_from).days > 366:
        raise ForbiddenException("Oraliq bir yildan oshmasligi kerak", "RANGE_TOO_WIDE")

    h_id = _get_hotel_id(current_user)
    target_id, target = await resolve_report_user(session, current_user, h_id, user_id)
    service = PersonalReportService(session)
    result = await service.summary(h_id, target_id, date_from, date_to)
    me = current_user["id"]
    me = me if isinstance(me, UUID) else UUID(str(me))
    result["user"] = user_brief(target, me) if target is not None else {"id": str(target_id)}
    result["is_self"] = target_id == me
    return result


@router.get("/staff")
async def report_staff(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Hisobotini ko'rish mumkin bo'lgan xodimlar — administrator va
    menejer uchun (boshqalarga 403 `STAFF_REPORT_FORBIDDEN`). So'rovchi
    birinchi, keyin faollar, keyin qolganlar; ism bo'yicha."""
    return await staff_options(session, current_user, _get_hotel_id(current_user))


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
