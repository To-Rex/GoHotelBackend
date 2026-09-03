"""Vazifa bandlari: standart ro'yxat va uni vazifaga ko'chirish.

Farrosh xonaga kirganda nima qilishi kerakligini eslab qolishi shart
emas — ro'yxat oldida turadi va u har bandni belgilab boradi. Ro'yxatni
ADMINISTRATOR tuzadi: mehmonxonaning o'z tartibi bor, "shampun va sovun"
birida bo'ladi, birida bo'lmaydi.

Ikki nozik qaror bor.

BIRINCHISI — NUSXA. Vazifa ochilganda bandlar shablondan vazifaning o'z
ro'yxatiga ko'chiriladi. Ular havola bo'lib qolsa, administrator bandni
o'zgartirganda yoki o'chirganda allaqachon bajarilgan ishlarning tarixi
buzilardi: kecha "sovun almashtirildi" deb belgilangan vazifa bugun
boshqa narsani ko'rsatib turardi.

IKKINCHISI — STANDART RO'YXAT. Mehmonxona hali hech narsa kiritmagan
bo'lsa ham farrosh bo'sh ekran ko'rmasligi kerak: shuning uchun har
vazifa turi uchun odatdagi bandlar tayyor turadi. Administrator o'z
ro'yxatini kiritishi bilan standart ro'yxat ishlatilmaydi — aralashmaydi,
butunlay o'rnini bo'shatadi.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.checklist_item import ChecklistItem
from app.infrastructure.database.models.checklist_template import ChecklistTemplate

#: Bandlar biriktiriladigan vazifa turlari — xo'jalik vazifasi turlari
#: bilan bir xil.
TASK_TYPES = (
    "CLEANING",
    "DEEP_CLEANING",
    "MAINTENANCE",
    "INSPECTION",
    "TURN_DOWN",
)

#: Mehmonxona o'z ro'yxatini kiritmaguncha ishlatiladigan bandlar.
#: Ular bazaga yozilmaydi — vazifa ochilganda shu yerdan olinadi.
DEFAULT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "CLEANING": (
        "Xonani tozalash",
        "To'shakni yig'ish va choyshabni almashtirish",
        "Hammomni tozalash",
        "Shampun va sovunni almashtirish",
        "Sochiqlarni almashtirish",
        "Chiqindini chiqarish",
        "Joylarni to'g'rilash",
    ),
    "DEEP_CLEANING": (
        "Xonani to'liq tozalash",
        "Deraza va pardalarni tozalash",
        "Gilam va mebelni tozalash",
        "Hammomni chuqur tozalash",
        "Konditsioner filtrini tekshirish",
        "Shampun va sovunni almashtirish",
    ),
    "MAINTENANCE": (
        "Nosozlikni aniqlash",
        "Ehtiyot qism va asboblarni tayyorlash",
        "Ta'mirlash ishlarini bajarish",
        "Ishlashini tekshirish",
        "Ish joyini tozalash",
    ),
    "INSPECTION": (
        "Xona tozaligini tekshirish",
        "Jihozlar ishlashini tekshirish",
        "Sanitariya buyumlari mavjudligini tekshirish",
        "Nosozliklarni qayd etish",
    ),
    "TURN_DOWN": (
        "To'shakni kechki holatga keltirish",
        "Sochiqlarni almashtirish",
        "Chiqindini chiqarish",
        "Yorug'likni kechki rejimga o'tkazish",
    ),
}

MAX_TITLE_LENGTH = 255


def _require_admin(current_user: dict) -> None:
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException(
            "Vazifa bandlarini faqat administrator boshqaradi", "ADMIN_ONLY"
        )


def _clean_title(title: str) -> str:
    text = (title or "").strip()
    if not text:
        raise ValidationException("Band nomi bo'sh bo'lmasligi kerak", "TITLE_REQUIRED")
    if len(text) > MAX_TITLE_LENGTH:
        raise ValidationException(
            f"Band nomi {MAX_TITLE_LENGTH} belgidan uzun bo'lmasligi kerak",
            "TITLE_TOO_LONG",
        )
    return text


def _clean_task_type(task_type: str) -> str:
    value = (task_type or "").strip().upper()
    if value not in TASK_TYPES:
        raise ValidationException(
            f"Noma'lum vazifa turi: {task_type}", "INVALID_TASK_TYPE"
        )
    return value


class ChecklistTemplateService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- o'qish --

    async def _rows(
        self, hotel_id: UUID, task_type: str | None = None
    ) -> list[ChecklistTemplate]:
        stmt = select(ChecklistTemplate).where(ChecklistTemplate.hotel_id == hotel_id)
        if task_type:
            stmt = stmt.where(ChecklistTemplate.task_type == task_type)
        stmt = stmt.order_by(
            ChecklistTemplate.task_type,
            ChecklistTemplate.sort_order,
            ChecklistTemplate.created_at,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_templates(
        self, hotel_id: UUID, task_type: str | None = None
    ) -> list[dict]:
        """Saqlangan bandlar. Bo'sh bo'lsa ham STANDART ro'yxat qaytmaydi —
        administrator nima kiritganini aynan ko'rishi kerak."""
        rows = await self._rows(
            hotel_id, _clean_task_type(task_type) if task_type else None
        )
        return [
            {
                "id": str(row.id),
                "task_type": row.task_type,
                "title": row.title,
                "sort_order": row.sort_order,
                "is_active": row.is_active,
            }
            for row in rows
        ]

    async def defaults(self, task_type: str | None = None) -> dict[str, list[str]]:
        """Standart bandlar — administratorga namuna sifatida ko'rsatiladi."""
        if task_type:
            value = _clean_task_type(task_type)
            return {value: list(DEFAULT_TEMPLATES.get(value, ()))}
        return {key: list(items) for key, items in DEFAULT_TEMPLATES.items()}

    # ------------------------------------------------------ yozish --

    async def create(
        self, hotel_id: UUID, data: dict, current_user: dict
    ) -> dict:
        _require_admin(current_user)
        task_type = _clean_task_type(data.get("task_type", ""))
        title = _clean_title(data.get("title", ""))

        sort_order = data.get("sort_order")
        if sort_order is None:
            # Yangi band ro'yxat oxiriga tushadi
            existing = await self._rows(hotel_id, task_type)
            sort_order = (max((r.sort_order for r in existing), default=-1)) + 1

        row = ChecklistTemplate(
            hotel_id=hotel_id,
            task_type=task_type,
            title=title,
            sort_order=int(sort_order),
            is_active=bool(data.get("is_active", True)),
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return {
            "id": str(row.id),
            "task_type": row.task_type,
            "title": row.title,
            "sort_order": row.sort_order,
            "is_active": row.is_active,
        }

    async def update(
        self, hotel_id: UUID, template_id: UUID, data: dict, current_user: dict
    ) -> dict:
        _require_admin(current_user)
        row = await self._get(hotel_id, template_id)
        if "title" in data and data["title"] is not None:
            row.title = _clean_title(data["title"])
        if "sort_order" in data and data["sort_order"] is not None:
            row.sort_order = int(data["sort_order"])
        if "is_active" in data and data["is_active"] is not None:
            row.is_active = bool(data["is_active"])
        # `updated_at` da server tomonidagi `onupdate` bor: refreshsiz
        # javob seriyalanayotganda u lazy-load bo'lib xato berardi
        await self.session.flush()
        await self.session.refresh(row)
        return {
            "id": str(row.id),
            "task_type": row.task_type,
            "title": row.title,
            "sort_order": row.sort_order,
            "is_active": row.is_active,
        }

    async def delete(
        self, hotel_id: UUID, template_id: UUID, current_user: dict
    ) -> None:
        _require_admin(current_user)
        row = await self._get(hotel_id, template_id)
        # Shablon o'chirilsa ham ochilgan vazifalardagi NUSXALAR qoladi —
        # bajarilgan ishlar tarixi yo'qolmasligi kerak
        await self.session.delete(row)
        await self.session.flush()

    async def reorder(
        self, hotel_id: UUID, task_type: str, ids: list[UUID], current_user: dict
    ) -> list[dict]:
        """Bandlar tartibini butunlay qayta yozadi."""
        _require_admin(current_user)
        value = _clean_task_type(task_type)
        rows = {row.id: row for row in await self._rows(hotel_id, value)}
        for position, template_id in enumerate(ids):
            row = rows.get(template_id)
            if row is None:
                raise NotFoundException(
                    "Band topilmadi", "CHECKLIST_TEMPLATE_NOT_FOUND"
                )
            row.sort_order = position
        await self.session.flush()
        return await self.list_templates(hotel_id, value)

    async def replace_all(
        self, hotel_id: UUID, task_type: str, titles: list[str], current_user: dict
    ) -> list[dict]:
        """Turdagi barcha bandlarni berilgan ro'yxat bilan almashtiradi.

        Standart ro'yxatni "shu yerdan boshlab tahrirlash" uchun qulay:
        administrator namunani oladi, ustiga o'zgartiradi va saqlaydi.
        """
        _require_admin(current_user)
        value = _clean_task_type(task_type)
        cleaned = [_clean_title(t) for t in titles]

        rows = await self._rows(hotel_id, value)
        if not cleaned:
            # BO'SH ro'yxat — ongli tanlov. Qatorlarni o'chirib yuborsak
            # "hech qachon sozlanmagan" holatidan farqi qolmasdi va
            # keyingi vazifaga standart bandlar qaytib tushardi. Shuning
            # uchun ular o'chirilmaydi, faqat o'chirib qo'yiladi:
            # administrator fikridan qaytsa qayta yoqa oladi.
            for row in rows:
                row.is_active = False
            await self.session.flush()
            return await self.list_templates(hotel_id, value)

        for row in rows:
            await self.session.delete(row)
        await self.session.flush()

        for position, title in enumerate(cleaned):
            self.session.add(
                ChecklistTemplate(
                    hotel_id=hotel_id,
                    task_type=value,
                    title=title,
                    sort_order=position,
                    is_active=True,
                )
            )
        await self.session.flush()
        return await self.list_templates(hotel_id, value)

    async def _get(self, hotel_id: UUID, template_id: UUID) -> ChecklistTemplate:
        row = (
            await self.session.execute(
                select(ChecklistTemplate).where(
                    ChecklistTemplate.id == template_id,
                    ChecklistTemplate.hotel_id == hotel_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundException("Band topilmadi", "CHECKLIST_TEMPLATE_NOT_FOUND")
        return row

    # ---------------------------------------- vazifaga nusxa olish --

    async def titles_for(self, hotel_id: UUID, task_type: str) -> list[str]:
        """Shu tur uchun ishlatiladigan bandlar nomi, tartibda.

        Mehmonxona o'z ro'yxatini kiritgan bo'lsa — faqat o'sha (faol
        bandlar). Kiritmagan bo'lsa — standart ro'yxat.
        """
        value = (task_type or "").strip().upper()
        rows = await self._rows(hotel_id, value if value in TASK_TYPES else None)
        own = [r.title for r in rows if r.task_type == value and r.is_active]
        if own:
            return own
        # Mehmonxona bandlarni kiritgan, lekin hammasini o'chirib qo'ygan
        # bo'lsa — bu ham ONGLI tanlov: standart ro'yxatni tiqishtirmaymiz.
        # Shu sabab `replace_all` bo'sh ro'yxatda qatorlarni o'chirmaydi,
        # faqat o'chirib qo'yadi — aks holda bu farq yo'qolardi.
        if any(r.task_type == value for r in rows):
            return []
        return list(DEFAULT_TEMPLATES.get(value, ()))

    async def attach_to_task(self, task) -> int:
        """Vazifaga bandlarni nusxalab qo'yadi. Nechta qo'shilganini qaytaradi.

        Vazifada allaqachon bandlar bo'lsa hech narsa qilinmaydi — bu
        funksiya vazifa YARATILGANDA chaqiriladi va takroriy chaqiruv
        ro'yxatni ikkilantirib yubormasligi kerak.
        """
        existing = (
            await self.session.execute(
                select(ChecklistItem.id).where(ChecklistItem.task_id == task.id).limit(1)
            )
        ).first()
        if existing:
            return 0

        titles = await self.titles_for(task.hotel_id, task.task_type)
        for position, title in enumerate(titles):
            self.session.add(
                ChecklistItem(
                    task_id=task.id,
                    title=title,
                    is_completed=False,
                    sort_order=position,
                )
            )
        if titles:
            await self.session.flush()
        return len(titles)
