"""Do'kon savdosini to'lash — to'liq yoki QISMAN, bir yoki bir necha usulda.

Bronga yozilgan savdo (PENDING) istalgancha marta qisman to'lanadi: har
to'lovda bir usul (masalan naqd) yoki bir nechta (naqd + karta). Qoldiq
qolgan ekan savdo PENDING — qarz sifatida ko'rinadi; to'liq to'langanda
PAID va `paid_at` qo'yiladi.

Pul qachon va kim tomonidan olingani `shop_sale_payments` da saqlanadi va
do'kon tushumi, kassa, xodimlar kesimi va shaxsiy hisobot SHU jadvaldan
hisoblanadi: qisman to'lov o'sha kuni va o'sha xodim kassasiga tushadi.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.core.exceptions import ValidationException
from app.infrastructure.database.models.shop import ShopSale, ShopSalePayment

#: Yaxlitlash farqi (so'mning kasri) — to'liq to'langan deb hisoblanadi
EPSILON = 0.01

MIXED = "MIXED"


def _num(value) -> float:
    return float(value or 0)


def remaining_of(sale: ShopSale) -> float:
    """To'lanmagan qoldiq (manfiy bo'lmaydi)."""
    return max(_num(sale.total_amount) - _num(getattr(sale, "paid_amount", 0)), 0.0)


def summarize_method(parts: list[dict]) -> str | None:
    """Barcha bo'laklar bitta usulda bo'lsa — o'sha, aks holda MIXED."""
    methods = {p.get("payment_method") for p in parts if p.get("payment_method")}
    if not methods:
        return None
    return methods.pop() if len(methods) == 1 else MIXED


def validate_parts(parts: list[dict], limit: float) -> float:
    """Bo'laklar musbat va jami `limit` dan oshmaydi. Jami summani qaytaradi."""
    if not parts:
        raise ValidationException("To'lov summasi kiritilmagan", "SHOP_PAYMENT_EMPTY")
    total = 0.0
    for part in parts:
        amount = _num(part.get("amount"))
        if amount <= 0:
            raise ValidationException("To'lov summasi 0 dan katta bo'lsin", "SHOP_PAYMENT_INVALID")
        total += amount
    if total > limit + EPSILON:
        raise ValidationException(
            f"To'lov ({total:.0f}) qoldiqdan ({limit:.0f}) oshib ketdi",
            "SHOP_PAYMENT_EXCEEDS_REMAINING",
        )
    return total


def apply_payment(
    session,
    sale: ShopSale,
    parts: list[dict],
    *,
    user_id,
    user_name: str | None,
    now: datetime,
) -> list[ShopSalePayment]:
    """Savdoga to'lov qo'shadi (qisman yoki to'liq).

    `parts` — [{"amount", "payment_method"}, ...]. Har bo'lak alohida
    `ShopSalePayment` qatori bo'ladi; `paid_amount`, holat, usul va
    `payments` (chek va tarix uchun JSON) yangilanadi.
    """
    total_paid = validate_parts(parts, remaining_of(sale))
    rows = [
        ShopSalePayment(
            hotel_id=sale.hotel_id,
            sale_id=sale.id,
            amount=Decimal(str(round(_num(p["amount"]), 2))),
            payment_method=p.get("payment_method"),
            paid_at=now,
            created_by=user_id,
        )
        for p in parts
    ]
    for row in rows:
        session.add(row)

    history = list(sale.payments or [])
    for p in parts:
        history.append({
            "amount": round(_num(p["amount"]), 2),
            "payment_method": p.get("payment_method"),
            "paid_at": now.isoformat(),
            "created_by": str(user_id),
            "created_by_name": user_name,
        })
    sale.paid_amount = Decimal(str(round(_num(sale.paid_amount) + total_paid, 2)))
    fully_paid = remaining_of(sale) <= EPSILON
    sale.payment_method = summarize_method(history)
    if fully_paid and len(history) == 1:
        # Bir martada bitta usulda to'liq to'langan — avvalgi shakl
        # (payments = null), chek va hisobotlar avvalgidek
        sale.payments = None
    else:
        sale.payments = history
    if fully_paid:
        sale.status = "PAID"
        sale.paid_at = now
    return rows
