"""Sozlovchi (CONFIGURATOR) roli va sozlamalarni kim o'zgartira olishi.

Sozlovchi — mehmonxonaga bog'lanmagan (`users.hotel_id` bo'sh), tizim
egasi tomonidan boshqaruv panelida yaratiladigan hisob. U istalgan
mehmonxona va istalgan filialga "o'tib", o'sha mehmonxonani talabiga ko'ra
sozlab beradi.

Qanday ishlaydi:

* Kirgandan keyin sozlovchining tokenida mehmonxona yo'q — u avval
  `POST /auth/context` bilan mehmonxona va filialni tanlaydi. Tanlov yangi
  token juftligiga yoziladi (`hotel_id`, `branch_id` claim'lari) va token
  yangilanganda ham saqlanadi.
* Tanlangan mehmonxonada sozlovchi ADMINISTRATOR kabi ishlaydi:
  `get_current_user` uning `user_type` ini "ADMIN" qilib beradi
  (`effective_user_type`), haqiqiy turi esa `actual_user_type` da qoladi.
  Shu tufayli tizimdagi o'nlab "ADMIN/SUPER_ADMIN" tekshiruvlariga tegilmadi —
  ularning har biriga yangi turni qo'shish xatoga moyil bo'lardi.
* Mehmonxona tanlanmagan sozlovchi hech qaysi mehmonxona ma'lumotiga tega
  olmaydi: uning turi "CONFIGURATOR" bo'lib qoladi, endpointlar "Hotel
  context required" qaytaradi.
* Qurilma tasdig'i va "mehmonxona to'xtatilgan" to'sig'i sozlovchiga
  qo'llanmaydi (tizim ma'muri kabi): u ko'p mehmonxonada ishlaydi va
  to'xtatilgan mehmonxonani ham sozlashi kerak bo'lishi mumkin.

SOZLAMALAR: mehmonxona sozlamalarini (Sozlamalar sahifasidagi hamma narsa)
faqat sozlovchi va tizim ma'muri (SUPER_ADMIN) o'zgartiradi. Mehmonxona
administratori va xodimlar sozlamalarni faqat O'QIYDI — bron oynasi,
smena va boshqalar shu qiymatlar bilan ishlaydi.

Tizim ma'muri ham mehmonxona tanlay oladi (ixtiyoriy) — tanlamasa avvalgidek
`?hotel_id=` bilan ishlaydi.
"""
from __future__ import annotations

from uuid import UUID

from app.core.exceptions import ForbiddenException

CONFIGURATOR_TYPE = "CONFIGURATOR"
SUPER_ADMIN_TYPE = "SUPER_ADMIN"
ADMIN_TYPE = "ADMIN"

#: Mehmonxona va filialni o'zi tanlay oladiganlar
CONTEXT_SWITCH_TYPES = (CONFIGURATOR_TYPE, SUPER_ADMIN_TYPE)

#: Mehmonxona sozlamalarini o'zgartira oladiganlar
SETTINGS_MANAGER_TYPES = (CONFIGURATOR_TYPE, SUPER_ADMIN_TYPE)

SETTINGS_FORBIDDEN_CODE = "SETTINGS_CONFIGURATOR_ONLY"


def effective_user_type(token_user_type: str | None, hotel_id: UUID | None) -> str:
    """So'rovda ishlatiladigan tur: mehmonxona tanlagan sozlovchi — ADMIN."""
    value = token_user_type or ""
    if value == CONFIGURATOR_TYPE and hotel_id:
        return ADMIN_TYPE
    return value


def actual_user_type(current_user: dict | None) -> str:
    """Foydalanuvchining HAQIQIY turi (sozlovchi ADMIN bo'lib ko'rinsa ham)."""
    if not current_user:
        return ""
    return current_user.get("actual_user_type") or current_user.get("user_type") or ""


def is_configurator(current_user: dict | None) -> bool:
    return actual_user_type(current_user) == CONFIGURATOR_TYPE


def can_switch_context(current_user: dict | None) -> bool:
    return actual_user_type(current_user) in CONTEXT_SWITCH_TYPES


def can_manage_settings(current_user: dict | None) -> bool:
    return actual_user_type(current_user) in SETTINGS_MANAGER_TYPES


def assert_can_manage_settings(current_user: dict | None) -> None:
    """Sozlamani o'zgartirishdan oldin: faqat sozlovchi va tizim ma'muri."""
    if not can_manage_settings(current_user):
        raise ForbiddenException(
            "Sozlamalarni faqat sozlovchi o'zgartira oladi",
            SETTINGS_FORBIDDEN_CODE,
        )
