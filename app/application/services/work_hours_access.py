"""Ish vaqtidan tashqarida xodimni tizimga qo'ymaslik.

Mehmonxona sozlamasi (`branches.settings["work_hours"]["enforce"]`) yoqilsa,
oddiy xodim (`EMPLOYEE`) o'z ish vaqti (`work_start`–`work_end`) dan
tashqarida tizimdan foydalana olmaydi: har bir so'rov 403
`OUTSIDE_WORK_HOURS` bilan qaytadi va klient "ish vaqtingiz emas" ekranini
ko'rsatadi. Standart — O'CHIQ: sozlama qo'shilishidan oldingi mehmonxonalar
avvalgidek ishlayveradi.

Kim to'silMAYDI:

* `ADMIN` va `SUPER_ADMIN` — hech qachon (aks holda administrator
  sozlamani o'chirish uchun ham kira olmay qolardi). Panel foydalanuvchilari
  alohida token bilan ishlaydi va bu yerga umuman tushmaydi.
* "Ish vaqtidan tashqari ham ishlay oladi" belgisi qo'yilgan xodim
  (`users.allow_outside_work_hours`). Belgini faqat administrator qo'yadi.
* Ish vaqti yozilmagan, buzuq yoki boshlanish = tugash (24 soat) bo'lgan
  xodim — `cleaner_assignment.is_on_duty` dagi qoida: ma'lumot xatosi
  odamni ishdan to'sib qo'ymasin.
* FAOL (`ACTIVE`) smena sessiyasi bor xodim. Bu qabulxonaning bugungi
  ish oqimini aynan saqlaydi: vaqti tugaganda kassa sessiyasi hamon ochiq,
  veb "ish vaqti tugadi" oynasini ko'rsatadi, "Davom etish" bosilgach xodim
  ishlashda davom etadi, kassani yopib smenani topshira oladi.
* Mehmonxona to'xtatilgan bo'lsa — bu tekshiruv o'tkazib yuboriladi:
  `HOTEL_*` xatosi ("xizmat to'xtatilgan" ekrani) ustun bo'lishi kerak.

Tekshiruv `get_current_user` ichida, qurilma tekshiruvidan keyin ishlaydi —
faqat shu joy BARCHA autentifikatsiyali so'rovlarni qamraydi (farroshning
`/tasks`, `/problems` yo'llarida `require_active_hotel` yo'q). Shuning uchun
ilova o'zini tiklashi uchun zarur bir nechta yo'l ochiq qoldiriladi
(`ALLOWED_PATHS`). Kirish (login) va `/auth/refresh` autentifikatsiyasiz —
ularga tegilmaydi: xodim kira oladi, ilova to'siq ekranini ko'rsatadi va
ish vaqti boshlanganda o'zi tiklanadi.

Qaror mantig'i (`work_hours_block_error`, `resolve_work_hours_settings`,
`is_path_allowed`, `outside_flag_change_error`) bazaga bog'lanmagan sof
funksiyalar — tests/test_work_hours_access.py.
"""
from __future__ import annotations

from datetime import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.cleaner_assignment import is_on_duty, local_time_now
from app.application.services.hotel_access import ACTIVE_STATUS
from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.shift import ShiftSession
from app.infrastructure.database.models.user import User

#: branches.settings ichidagi kalit
WORK_HOURS_SETTINGS_KEY = "work_hours"

#: Standart — o'chiq: avvalgi xatti-harakat o'zgarmaydi
WORK_HOURS_DEFAULTS = {"enforce": False}

#: Klient shu kodga qarab "ish vaqtingiz emas" ekranini ko'rsatadi.
#: `HOTEL_` bilan BOSHLANMASLIGI shart — klientlar `HOTEL_*` ni "mehmonxona
#: to'xtatilgan" deb tushunadi.
OUTSIDE_WORK_HOURS_CODE = "OUTSIDE_WORK_HOURS"

#: Faqat shu tur to'siladi
EMPLOYEE_TYPE = "EMPLOYEE"

#: Sozlamani va xodim belgisini o'zgartira oladiganlar
ADMIN_TYPES = ("ADMIN", "SUPER_ADMIN")

#: To'silgan xodimga ham ochiq yo'llar (to'liq yo'l, `/api/v1` bilan).
#: Ilova ishga tushishi (`/auth/me`), chiqish, push tokenini yangilash va
#: to'siq ekrani uchun zarur — boshqa hech narsa.
ALLOWED_PATHS = frozenset(
    {
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
        "/api/v1/notifications/register-device",
        "/api/v1/hotels/work-hours-settings",
        "/api/v1/auth/face/status",
    }
)

#: Prefiks bo'yicha ochiq yo'llar — passkey (Face ID / Windows Hello) boshqaruvi
ALLOWED_PREFIXES = ("/api/v1/auth/webauthn/",)

_API_PREFIX = "/api/v1/"


def resolve_work_hours_settings(hotel_settings: dict | None) -> dict:
    """Mehmonxona sozlamasi; yo'q yoki buzuq qiymat — standart (o'chiq).

    Faqat aniq `True` yoqadi: buzuq yozuv butun jamoani tizimdan
    chiqarib yubormasligi kerak.
    """
    saved = (hotel_settings or {}).get(WORK_HOURS_SETTINGS_KEY)
    if not isinstance(saved, dict):
        return dict(WORK_HOURS_DEFAULTS)
    return {"enforce": saved.get("enforce") is True}


def _normalize_path(path: str | None) -> str:
    # Bo'shliqlar kesilmaydi: marshrutlash ham ularni kesmaydi, ochiq yo'l
    # boshqa so'rovga "o'xshab" qolmasligi kerak
    value = path or ""
    # Proksi orqali ilova prefiks bilan joylashtirilsa ham (root_path) —
    # `/api/v1/` dan boshlab solishtiriladi
    index = value.find(_API_PREFIX)
    if index > 0:
        value = value[index:]
    if len(value) > 1:
        value = value.rstrip("/")
    return value


def is_path_allowed(path: str | None) -> bool:
    """So'rov yo'li to'silgan xodimga ham ochiqmi."""
    value = _normalize_path(path)
    if value in ALLOWED_PATHS:
        return True
    return any(value.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def outside_hours_message(work_start: str | None, work_end: str | None) -> str:
    return (
        f"Ish vaqtingiz emas ({work_start}–{work_end}). Ish vaqti boshlanganda "
        "tizimdan foydalanishingiz mumkin."
    )


def work_hours_block_error(
    *,
    user_type: str | None,
    enforce: bool,
    allow_outside: bool,
    work_start: str | None,
    work_end: str | None,
    now: time,
    has_active_session: bool = False,
) -> ForbiddenException | None:
    """Xodim hozir to'siladimi; to'silsa — tayyor xato.

    `None` qaytsa hammasi joyida. Xato QAYTARILADI (ko'tarilmaydi) —
    `hotel_access.hotel_block_error` bilan bir xil naqsh.
    """
    if user_type != EMPLOYEE_TYPE:
        return None
    if not enforce:
        return None
    if allow_outside:
        return None
    # Ish vaqtida, 24 soatlik yoki jadval buzuq/yo'q — to'silmaydi
    if is_on_duty(work_start, work_end, now):
        return None
    # Ochiq smena sessiyasi — qabulxonaning "Davom etish" oqimi
    if has_active_session:
        return None
    return ForbiddenException(
        outside_hours_message(work_start, work_end), OUTSIDE_WORK_HOURS_CODE
    )


def outside_flag_change_error(
    actor_type: str | None, requested: bool | None, current: bool = False
) -> ForbiddenException | None:
    """«Ish vaqtidan tashqari ham ishlay oladi» belgisini o'zgartirish huquqi.

    Faqat administrator. Menejer (`employee.update` egasi) o'zini ham,
    boshqani ham ozod qila olmaydi. Qiymat yuborilmagan yoki joriysi bilan
    bir xil bo'lsa — o'zgarish yo'q, demak ruxsat (tahrirlash oynasi
    belgini har safar qaytarib yuborishi mumkin).
    """
    if requested is None:
        return None
    if actor_type in ADMIN_TYPES:
        return None
    if bool(requested) == bool(current):
        return None
    return ForbiddenException(
        "Ish vaqtidan tashqari ishlash ruxsatini faqat administrator "
        "o'zgartira oladi",
        "FORBIDDEN",
    )


async def has_active_shift_session(
    session: AsyncSession, hotel_id: UUID, user_id: UUID
) -> bool:
    """Xodimning shu mehmonxonada FAOL smena sessiyasi bormi."""
    found = (
        await session.execute(
            select(ShiftSession.id)
            .where(
                ShiftSession.hotel_id == hotel_id,
                ShiftSession.user_id == user_id,
                ShiftSession.status == "ACTIVE",
            )
            .limit(1)
        )
    ).scalar()
    return found is not None


async def evaluate_work_hours_block(
    session: AsyncSession,
    *,
    user_type: str | None,
    hotel_id: UUID | None,
    user_id: UUID | None,
    hotel: Hotel | None = None,
    user: User | None = None,
    now: time | None = None,
) -> ForbiddenException | None:
    """Xodim hozir (yo'l ruxsatidan tashqari) to'siladimi.

    So'rov narxi past bo'lishi uchun tartib muhim: xodim bo'lmasa — bazaga
    umuman tegilmaydi; sozlama o'chiq bo'lsa — faqat mehmonxona (identity
    map orqali `require_active_hotel` bilan bo'lishiladi); xodim jadvali va
    smena sessiyasi faqat kerak bo'lganda o'qiladi.

    `/auth/me` ham shu funksiyani ishlatadi — `work_hours_blocked` va
    haqiqiy to'siq hech qachon ajralib ketmaydi.
    """
    if user_type != EMPLOYEE_TYPE or not hotel_id or not user_id:
        return None

    if hotel is None:
        hotel = await session.get(Hotel, hotel_id)
    # Mehmonxona yo'q yoki to'xtatilgan — HOTEL_* xatosi ustun
    if hotel is None or (hotel.status or "").upper() != ACTIVE_STATUS:
        return None
    # Sozlama — xodim filialiniki (branch_settings)
    from app.application.services.branch_settings import settings_owner

    owner = await settings_owner(session, hotel_id, getattr(user, "branch_id", None))
    if not resolve_work_hours_settings(owner.settings if owner else None)["enforce"]:
        return None

    if user is not None:
        allow_outside = user.allow_outside_work_hours
        work_start, work_end = user.work_start, user.work_end
    else:
        row = (
            await session.execute(
                select(
                    User.allow_outside_work_hours, User.work_start, User.work_end
                ).where(User.id == user_id)
            )
        ).first()
        if row is None:
            return None
        allow_outside, work_start, work_end = row

    params = dict(
        user_type=user_type,
        enforce=True,
        allow_outside=bool(allow_outside),
        work_start=work_start,
        work_end=work_end,
        now=now if now is not None else local_time_now(),
    )
    if work_hours_block_error(**params) is None:
        return None
    # Faqat ish vaqtidan tashqarida — smena sessiyasi so'raladi
    active = await has_active_shift_session(session, hotel_id, user_id)
    return work_hours_block_error(**params, has_active_session=active)


async def assert_within_work_hours(
    session: AsyncSession, current_user: dict, path: str | None
) -> None:
    """`get_current_user` dan chaqiriladi: to'silsa 403 OUTSIDE_WORK_HOURS."""
    if current_user.get("user_type") != EMPLOYEE_TYPE:
        return
    if not current_user.get("hotel_id"):
        return
    if is_path_allowed(path):
        return
    error = await evaluate_work_hours_block(
        session,
        user_type=current_user.get("user_type"),
        hotel_id=current_user.get("hotel_id"),
        user_id=current_user.get("id"),
    )
    if error is not None:
        raise error
