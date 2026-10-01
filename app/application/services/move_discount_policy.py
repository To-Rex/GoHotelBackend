"""Xona almashtirishda chegirma — mehmonxona sozlamasidan.

Holat: mehmon kirib, xona yoqmadi va QIMMATROQ xonaga o'tmoqchi. Narx
farqini to'liq olish o'rniga resepshn chegirma qilib bera oladi.

Sozlama `hotels.settings["room_move_discount"]` da:

* `enabled` — xodimlar (resepshn) bu chegirmani bera oladimi. Standart —
  O'CHIQ: sozlama qo'shilishidan oldingi mehmonxonalarda xodim chegirma
  bera olmaydi, ko'chirish avvalgidek ishlaydi.
* `max_percent` — narx FARQIDAN eng ko'p necha foiz (0 — cheklovsiz,
  ya'ni farqning hammasigacha).
* `max_amount` — bir ko'chirishda eng ko'p necha so'm (0 — cheklovsiz).

Bu 0 = "cheklovsiz" kelishuvi bron chegirmasi qoidalari bilan bir xil
(`discount_policy`).

Administrator sozlamaga bog'lanmaydi: qoidani u o'zi belgilaydi va bron
chegirmasini tahrirlash orqali baribir o'zgartira oladi. Lekin uning uchun
ham chegirma narx farqidan oshmaydi — bu chegirma faqat qimmatroq xonaga
o'tish uchun.

Chegirma so'mda saqlanadi (`reservations.move_discount_amount`) va bron
chegirmasidan ALOHIDA: bron chegirmasi foizda bo'lishi mumkin, uni so'mga
aylantirib qo'shib yuborish keyingi qayta hisoblarda (chiqish, hisob-faktura)
foiz ma'nosini buzardi.

"Narx farqi" — mehmon AMALDA qancha ko'p to'laydi: bron chegirmasi foizda
bo'lsa, farq ham shu foizga kamayadi (`net_increase`). Qayta hisoblarda
chegirma boshlang'ich (arzon) xona narxiga nisbatan cheklanadi
(`clamp_move_discount`) — muddat qisqartirilsa ham mehmon o'sha xona
narxidan kam to'lamaydi. Boshlang'ich narx `room_moves` ning oxirgi
yozuvida (`discount_baseline_price`).

Sof funksiyalar — bazaga bog'lanmagan (tests/test_move_discount_policy.py).
"""
from __future__ import annotations

import math

from app.core.exceptions import ValidationException

MOVE_DISCOUNT_SETTINGS_KEY = "room_move_discount"

#: Sozlanmagan mehmonxona: xodim chegirma bera olmaydi (yangi imkoniyat)
MOVE_DISCOUNT_DEFAULTS = {"enabled": False, "max_percent": 0.0, "max_amount": 0.0}

#: Qoidaga bog'lanmaydiganlar
ADMIN_TYPES = ("ADMIN", "SUPER_ADMIN")


def _number(value, maximum: float | None = None) -> float:
    """Manfiy va noto'g'ri qiymatlar 0 ga (cheklovsizga) tushadi."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return 0.0
    if maximum is not None:
        return min(number, maximum)
    return number


def resolve_move_discount_settings(hotel_settings: dict | None) -> dict:
    """Saqlangan sozlamani o'qish. Buzuq yozuv standartga (o'chiq) qaytadi.

    Faqat aniq `True` yoqadi: buzuq yozuv xodimga cheklovsiz chegirma
    ochib qo'ymasligi kerak.
    """
    saved = (hotel_settings or {}).get(MOVE_DISCOUNT_SETTINGS_KEY)
    if not isinstance(saved, dict):
        return dict(MOVE_DISCOUNT_DEFAULTS)
    return {
        "enabled": saved.get("enabled") is True,
        "max_percent": _number(saved.get("max_percent"), 100),
        "max_amount": _number(saved.get("max_amount")),
    }


def is_admin(actor_type: str | None) -> bool:
    return actor_type in ADMIN_TYPES


def _fmt(value: float) -> str:
    """So'm summasi: 150000 -> 150 000."""
    return f"{int(round(value)):,}".replace(",", " ")


def max_move_discount(rule: dict, increase: float, actor_type: str | None) -> int:
    """Shu ko'chirishda berilishi mumkin bo'lgan eng katta chegirma (so'm).

    Pastga yaxlitlanadi (math.floor) — veb ham xuddi shunday hisoblaydi
    (Math.floor), "Maks" tugmasi bosilganda server rad etmasligi uchun.
    Xodimga ruxsat bo'lmasa yoki xona arzonroq/teng bo'lsa — 0.
    """
    diff = _number(increase)
    if diff <= 0:
        return 0
    limit = diff
    if not is_admin(actor_type):
        if not rule.get("enabled"):
            return 0
        percent = _number(rule.get("max_percent"), 100)
        amount = _number(rule.get("max_amount"))
        if percent > 0:
            limit = min(limit, diff * percent / 100)
        if amount > 0:
            limit = min(limit, amount)
    return int(math.floor(limit + 1e-9))


def check_move_discount(
    rule: dict,
    *,
    actor_type: str | None,
    increase: float,
    discount: float | None,
) -> float:
    """Chegirma qoidaga sig'adimi. Sig'sa — butun so'mga yaxlitlangan summa,
    sig'masa — tushunarli xato. Chegirma so'ralmagan bo'lsa — 0.
    """
    requested = _number(discount)
    if requested <= 0:
        return 0.0
    requested = _round_half_up(requested)
    if requested <= 0:
        return 0.0

    diff = _number(increase)
    if diff <= 0:
        raise ValidationException(
            "Chegirma faqat qimmatroq xonaga o'tishda beriladi",
            "MOVE_DISCOUNT_NOT_UPGRADE",
        )

    if not is_admin(actor_type):
        if not rule.get("enabled"):
            raise ValidationException(
                "Xona almashtirishda chegirma berish sozlamalarda o'chirilgan",
                "MOVE_DISCOUNT_DISABLED",
            )
        percent = _number(rule.get("max_percent"), 100)
        amount = _number(rule.get("max_amount"))
        if percent > 0 and requested > math.floor(diff * percent / 100 + 1e-9):
            raise ValidationException(
                f"Chegirma narx farqining {percent:g}% idan oshmasligi kerak "
                f"(ko'pi bilan {_fmt(math.floor(diff * percent / 100 + 1e-9))} so'm)",
                "MOVE_DISCOUNT_MAX_PERCENT",
            )
        if amount > 0 and requested > amount:
            raise ValidationException(
                f"Chegirma {_fmt(amount)} so'mdan oshmasligi kerak",
                "MOVE_DISCOUNT_MAX_AMOUNT",
            )

    if requested > math.floor(diff + 1e-9):
        raise ValidationException(
            f"Chegirma narx farqidan ({_fmt(math.floor(diff + 1e-9))} so'm) "
            "oshmasligi kerak",
            "MOVE_DISCOUNT_EXCEEDS_DIFFERENCE",
        )
    return requested


def _round_half_up(value: float) -> float:
    """Veb `Math.round` bilan bir xil yaxlitlash (Python `round` juftga
    yaxlitlaydi: round(0.5) == 0)."""
    return float(math.floor(value + 0.5))


def net_increase(increase: float, discount_percent=None) -> float:
    """Mehmon AMALDA qancha ko'p to'laydi.

    Bron chegirmasi foizda bo'lsa, u yangi xona narxiga ham qo'llanadi —
    demak farqning o'zi ham shu foizga kamayadi. Chegara shu sof farqdan
    olinmasa, mehmon avvalgi xonadagidan kam to'lab qolardi (100 000 x 2,
    10% — 180 000; 150 000 ga o'tib 100 000 chegirma — 170 000).
    Bron chegirmasi so'mda bo'lsa — farq o'zgarmaydi.
    """
    diff = float(increase or 0)
    percent = _number(discount_percent, 100)
    if percent > 0:
        return diff * (100 - percent) / 100
    return diff


def carry_over_move_discount(previous: float | None, increase: float) -> float:
    """Oldingi ko'chirishlarda berilgan chegirmadan nima qoladi.

    Arzonroq xonaga qaytilsa, narx tushgani avval shu chegirma hisobidan
    yopiladi: 100 000 lik xonadan 150 000 likka 30 000 chegirma bilan
    o'tgan mehmon yana 100 000 likka qaytsa, avvalgi 100 000 ni to'laydi —
    70 000 emas. Narx oshsa yoki teng bo'lsa — chegirma o'zgarmaydi.
    """
    kept = _number(previous)
    diff = float(increase or 0)
    if diff < 0:
        kept = max(0.0, kept + diff)
    return _round_half_up(kept)


#: room_moves yozuvidagi kalit — chegirma qaysi (arzon) xona narxidan
#: boshlab hisoblangani
BASELINE_KEY = "discount_baseline_price"


def discount_baseline(room_moves) -> float | None:
    """Joriy ko'chirish chegirmasining boshlang'ich xona narxi (oxirgi
    yozuvdan). Chegirma yo'q yoki eski yozuv bo'lsa — None."""
    if not isinstance(room_moves, list) or not room_moves:
        return None
    last = room_moves[-1]
    if not isinstance(last, dict) or last.get(BASELINE_KEY) is None:
        return None
    value = _number(last.get(BASELINE_KEY))
    return value if value > 0 else None


def clamp_move_discount(
    move_discount, *, room_charge: float, baseline_charge: float | None, discount_percent=None
) -> float:
    """Qayta hisobda (chiqish, hisob-faktura, ko'chirish) chegirma hech qachon
    joriy xona bilan boshlang'ich xona narxi farqidan oshmaydi.

    Chegirma so'mda saqlanadi va berilgan paytdagi kechalar soni uchun
    o'lchangan. Keyin muddat qisqartirilsa (yoki xona bron tahriri orqali
    almashtirilsa), farq kichrayadi — chegirma ham shunga qisqaradi, mehmon
    boshlang'ich xona narxidan kam to'lamaydi. Muddat uzaytirilsa — chegirma
    o'sha-o'sha qoladi.
    """
    stored = _number(move_discount)
    if stored <= 0:
        return 0.0
    if baseline_charge is None:
        return stored
    cap = net_increase(float(room_charge or 0) - float(baseline_charge or 0), discount_percent)
    return float(min(stored, max(0.0, math.floor(cap + 1e-9))))
