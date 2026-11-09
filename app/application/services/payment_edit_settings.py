"""To'lov turini tahrirlash huquqi: kim, qancha marta, qaysi sozlama bilan.

Uch daraja:

* ADMINISTRATOR / tizim ma'muri — cheklovsiz (eski to'lovlar ham).
* MENEJER (`shift.force_close`) — faqat oxirgi 2 kunlik to'lovlar
  (`PAYMENT_EDIT_WINDOW_HOURS`).
* QABULXONA (`finance.payment.create`, menejer emas) — faqat mehmonxona
  sozlamasida YOQILGAN bo'lsa (`payment_edit.reception_enabled`), oxirgi
  2 kunlik to'lovlar va bitta to'lovni ko'pi bilan N marta
  (`reception_max_edits`, standart 2). Sozlama o'chiq — qabulxona umuman
  tahrirlay olmaydi. Sanoq to'lovning o'z tarixidan (`method_changes`):
  butun yoki qisman tahrir — bitta; ajralgan qatordagi "qayerdan
  ajralgan" yozuvi tahrir emas. Kim tahrirlagani farq qilmaydi — to'lov
  N marta tahrirlangan bo'lsa qabulxona uchun yopiq.

Har tahrir yozuvida `by_role` saqlanadi — tarixda kim qaysi vazifada
tahrirlagani ko'rinadi (ADMIN, SUPER_ADMIN, MANAGER, RECEPTION).
"""
from __future__ import annotations

from dataclasses import dataclass

PAYMENT_EDIT_SETTINGS_KEY = "payment_edit"

#: Qabulxona uchun bitta to'lovdagi tahrirlar chegarasi — tanlanishi mumkin
ALLOWED_MAX_EDITS = (1, 2, 3)
DEFAULT_PAYMENT_EDIT_SETTINGS = {"reception_enabled": False, "reception_max_edits": 2}

ROLE_ADMIN = "ADMIN"
ROLE_SUPER_ADMIN = "SUPER_ADMIN"
ROLE_MANAGER = "MANAGER"
ROLE_RECEPTION = "RECEPTION"

MANAGER_CODE = "shift.force_close"
RECEPTION_CODE = "finance.payment.create"


def resolve_payment_edit_settings(settings: dict | None) -> dict:
    """Saqlangan sozlamadan to'g'ri qiymatlar (noto'g'ri yoki yo'q — standart)."""
    saved = (settings or {}).get(PAYMENT_EDIT_SETTINGS_KEY) if isinstance(settings, dict) else None
    if not isinstance(saved, dict):
        saved = {}
    enabled = saved.get("reception_enabled")
    max_edits = saved.get("reception_max_edits")
    try:
        max_edits = int(max_edits)
    except (TypeError, ValueError):
        max_edits = DEFAULT_PAYMENT_EDIT_SETTINGS["reception_max_edits"]
    if max_edits not in ALLOWED_MAX_EDITS:
        max_edits = DEFAULT_PAYMENT_EDIT_SETTINGS["reception_max_edits"]
    return {
        "reception_enabled": bool(enabled) if isinstance(enabled, bool) else DEFAULT_PAYMENT_EDIT_SETTINGS["reception_enabled"],
        "reception_max_edits": max_edits,
    }


@dataclass(frozen=True)
class ReceptionEditRules:
    """Qabulxona uchun amaldagi qoida (sozlamadan)."""

    enabled: bool
    max_edits: int

    @classmethod
    def from_settings(cls, settings: dict | None) -> "ReceptionEditRules":
        resolved = resolve_payment_edit_settings(settings)
        return cls(enabled=resolved["reception_enabled"], max_edits=resolved["reception_max_edits"])


#: Qoida berilmaganda (eski chaqiruvlar) — qabulxona tahrirlay olmaydi
RECEPTION_DISABLED = ReceptionEditRules(enabled=False, max_edits=DEFAULT_PAYMENT_EDIT_SETTINGS["reception_max_edits"])


def editor_role(current_user: dict | None) -> str | None:
    """Tahrirlovchining vazifasi; huquqi yo'q bo'lsa None."""
    if not current_user:
        return None
    user_type = current_user.get("user_type")
    if user_type in (ROLE_ADMIN, ROLE_SUPER_ADMIN):
        return user_type
    codes = current_user.get("permissions") or []
    if MANAGER_CODE in codes:
        return ROLE_MANAGER
    if RECEPTION_CODE in codes:
        return ROLE_RECEPTION
    return None


def edit_count(method_changes) -> int:
    """To'lov necha marta tahrirlangan: butun va qisman tahrirlar (eski,
    `kind` siz yozuvlar ham); ajralgan qatorning `split_from` yozuvi emas."""
    if not isinstance(method_changes, list):
        return 0
    return sum(
        1
        for entry in method_changes
        if isinstance(entry, dict) and entry.get("kind") != "split_from"
    )
