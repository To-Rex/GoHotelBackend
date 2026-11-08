"""Xarajat toifalari: mehmonxonaning tanlash ro'yxati.

Xarajat toifani NOM bilan saqlaydi (`expenses.category`) — hisobotlar
shu nom bo'yicha guruhlaydi, mobil ilova ham shu nomni o'qiydi. Toifalar
jadvali esa ro'yxatning o'zi: nom, belgi, rang, tartib, arxiv. Shundan
uch qoida kelib chiqadi:

* QAYTA NOMLASH xarajatlarga ham o'tadi. Toifa "Kommunal" dan "Kommunal
  to'lovlar" ga o'zgarsa, shu mehmonxonaning (barcha filiallarning)
  xarajatlaridagi nom ham o'zgaradi — aks holda hisobotda bir toifa
  ikkiga bo'linib qolardi.
* O'CHIRISH ishlatilgan toifani yo'qotmaydi. Xarajati bor toifa ARXIVGA
  tushadi: tanlovdan chiqadi, lekin eski xarajatlar o'z toifasida qoladi
  va uni qayta yoqsa bo'ladi. Hech qachon ishlatilmagan toifa butunlay
  o'chadi.
* STANDART RO'YXAT bir marta. Mehmonxonada hali birorta toifa bo'lmasa
  (yangi mehmonxona) ro'yxat birinchi ochilganda standart toifalar
  yoziladi. Mavjud mehmonxonalarga ular migratsiyada kiritilgan.
  Keyin administrator nimani qoldirsa — shu.

Kim boshqaradi: ADMIN/SUPER_ADMIN va `expense.delete` ruxsati bor xodim
(xarajatni o'chira oladigan menejer/buxgalter). O'qish va tanlash —
xarajat kirita oladigan har bir xodimga.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.expense_category import ExpenseCategory
from app.shared.expense_categories import (
    COLORS,
    ICONS,
    IMPORTED_COLOR,
    IMPORTED_ICON,
    MAX_NAME_LENGTH,
    OTHER_NAME,
    default_rows,
    name_key,
    normalize_name,
)

#: Toifalarni boshqarishga yetarli ruxsat kodi (xarajatni o'chira oladigan
#: xodim toifalarni ham tartibga sola oladi)
MANAGE_PERMISSION = "expense.delete"


def can_manage_categories(current_user: dict | None) -> bool:
    if not current_user:
        return False
    if current_user.get("user_type") in ("ADMIN", "SUPER_ADMIN"):
        return True
    return MANAGE_PERMISSION in (current_user.get("permissions") or [])


def _require_manager(current_user: dict | None) -> None:
    if not can_manage_categories(current_user):
        raise ForbiddenException(
            "Xarajat toifalarini faqat administrator yoki xarajatni o'chira "
            "oladigan xodim o'zgartira oladi",
            "EXPENSE_CATEGORY_FORBIDDEN",
        )


def _clean_name(name: str | None) -> str:
    text = normalize_name(name)
    if not text:
        raise ValidationException("Toifa nomi bo'sh bo'lmasligi kerak", "NAME_REQUIRED")
    if len(text) > MAX_NAME_LENGTH:
        raise ValidationException(
            f"Toifa nomi {MAX_NAME_LENGTH} belgidan uzun bo'lmasligi kerak",
            "NAME_TOO_LONG",
        )
    return text


def _clean_icon(icon: str | None) -> str | None:
    value = (icon or "").strip()
    if not value:
        return None
    if value not in ICONS:
        raise ValidationException(f"Noma'lum belgi: {icon}", "INVALID_ICON")
    return value


def _clean_color(color: str | None) -> str | None:
    value = (color or "").strip().lower()
    if not value:
        return None
    if value not in COLORS:
        raise ValidationException(f"Noma'lum rang: {color}", "INVALID_COLOR")
    return value


class ExpenseCategoryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- o'qish --

    async def _rows(self, hotel_id: UUID) -> list[ExpenseCategory]:
        stmt = (
            select(ExpenseCategory)
            .where(ExpenseCategory.hotel_id == hotel_id)
            .order_by(ExpenseCategory.sort_order, ExpenseCategory.created_at, ExpenseCategory.name)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _usage(self, hotel_id: UUID) -> dict[str, tuple[int, float]]:
        """Nom kaliti → (xarajatlar soni, summasi) — BUTUN mehmonxona bo'yicha.

        Filial chegarasi ataylab olib tashlangan: toifa mehmonxonaniki,
        boshqa filialda ishlatilgan toifani "ishlatilmagan" deb o'chirib
        yuborish mumkin emas.
        """
        stmt = (
            select(
                func.lower(Expense.category),
                func.count(Expense.id),
                func.coalesce(func.sum(Expense.amount), 0),
            )
            .where(Expense.hotel_id == hotel_id, Expense.category.is_not(None))
            .group_by(func.lower(Expense.category))
            .execution_options(all_branches=True)
        )
        usage: dict[str, tuple[int, float]] = {}
        for name, count, total in (await self.session.execute(stmt)).all():
            key = name_key(name)
            if not key:
                continue
            prev = usage.get(key, (0, 0.0))
            usage[key] = (prev[0] + int(count or 0), prev[1] + float(total or 0))
        return usage

    @staticmethod
    def _dict(row: ExpenseCategory, usage: dict[str, tuple[int, float]] | None = None) -> dict:
        count, total = (usage or {}).get(name_key(row.name), (0, 0.0))
        return {
            "id": str(row.id),
            "hotel_id": str(row.hotel_id),
            "name": row.name,
            "icon": row.icon,
            "color": row.color,
            "sort_order": row.sort_order,
            "is_active": row.is_active,
            "usage_count": count,
            "usage_total": round(total, 2),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    async def list_categories(
        self, hotel_id: UUID, include_archived: bool = False
    ) -> list[dict]:
        rows = await self._rows(hotel_id)
        if not rows:
            rows = await self._seed_defaults(hotel_id)
        usage = await self._usage(hotel_id)
        return [
            self._dict(row, usage)
            for row in rows
            if include_archived or row.is_active
        ]

    async def _seed_defaults(self, hotel_id: UUID) -> list[ExpenseCategory]:
        """Birorta toifasi yo'q mehmonxonaga standart ro'yxat."""
        rows = [ExpenseCategory(**data) for data in default_rows(hotel_id)]
        self.session.add_all(rows)
        await self.session.flush()
        return await self._rows(hotel_id)

    async def resolve_name(self, hotel_id: UUID, raw: str | None) -> str | None:
        """Xarajatga yoziladigan toifa nomi.

        Ro'yxatdagi toifaga mos kelsa (harf kattaligi, bo'shliqlar farq
        qilmaydi) — ro'yxatdagi YOZILISHI olinadi: "ish haqi" → "Ish haqi",
        hisobot bitta guruhda qoladi. Mos kelmasa — tozalangan matnning
        o'zi: eski mijozlar va qo'lda yozilgan toifa ishlayveradi.
        """
        text = normalize_name(raw)
        if not text:
            return None
        key = name_key(text)
        for row in await self._rows(hotel_id):
            if name_key(row.name) == key:
                return row.name
        return text[:MAX_NAME_LENGTH]

    # ------------------------------------------------------ yozish --

    async def _find_by_key(self, hotel_id: UUID, key: str) -> ExpenseCategory | None:
        for row in await self._rows(hotel_id):
            if name_key(row.name) == key:
                return row
        return None

    async def create(self, hotel_id: UUID, data: dict, current_user: dict) -> dict:
        _require_manager(current_user)
        name = _clean_name(data.get("name"))
        icon = _clean_icon(data.get("icon"))
        color = _clean_color(data.get("color"))

        rows = await self._rows(hotel_id)
        same = next((r for r in rows if name_key(r.name) == name_key(name)), None)
        if same is not None:
            if same.is_active:
                raise ConflictException(
                    f"\"{same.name}\" toifasi allaqachon bor", "EXPENSE_CATEGORY_EXISTS"
                )
            # Arxivdagi toifa qayta yaratilmaydi — qayta yoqiladi: eski
            # xarajatlari o'sha nom bilan turibdi, ular bilan birga qaytadi
            same.is_active = True
            same.name = name
            if icon:
                same.icon = icon
            if color:
                same.color = color
            await self.session.flush()
            await self.session.refresh(same)
            return self._dict(same, await self._usage(hotel_id))

        sort_order = data.get("sort_order")
        if sort_order is None:
            # Yangi toifa "Boshqa" dan OLDIN, qolganlarning oxirida turadi
            active = [r for r in rows if r.is_active]
            last = active[-1] if active else None
            if last is not None and name_key(last.name) == name_key(OTHER_NAME):
                sort_order = last.sort_order
                last.sort_order = sort_order + 1
            else:
                sort_order = max((r.sort_order for r in rows), default=-1) + 1

        row = ExpenseCategory(
            hotel_id=hotel_id,
            name=name,
            icon=icon or IMPORTED_ICON,
            color=color or IMPORTED_COLOR,
            sort_order=int(sort_order),
            is_active=True,
            created_by=current_user.get("id"),
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return self._dict(row, await self._usage(hotel_id))

    async def update(
        self, hotel_id: UUID, category_id: UUID, data: dict, current_user: dict
    ) -> dict:
        _require_manager(current_user)
        row = await self._get(hotel_id, category_id)
        renamed = 0

        if data.get("name") is not None:
            name = _clean_name(data["name"])
            if name != row.name:
                clash = await self._find_by_key(hotel_id, name_key(name))
                if clash is not None and clash.id != row.id:
                    raise ConflictException(
                        f"\"{clash.name}\" toifasi allaqachon bor", "EXPENSE_CATEGORY_EXISTS"
                    )
                # Xarajatlardagi nom ham o'zgaradi — BARCHA filiallarda,
                # aks holda hisobotda toifa ikkiga bo'linib qolardi
                result = await self.session.execute(
                    update(Expense)
                    .where(Expense.hotel_id == hotel_id, Expense.category == row.name)
                    .values(category=name)
                    .execution_options(all_branches=True, synchronize_session=False)
                )
                renamed = int(result.rowcount or 0)
                row.name = name
        if "icon" in data and data["icon"] is not None:
            row.icon = _clean_icon(data["icon"]) or row.icon
        if "color" in data and data["color"] is not None:
            row.color = _clean_color(data["color"]) or row.color
        if data.get("sort_order") is not None:
            row.sort_order = int(data["sort_order"])
        if data.get("is_active") is not None:
            row.is_active = bool(data["is_active"])

        await self.session.flush()
        await self.session.refresh(row)
        payload = self._dict(row, await self._usage(hotel_id))
        payload["renamed_expenses"] = renamed
        return payload

    async def delete(self, hotel_id: UUID, category_id: UUID, current_user: dict) -> dict:
        """Ishlatilmagan toifa o'chadi; ishlatilgani arxivga tushadi."""
        _require_manager(current_user)
        row = await self._get(hotel_id, category_id)
        count, _total = (await self._usage(hotel_id)).get(name_key(row.name), (0, 0.0))
        if count > 0:
            row.is_active = False
            await self.session.flush()
            await self.session.refresh(row)
            return {"archived": True, "deleted": False, "usage_count": count,
                    "category": self._dict(row, {name_key(row.name): (count, _total)})}
        await self.session.delete(row)
        await self.session.flush()
        return {"archived": False, "deleted": True, "usage_count": 0, "category": None}

    async def reorder(self, hotel_id: UUID, ids: list[UUID], current_user: dict) -> list[dict]:
        """Ko'rsatilgan toifalar berilgan tartibda boshiga chiqadi, qolganlari
        o'z tartibida ulardan keyin turadi."""
        _require_manager(current_user)
        rows = await self._rows(hotel_id)
        by_id = {row.id: row for row in rows}
        position = 0
        seen: set[UUID] = set()
        for category_id in ids:
            row = by_id.get(category_id)
            if row is None:
                raise NotFoundException("Toifa topilmadi", "EXPENSE_CATEGORY_NOT_FOUND")
            if category_id in seen:
                continue
            row.sort_order = position
            seen.add(category_id)
            position += 1
        for row in rows:
            if row.id not in seen:
                row.sort_order = position
                position += 1
        await self.session.flush()
        return await self.list_categories(hotel_id, include_archived=True)

    async def _get(self, hotel_id: UUID, category_id: UUID) -> ExpenseCategory:
        row = (
            await self.session.execute(
                select(ExpenseCategory).where(
                    ExpenseCategory.id == category_id,
                    ExpenseCategory.hotel_id == hotel_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundException("Toifa topilmadi", "EXPENSE_CATEGORY_NOT_FOUND")
        return row
