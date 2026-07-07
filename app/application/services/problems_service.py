from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.infrastructure.database.models.problem import Problem


class ProblemsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_problem(
        self,
        hotel_id: UUID,
        reported_by: UUID,
        category: str,
        description: str,
        task_id: UUID | None = None,
        room_number: str | None = None,
        branch_id: UUID | None = None,
        room_id: UUID | None = None,
    ) -> Problem:
        problem = Problem(
            hotel_id=hotel_id,
            branch_id=branch_id,
            room_id=room_id,
            task_id=task_id,
            category=category,
            description=description,
            reported_by=reported_by,
            room_number=room_number,
        )
        self.session.add(problem)
        await self.session.flush()
        await self.session.refresh(problem)
        return problem

    async def get_problems(
        self,
        hotel_id: UUID,
        skip: int = 0,
        limit: int = 50,
        status: str | None = None,
    ) -> list[Problem]:
        stmt = select(Problem).where(Problem.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(Problem.status == status)
        stmt = stmt.order_by(Problem.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_problem(self, problem_id: UUID, hotel_id: UUID) -> Problem:
        stmt = select(Problem).where(
            Problem.id == problem_id,
            Problem.hotel_id == hotel_id,
        )
        result = await self.session.execute(stmt)
        problem = result.scalar_one_or_none()
        if not problem:
            raise NotFoundException("Problem not found", "PROBLEM_NOT_FOUND")
        return problem

    async def update_problem_status(self, problem_id: UUID, hotel_id: UUID, status: str) -> Problem:
        problem = await self.get_problem(problem_id, hotel_id)
        problem.status = status
        await self.session.flush()
        await self.session.refresh(problem)
        return problem
