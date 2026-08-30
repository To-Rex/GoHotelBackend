"""Chegirma qoidalari — mehmonxona sozlamasidan.

Administrator qoidani belgilaydi, qolgan xodimlar shu doirada ishlaydi.
Qoida `hotels.settings["discount"]` da saqlanadi va bron turi bo'yicha
alohida bo'ladi: kunlik bronda o'lchov KECHA, soatlikda esa SOAT.

Barcha chegaralarda 0 — "cheklov yo'q" degani. Bu ilovadagi mavjud kelishuv
(masalan bron tahriri oynasi ham shunday), shuning uchun yangi kalit
qo'shilganda eski mehmonxonalar avvalgidek ishlab ketaveradi: sozlanmagan
mehmonxonada chegirma hech qanday chegarasiz.

Tekshiruv bron YARATILAYOTGANDA qo'llanadi. Xonani ko'chirishda chegirma
qayta hisoblanadi, lekin qayta TEKSHIRILMAYDI: u allaqachon ruxsat etilgan
edi va ko'chirish paytida davomiylik o'zgarib qoidaga tushmay qolishi
mumkin — bu mavjud bronni qulflab qo'yardi.
"""
from __future__ import annotations

from app.core.exceptions import ValidationException

DISCOUNT_SETTINGS_KEY = "discount"

#: Bron turlari va ularning o'lchov birligi (xato matnida ishlatiladi)
DURATION_UNITS = {"daily": "kecha", "hourly": "soat"}

#: Sozlanmagan mehmonxona: chegirma ochiq va cheklovsiz (eski xatti-harakat)
DEFAULT_RULE = {
    "enabled": True,
    "max_percent": 0.0,
    "max_amount": 0.0,
    "min_duration": 0.0,
    "max_duration": 0.0,
}


def _number(value, maximum: float | None = None) -> float:
    """Manfiy va noto'g'ri qiymatlar 0 ga (cheklovsizga) tushadi."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number < 0:  # NaN yoki manfiy
        return 0.0
    if maximum is not None:
        return min(number, maximum)
    return number


def resolve_discount_rules(hotel_settings: dict | None) -> dict:
    """Saqlangan qoidalarni o'qish. Buzuq yozuv standartga qaytadi."""
    saved = (hotel_settings or {}).get(DISCOUNT_SETTINGS_KEY)
    if not isinstance(saved, dict):
        saved = {}
    rules = {}
    for key in DURATION_UNITS:
        raw = saved.get(key)
        if not isinstance(raw, dict):
            raw = {}
        rules[key] = {
            # Faqat ATAYLAB o'chirilgan bo'lsa yopiladi
            "enabled": raw.get("enabled") is not False,
            "max_percent": _number(raw.get("max_percent"), 100),
            "max_amount": _number(raw.get("max_amount")),
            "min_duration": _number(raw.get("min_duration")),
            "max_duration": _number(raw.get("max_duration")),
        }
    return rules


def rule_for(hotel_settings: dict | None, booking_type: str) -> dict:
    rules = resolve_discount_rules(hotel_settings)
    return rules["hourly" if str(booking_type).upper() == "HOURLY" else "daily"]


def _fmt(value: float) -> str:
    """Butun son butun ko'rinishda yozilsin (2.0 emas, 2)."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def check_discount(
    hotel_settings: dict | None,
    booking_type: str,
    duration: float,
    room_charge: float,
    discount_amount: float,
    discount_percent: float,
) -> None:
    """Chegirma qoidaga sig'adimi. Sig'masa — tushunarli xato.

    `duration` — kunlikda kecha soni, soatlikda soat soni (narx hisobidan
    keladi, ya'ni bron qanday saqlangan bo'lsa shunday).
    """
    amount = _number(discount_amount)
    percent = _number(discount_percent, 100)
    if amount <= 0 and percent <= 0:
        return  # chegirma yo'q — tekshiradigan narsa ham yo'q

    rule = rule_for(hotel_settings, booking_type)
    unit = DURATION_UNITS["hourly" if str(booking_type).upper() == "HOURLY" else "daily"]

    if not rule["enabled"]:
        raise ValidationException(
            "Bu mehmonxonada chegirma berish o'chirilgan", "DISCOUNT_DISABLED"
        )

    if rule["min_duration"] > 0 and duration < rule["min_duration"]:
        raise ValidationException(
            f"Chegirma kamida {_fmt(rule['min_duration'])} {unit}dan boshlab "
            f"beriladi (bu bron — {_fmt(duration)} {unit})",
            "DISCOUNT_MIN_DURATION",
        )

    if rule["max_duration"] > 0 and duration > rule["max_duration"]:
        raise ValidationException(
            f"Chegirma ko'pi bilan {_fmt(rule['max_duration'])} {unit}lik bronga "
            f"beriladi (bu bron — {_fmt(duration)} {unit})",
            "DISCOUNT_MAX_DURATION",
        )

    # Ikki chegara ham bir xil o'lchovga keltiriladi: xodim foizda kiritsa
    # ham, so'mda kiritsa ham ikkala chegara ishlashi kerak
    charge = _number(room_charge)
    effective_amount = round(charge * percent / 100) if percent > 0 else amount
    effective_percent = (
        percent if percent > 0 else (amount / charge * 100 if charge > 0 else 0.0)
    )

    if rule["max_percent"] > 0 and effective_percent > rule["max_percent"] + 0.001:
        raise ValidationException(
            f"Chegirma {_fmt(rule['max_percent'])}% dan oshmasligi kerak "
            f"(kiritilgan: {effective_percent:.1f}%)",
            "DISCOUNT_MAX_PERCENT",
        )

    if rule["max_amount"] > 0 and effective_amount > rule["max_amount"] + 0.001:
        raise ValidationException(
            f"Chegirma {_fmt(rule['max_amount'])} so'mdan oshmasligi kerak "
            f"(kiritilgan: {_fmt(effective_amount)} so'm)",
            "DISCOUNT_MAX_AMOUNT",
        )
