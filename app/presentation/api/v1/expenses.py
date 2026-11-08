"""Xarajatlar (chiqimlar) endpointlari — Xarajatlar sahifasi uchun.

Ko'rish va kiritish BARCHA tizimga kirgan xodimlar uchun ochiq (har bir
xarajatda kim kiritgani created_by sifatida majburiy saqlanadi va ro'yxatda
ism-familiya bilan qaytariladi — javobgarlik saqlanadi). O'chirish esa faqat
expense.delete ruxsati bilan (ADMIN/SUPER_ADMIN avtomatik).

Toifalar (`/expenses/categories`): mehmonxonaning tanlash ro'yxati — nom,
belgi, rang, tartib. O'qish hammaga; qo'shish, qayta nomlash, arxivlash
ADMIN/SUPER_ADMIN va expense.delete ruxsati borlarga
(expense_category_service).
"""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.expense_category_service import (
    ExpenseCategoryService,
    can_manage_categories,
)
from app.application.services.expense_service import ExpenseService, expense_dict
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.user import User
from app.presentation.api.v1._deps import require_active_hotel, require_open_shift
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


PaymentMethodCode = Literal[
    # Kanonik to'rtlik + eski kodlar (eski klientlar uchun)
    "CASH", "CARD", "ONLINE", "BANK_TRANSFER",
    "CREDIT_CARD", "DEBIT_CARD", "MOBILE_PAYMENT", "TRANSFER",
]


class ExpensePartRequest(BaseModel):
    """Qisman (aralash) to'lovning bitta bo'lagi."""

    payment_method: PaymentMethodCode
    amount: float = Field(..., gt=0)


class ExpenseCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    amount: float = Field(..., gt=0)
    category: str | None = Field(default=None, max_length=50)
    payment_method: PaymentMethodCode = "CASH"
    #: Qisman to'lov: summa bir necha to'lov turiga bo'linadi (yig'indisi
    #: `amount` ga teng, har tur bir marta). Berilsa `payment_method`
    #: e'tiborga olinmaydi; har bo'lak alohida qator bo'lib yoziladi
    #: (expense_service).
    payments: list[ExpensePartRequest] | None = Field(default=None, max_length=4)
    expense_date: date | None = None
    notes: str | None = None
    branch_id: UUID | None = None


class ExpenseCategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    #: app/shared/expense_categories.ICONS dan biri (bo'sh — "tag")
    icon: str | None = Field(default=None, max_length=30)
    #: app/shared/expense_categories.COLORS dan biri (bo'sh — "gray")
    color: str | None = Field(default=None, max_length=20)
    sort_order: int | None = None


class ExpenseCategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    icon: str | None = Field(default=None, max_length=30)
    color: str | None = Field(default=None, max_length=20)
    sort_order: int | None = None
    is_active: bool | None = None


class ExpenseCategoryReorderRequest(BaseModel):
    ids: list[UUID] = Field(default_factory=list, max_length=200)


def _scope_hotel_id(current_user: dict, hotel_id: UUID | None) -> UUID:
    """Toifalar uchun mehmonxona: SUPER_ADMIN `?hotel_id=` bilan, qolganlar
    o'z mehmonxonasi."""
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


_expense_dict = expense_dict


# ----------------------------------------------------------- toifalar --


@router.get("/categories")
async def list_expense_categories(
    include_archived: bool = Query(default=False),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Toifalar ro'yxati — xarajat kirita oladigan har bir xodimga.

    Yangi mehmonxonada birinchi ochilishda standart toifalar yoziladi.
    `can_manage` — shu foydalanuvchi ro'yxatni o'zgartira oladimi (mijoz
    tugmalarni shunga qarab ko'rsatadi; server baribir qayta tekshiradi)."""
    h_id = _scope_hotel_id(current_user, hotel_id)
    items = await ExpenseCategoryService(session).list_categories(h_id, include_archived)
    return {"items": items, "can_manage": can_manage_categories(current_user)}


@router.post("/categories")
async def create_expense_category(
    data: ExpenseCategoryCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _scope_hotel_id(current_user, hotel_id)
    return await ExpenseCategoryService(session).create(h_id, data.model_dump(), current_user)


@router.put("/categories/reorder")
async def reorder_expense_categories(
    data: ExpenseCategoryReorderRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _scope_hotel_id(current_user, hotel_id)
    return await ExpenseCategoryService(session).reorder(h_id, data.ids, current_user)


@router.put("/categories/{category_id}")
async def update_expense_category(
    data: ExpenseCategoryUpdateRequest,
    category_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Nom o'zgarsa shu mehmonxonaning xarajatlaridagi toifa nomi ham
    o'zgaradi — javobdagi `renamed_expenses` nechta qator ekanini aytadi."""
    h_id = _scope_hotel_id(current_user, hotel_id)
    return await ExpenseCategoryService(session).update(
        h_id, category_id, data.model_dump(exclude_unset=True), current_user
    )


@router.delete("/categories/{category_id}")
async def delete_expense_category(
    category_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ishlatilmagan toifa o'chadi; xarajati bor toifa ARXIVGA tushadi
    (`archived: true`) — eski xarajatlar o'z toifasida qoladi."""
    h_id = _scope_hotel_id(current_user, hotel_id)
    return await ExpenseCategoryService(session).delete(h_id, category_id, current_user)


# ---------------------------------------------------------- xarajatlar --


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

    return await ExpenseService(session).create(h_id, data.model_dump(), current_user)


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

    # Qisman to'lov bo'lagi o'chirilsa BUTUN xarajat (barcha bo'laklari) o'chadi
    deleted = await ExpenseService(session).delete(h_id, expense_id)
    return {"message": "Expense deleted", "deleted": deleted}
