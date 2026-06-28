import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.report import Report


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        hotel_id: UUID,
        name: str,
        report_type: str,
        parameters: dict,
        generated_by: UUID | None = None,
    ) -> Report:
        report = Report(
            hotel_id=hotel_id,
            name=name,
            report_type=report_type,
            parameters=parameters,
            generated_by=generated_by,
        )
        self.session.add(report)
        await self.session.flush()
        return report

    async def get_by_hotel(
        self,
        hotel_id: UUID | None,
        report_type: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Report]:
        stmt = select(Report)
        if hotel_id is not None:
            stmt = stmt.where(Report.hotel_id == hotel_id)
        if report_type:
            stmt = stmt.where(Report.report_type == report_type)
        stmt = stmt.order_by(Report.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def save_result(
        self, report_id: UUID, result_data: dict
    ) -> Report | None:
        stmt = select(Report).where(Report.id == report_id)
        result = await self.session.execute(stmt)
        report = result.scalar_one_or_none()
        if report:
            report.result_data = result_data
            report.generated_at = datetime.datetime.now(datetime.UTC)
            await self.session.flush()
        return report
