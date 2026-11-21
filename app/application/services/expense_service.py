"""Xarajat yozish, tahrirlash va o'chirish — shu jumladan QISMAN (aralash) to'lov.

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

TAHRIRLASH (`ExpenseService.update`) — faqat administrator, super admin va
mehmonxona tanlagan sozlovchi (u mehmonxonada ADMIN bo'lib ishlaydi,
configurator_access). Nom, summa, toifa, to'lov turi (yoki bo'laklari),
sana va izoh o'zgaradi. Xarajat QACHON va KIM tomonidan kiritilgani
(`created_at`, `created_by`) o'zgarmaydi: xarajat o'z smenasida, o'z
xodimining hisobotida qoladi — bugungi smenaga o'tib ketmaydi. Har tahrir
`edit_history` ga (kim, qachon, qaysi vazifada, nimadan nimaga, izoh) va
audit jurnaliga yoziladi.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.audit_service import AuditService
from app.application.services.configurator_access import actual_user_type
from app.application.services.expense_category_service import ExpenseCategoryService
from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.user import User
from app.shared.utils import hotel_today

#: Bo'laklar soni — to'lov turlari soni bilan bir xil (har turi bir marta)
MAX_PARTS = 4
#: Tiyin aniqligi — float yig'indidagi mayda farq xato emas
TOLERANCE = 0.005

logger = logging.getLogger(__name__)

#: Xarajatni tahrirlay oladigan turlar (so'rovdagi `user_type`). Mehmonxona
#: tanlagan sozlovchi shu yerda "ADMIN" bo'lib keladi (effective_user_type).
EDIT_USER_TYPES = ("ADMIN", "SUPER_ADMIN")
EDIT_FORBIDDEN_CODE = "EXPENSE_EDIT_FORBIDDEN"
#: Bitta xarajat tarixida ko'pi bilan shuncha oxirgi tahrir saqlanadi
#: (audit jurnalida hammasi qoladi)
MAX_HISTORY = 50
#: Tarixda solishtiriladigan maydonlar (ekrandagi tartibda)
EDIT_FIELDS = ("amount", "payment", "category", "title", "expense_date", "notes")


def can_edit_expenses(current_user: dict | None) -> bool:
    """Xarajatni tahrirlay oladimi: administrator, super admin, sozlovchi."""
    return bool(current_user) and current_user.get("user_type") in EDIT_USER_TYPES


def _clean_text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _layout(parts: list[tuple[str, float]]) -> list[dict]:
    """To'lov tarkibi — katta summadan kichikka (solishtirish va tarix uchun)."""
    ordered = sorted(parts, key=lambda p: (-p[1], p[0]))
    return [{"payment_method": m, "amount": round(float(a), 2)} for m, a in ordered]


def _snapshot(
    *, title: str, category: str | None, parts: list[tuple[str, float]],
    expense_date, notes: str | None,
) -> dict:
    return {
        "title": title,
        "amount": round(sum(a for _m, a in parts), 2),
        "category": category,
        "payment": _layout(parts),
        "expense_date": str(expense_date),
        "notes": notes,
    }


def _rows_snapshot(rows: list[Expense]) -> dict:
    main = rows[0]
    return _snapshot(
        title=main.title,
        category=main.category,
        parts=[((r.payment_method or "").strip().upper(), float(r.amount)) for r in rows],
        expense_date=main.expense_date,
        notes=main.notes,
    )


def diff_snapshots(before: dict, after: dict) -> dict:
    """O'zgargan maydonlar: {maydon: {"old": ..., "new": ...}}."""
    changes: dict = {}
    for field in EDIT_FIELDS:
        old, new = before.get(field), after.get(field)
        if field == "amount":
            same = abs(float(old or 0) - float(new or 0)) <= TOLERANCE
        elif field == "payment" and len(old or []) == 1 and len(new or []) == 1:
            # Bitta turdagi xarajat: summa "amount" da — bu yerda faqat tur
            same = old[0]["payment_method"] == new[0]["payment_method"]
        else:
            same = old == new
        if not same:
            changes[field] = {"old": old, "new": new}
    return changes


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
        # Tahrirlar tarixi (eng eskisi birinchi); bo'sh ro'yxat — tahrirlanmagan
        "edit_history": list(e.edit_history) if isinstance(e.edit_history, list) else [],
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

    async def _group_rows(self, hotel_id: UUID | None, expense_id: UUID) -> list[Expense]:
        """Xarajat va (bo'lingan bo'lsa) uning barcha bo'laklari."""
        expense = await self.session.get(Expense, expense_id)
        if not expense or (hotel_id is not None and expense.hotel_id != hotel_id):
            raise NotFoundException("Expense not found", "EXPENSE_NOT_FOUND")
        if expense.split_group is None:
            return [expense]
        rows = (
            await self.session.execute(
                select(Expense).where(
                    Expense.hotel_id == expense.hotel_id,
                    Expense.split_group == expense.split_group,
                )
            )
        ).scalars().all()
        # So'ralgan qator birinchi — javobning `id` si o'sha bo'lsin
        return [expense, *[r for r in rows if r.id != expense.id]]

    async def update(
        self, hotel_id: UUID | None, expense_id: UUID, data: dict, current_user: dict
    ) -> dict:
        """Xarajatni tahrirlaydi (faqat administrator, super admin, sozlovchi).

        `data` — faqat yuborilgan maydonlar (`exclude_unset`): title, amount,
        category, payment_method yoki payments, expense_date, notes va
        tahrir izohi `note`. Yuborilmagan maydon o'zgarmaydi.

        Bo'lingan xarajat BUTUNLIGICHA tahrirlanadi (bo'lakning o'zi emas):
        summa o'zgarsa bo'laklar ham berilishi shart, yig'indisi yangi
        summaga teng. Oddiy xarajat bo'linishi va bo'lingani bitta turga
        qaytishi mumkin. Qatorlar imkon qadar saqlanadi (o'sha to'lov turi —
        o'sha qator); yangi bo'lak asl xarajatning `created_at` va
        `created_by` ini oladi: smena kassasi va shaxsiy hisobot xarajatni
        o'z paytida ko'radi.

        Javob: asosiy qator (`create` bilan bir xil shakl), bo'lingan
        bo'lsa `parts`, va `edit` — shu tahrir yozuvi.
        """
        if not can_edit_expenses(current_user):
            raise ForbiddenException(
                "Xarajatni faqat administrator, super admin yoki sozlovchi tahrirlay oladi",
                EDIT_FORBIDDEN_CODE,
            )
        rows = await self._group_rows(hotel_id, expense_id)
        main = rows[0]
        before = _rows_snapshot(rows)
        old_parts = [((r.payment_method or "").strip().upper(), round(float(r.amount), 2)) for r in rows]

        # --- yangi qiymatlar (yuborilmagani — avvalgisi) ---
        title = main.title
        if "title" in data:
            title = _clean_text(data.get("title"))
            if not title:
                raise ValidationException("Xarajat nomi bo'sh bo'lmasligi kerak", "TITLE_REQUIRED")
            title = title[:255]

        total = before["amount"]
        if data.get("amount") is not None:
            total = round(float(data["amount"]), 2)
            if total <= 0:
                raise ValidationException("Summa 0 dan katta bo'lishi kerak", "AMOUNT_INVALID")

        category = main.category
        if "category" in data:
            category = await ExpenseCategoryService(self.session).resolve_name(
                main.hotel_id, data.get("category")
            )

        expense_date = main.expense_date
        if data.get("expense_date") is not None:
            expense_date = data["expense_date"]

        notes = main.notes
        if "notes" in data:
            notes = _clean_text(data.get("notes"))

        # To'lov tarkibi: bo'laklar > bitta tur > avvalgisi
        parts = normalize_parts(total, data.get("payments")) if data.get("payments") else []
        if not parts:
            method = _clean_text(data.get("payment_method"))
            if method:
                parts = [(method.upper(), total)]
            elif len(old_parts) == 1:
                parts = [(old_parts[0][0], total)]
            elif abs(sum(a for _m, a in old_parts) - total) <= TOLERANCE:
                parts = old_parts
            else:
                raise ValidationException(
                    "Bo'lingan xarajat summasi o'zgarsa, to'lov bo'laklarini ham ko'rsating",
                    "PARTS_REQUIRED",
                )

        after = _snapshot(
            title=title, category=category, parts=parts, expense_date=expense_date, notes=notes
        )
        changes = diff_snapshots(before, after)
        if not changes:
            raise ValidationException("Hech narsa o'zgarmadi", "EXPENSE_UNCHANGED")

        # --- tarix yozuvi ---
        actor_id = current_user.get("id")
        by_name: str | None = None
        if actor_id:
            actor = await self.session.get(User, UUID(str(actor_id)))
            if actor is not None:
                by_name = f"{actor.first_name} {actor.last_name}".strip() or None
        edited_at = datetime.now(timezone.utc)
        entry = {
            "at": edited_at.isoformat(),
            "by": str(actor_id) if actor_id else None,
            "by_name": by_name,
            # Haqiqiy vazifa: ADMIN / SUPER_ADMIN / CONFIGURATOR
            "by_role": actual_user_type(current_user) or current_user.get("user_type"),
            "note": _clean_text(data.get("note")),
            "changes": changes,
        }
        history = [
            *(main.edit_history if isinstance(main.edit_history, list) else []),
            entry,
        ][-MAX_HISTORY:]

        # --- qatorlarni moslash: o'sha to'lov turi — o'sha qator ---
        pool = list(rows)
        plan: list[list] = []
        for method, amount in parts:
            row = next(
                (r for r in pool if (r.payment_method or "").strip().upper() == method), None
            )
            if row is not None:
                pool.remove(row)
            plan.append([method, amount, row])
        for item in plan:
            if item[2] is None and pool:
                item[2] = pool.pop(0)
        for leftover in pool:
            await self.session.delete(leftover)

        origin_created_at = min(
            (r.created_at for r in rows if r.created_at is not None), default=None
        )
        group = (main.split_group or uuid.uuid4()) if len(plan) > 1 else None
        result: list[Expense] = []
        for method, amount, row in plan:
            if row is None:
                row = Expense(
                    hotel_id=main.hotel_id,
                    branch_id=main.branch_id,
                    created_by=main.created_by,
                    created_at=origin_created_at or edited_at,
                )
                self.session.add(row)
            row.title = title
            row.category = category
            row.amount = amount
            row.payment_method = method
            row.expense_date = expense_date
            row.notes = notes
            row.split_group = group
            # JSONB — YANGI ro'yxat (SQLAlchemy o'zgarishni sezishi uchun)
            row.edit_history = list(history)
            result.append(row)
        await self.session.flush()

        # Audit jurnali — o'z savepointida: xatosi tahrirni hech qachon buzmaydi
        try:
            async with self.session.begin_nested():
                await AuditService(self.session).log_action(
                    hotel_id=main.hotel_id,
                    user_id=UUID(str(actor_id)) if actor_id else None,
                    action="UPDATE",
                    entity_type="expense",
                    entity_id=result[0].id,
                    old_values=before,
                    new_values={**after, "note": entry["note"], "changes": list(changes)},
                )
        except Exception:
            logger.exception("Xarajat tahriri audit yozuvi saqlanmadi (expense=%s)", main.id)

        creator = await self.session.get(User, main.created_by)
        payload = expense_dict(result[0], creator)
        if len(result) > 1:
            payload["parts"] = [expense_dict(r, creator) for r in result]
        payload["edit"] = entry
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


__all__ = [
    "ExpenseService",
    "expense_dict",
    "normalize_parts",
    "can_edit_expenses",
    "diff_snapshots",
    "MAX_PARTS",
    "delete",
]
