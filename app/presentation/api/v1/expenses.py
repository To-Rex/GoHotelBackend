"""Xarajatlar (chiqimlar) endpointlari — Xarajatlar sahifasi uchun.

Ko'rish va kiritish BARCHA tizimga kirgan xodimlar uchun ochiq (har bir
xarajatda kim kiritgani created_by sifatida majburiy saqlanadi va ro'yxatda
ism-familiya bilan qaytariladi — javobgarlik saqlanadi). O'chirish esa faqat
expense.delete ruxsati bilan (ADMIN/SUPER_ADMIN avtomatik).
"""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.user import User
from app.presentation.middleware.auth import get_current_user, require_permission
from app.presentation.api.v1._deps import require_active_hotel, require_open_shift

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


class ExpenseCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    amount: float = Field(..., gt=0)
    category: str | None = Field(default=None, max_length=50)
    payment_method: Literal[
        "CASH", "CREDIT_CARD", "DEBIT_CARD", "BANK_TRANSFER", "MOBILE_PAYMENT", "ONLINE"
    ] = "CASH"
    expense_date: date | None = None
    notes: str | None = None
    branch_id: UUID | None = None


def _expense_dict(e: Expense, creator: User | None) -> dict:
    return {
        "id": str(e.id),
        "hotel_id": str(e.hotel_id),
        "branch_id": str(e.branch_id) if e.branch_id else None,
        "title": e.title,
        "category": e.category,
        "amount": float(e.amount),
        "payment_method": e.payment_method,
        "expense_date": str(e.expense_date),
        "notes": e.notes,
        "created_by": str(e.created_by),
        "created_by_name": (
            f"{creator.first_name} {creator.last_name}".strip() if creator else None
        ),
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/")
async def list_expenses(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)

    stmt = select(Expense, User).join(User, User.id == Expense.created_by)
    if h_id is not None:
        stmt = stmt.where(Expense.hotel_id == h_id)
    if date_from:
        stmt = stmt.where(Expense.expense_date >= date_from)
    if date_to:
        stmt = stmt.where(Expense.expense_date <= date_to)
    stmt = (
        stmt.order_by(Expense.expense_date.desc(), Expense.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [_expense_dict(e, u) for e, u in result.all()]


@router.post("/")
async def create_expense(
    data: ExpenseCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _shift: None = Depends(require_open_shift),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
        if not h_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")

    expense = Expense(
        hotel_id=h_id,
        branch_id=data.branch_id or current_user.get("branch_id"),
        title=data.title.strip(),
        category=(data.category or "").strip() or None,
        amount=data.amount,
        payment_method=data.payment_method,
        expense_date=data.expense_date or date.today(),
        notes=data.notes,
        created_by=current_user["id"],
    )
    session.add(expense)
    await session.flush()

    creator = await session.get(User, current_user["id"])
    return _expense_dict(expense, creator)


@router.delete("/{expense_id}")
async def delete_expense(
    expense_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("expense.delete")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)

    expense = await session.get(Expense, expense_id)
    if not expense or (h_id is not None and expense.hotel_id != h_id):
        raise NotFoundException("Expense not found", "EXPENSE_NOT_FOUND")

    await session.delete(expense)
    await session.flush()
    return {"message": "Expense deleted"}
