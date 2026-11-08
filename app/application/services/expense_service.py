"""Xarajat yozish va o'chirish — shu jumladan QISMAN (aralash) to'lov.

Bitta xarajat bir necha to'lov turi bilan to'lanishi mumkin: 100 000 so'm
kommunal to'lovning 60 000 i naqd, 40 000 i online. Bu holda har bo'lak
ALOHIDA `expenses` qatori bo'lib yoziladi (o'z summasi va to'lov turi
bilan) va bo'laklar `split_group` kaliti bilan bog'lanadi.

Nega alohida qatorlar: smena kassasi, moliya yig'indisi, shaxsiy hisobot
va mobil ilova xarajatni "bitta qator — bitta to'lov turi" deb o'qiydi.
Bo'laklar qator bo'lsa, kassadan faqat naqd qismi chiqadi, usullar
kesimida har usul o'z ulushini oladi — hech bir hisobotga tegilmaydi.
Ro'yxatlar esa bo'laklarni bitta xarajat qilib ko'rsatadi (`split_group`
bo'yicha), soni hisobotlarda bitta deb sanaladi.

Bo'laklar yig'indisi xarajat summasiga teng bo'lishi shart; bitta to'lov
turi bir marta; bo'sh (0) bo'laklar tashlanadi; bittagina bo'lak qolsa —
oddiy xarajat.
"""
from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.expense_category_service import ExpenseCategoryService
from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.user import User
from app.shared.utils import hotel_today

#: Bo'laklar soni — to'lov turlari soni bilan bir xil (har turi bir marta)
MAX_PARTS = 4
#: Tiyin aniqligi — float yig'indidagi mayda farq xato emas
TOLERANCE = 0.005


def normalize_parts(total: float, parts: list[dict] | None) -> list[tuple[str, float]]:
    """Bo'laklarni tekshiradi va (to'lov turi, summa) ro'yxatini qaytaradi.

    Bo'sh ro'yxat — bo'lak yo'q (oddiy xarajat). Bitta bo'lak summaga teng
    bo'lsa ham qaytariladi: chaqiruvchi uni oddiy xarajat deb yozadi.
    """
    cleaned: list[tuple[str, float]] = []
    seen: set[str] = set()
    for part in parts or []:
        amount = round(float(part.get("amount") or 0), 2)
        if amount <= 0:
            continue
        method = str(part.get("payment_method") or "").strip().upper()
        if not method:
            raise ValidationException("Bo'lakning to'lov turi ko'rsatilmagan", "PART_METHOD_REQUIRED")
        if method in seen:
            raise ValidationException(
                "Bitta to'lov turi ikki marta ko'rsatilgan — ularni bitta bo'lakka qo'shing",
                "PARTS_DUPLICATE_METHOD",
            )
        seen.add(method)
        cleaned.append((method, amount))
    if not cleaned:
        return []
    if len(cleaned) > MAX_PARTS:
        raise ValidationException(f"Ko'pi bilan {MAX_PARTS} ta bo'lak", "PARTS_TOO_MANY")
    parts_sum = round(sum(a for _m, a in cleaned), 2)
    if abs(parts_sum - round(float(total), 2)) > TOLERANCE:
        raise ValidationException(
            f"Bo'laklar yig'indisi ({parts_sum:.0f}) xarajat summasiga ({float(total):.0f}) teng emas",
            "PARTS_SUM_MISMATCH",
        )
    return cleaned


def expense_dict(e: Expense, creator: User | None) -> dict:
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
        # Qisman to'lov bo'laklarini bog'laydigan kalit (oddiy xarajatda bo'sh)
        "split_group": str(e.split_group) if e.split_group else None,
    }


class ExpenseService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, hotel_id: UUID, data: dict, current_user: dict) -> dict:
        """Xarajat yozadi. `payments` da ikki va undan ko'p bo'lak bo'lsa —
        har bo'lak alohida qator, bitta `split_group` bilan.

        Javob: birinchi qator (eski mijozlar `id` ni shundan oladi) va
        bo'lingan bo'lsa `parts` — barcha qatorlar."""
        total = round(float(data["amount"]), 2)
        if total <= 0:
            raise ValidationException("Summa 0 dan katta bo'lishi kerak", "AMOUNT_INVALID")
        parts = normalize_parts(total, data.get("payments"))
        if len(parts) == 1:
            # Bitta bo'lak — oddiy xarajat o'sha to'lov turi bilan
            data = {**data, "payment_method": parts[0][0]}
            parts = []

        category = await ExpenseCategoryService(self.session).resolve_name(
            hotel_id, data.get("category")
        )
        common = dict(
            hotel_id=hotel_id,
            branch_id=data.get("branch_id") or current_user.get("branch_id"),
            title=str(data["title"]).strip(),
            category=category,
            # Sana berilmasa MAHALLIY bugun (server UTC da — tunda date.today()
            # kechagi sanani berardi)
            expense_date=data.get("expense_date") or hotel_today(),
            notes=data.get("notes"),
            created_by=current_user["id"],
        )

        rows: list[Expense] = []
        if parts:
            group = uuid.uuid4()
            for method, amount in parts:
                rows.append(
                    Expense(**common, amount=amount, payment_method=method, split_group=group)
                )
        else:
            rows.append(
                Expense(**common, amount=total, payment_method=data.get("payment_method") or "CASH")
            )
        self.session.add_all(rows)
        await self.session.flush()

        creator = await self.session.get(User, current_user["id"])
        payload = expense_dict(rows[0], creator)
        if parts:
            payload["parts"] = [expense_dict(r, creator) for r in rows]
        return payload

    async def delete(self, hotel_id: UUID | None, expense_id: UUID) -> int:
        """Xarajatni o'chiradi. Qisman to'lov bo'lagi bo'lsa — BUTUN xarajat
        (barcha bo'laklari): yarim xarajat qolib ketmasligi kerak.
        Nechta qator o'chganini qaytaradi."""
        expense = await self.session.get(Expense, expense_id)
        if not expense or (hotel_id is not None and expense.hotel_id != hotel_id):
            raise NotFoundException("Expense not found", "EXPENSE_NOT_FOUND")
        if expense.split_group is None:
            await self.session.delete(expense)
            await self.session.flush()
            return 1
        rows = (
            await self.session.execute(
                select(Expense).where(
                    Expense.hotel_id == expense.hotel_id,
                    Expense.split_group == expense.split_group,
                )
            )
        ).scalars().all()
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()
        return len(rows)


__all__ = ["ExpenseService", "expense_dict", "normalize_parts", "MAX_PARTS", "delete"]
