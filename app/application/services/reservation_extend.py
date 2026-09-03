"""Bronni cho'zish qoidalari.

Mehmon qolishni so'raganda administrator bronni uzaytiradi. Ikki chegara
bor va ular qat'iy:

1. XONA. Cho'zish shu xonadagi KEYINGI bron boshlanishigacha mumkin.
   Keyingi bron bo'lmasa cheklov ham yo'q. Bu qoida shu yerda, serverda
   turadi: brauzer nimani ko'rsatishidan qat'i nazar, ikkita bron
   ustma-ust tushib qolmasligi kerak.

2. YO'NALISH. Faqat CHO'ZISH mumkin, qisqartirish emas. Qisqartirish
   "to'langan pulning bir qismi qaytariladimi" degan savolni ochadi —
   bekor qilishdagi kabi alohida qoida kerak bo'ladi. Cho'zishda esa
   bunday savol yo'q, chunki QO'SHIMCHA HAQ OLINMAYDI.

Modul ataylab toza: bu yerda baza ham, so'rov ham yo'q — faqat vaqt
oralig'i ustidagi hisob. Shuning uchun qoidalarni bazasiz sinash mumkin.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

#: Xonani haqiqatan band qiladigan holatlar. Qolganlari (bekor qilingan,
#: chiqib ketgan, kelmagan) cho'zishga to'sqinlik qilmaydi — mehmon erta
#: chiqib ketgan bo'lsa xona bo'sh turadi.
BLOCKING_STATUSES = ("CONFIRMED", "CHECKED_IN")

#: Tugagan bronni cho'zishning ma'nosi yo'q.
LOCKED_STATUSES = ("CHECKED_OUT", "CANCELLED", "NO_SHOW")


class ExtendError(Exception):
    """Cho'zib bo'lmadi — sabab kodi bilan."""

    def __init__(self, code: str, limit: datetime | None = None):
        super().__init__(code)
        self.code = code
        self.limit = limit


@dataclass(frozen=True)
class Span:
    """Bron egallagan vaqt oralig'i."""

    start: datetime
    end: datetime
    hourly: bool


def _wall(value: datetime) -> datetime:
    """Vaqt zonasini olib tashlaydi.

    Loyihada `check_in_datetime` va `check_out_datetime` xodim TERGAN
    vaqtni saqlaydi, ya'ni ular devor soati. Ustun turi `timezone=True`
    bo'lgani uchun bazadan zona bilan qaytadi; taqqoslashda esa `date`
    dan yasalgan zonasiz paytlar ham qatnashadi. Ikkisini aralashtirish
    TypeError beradi, shuning uchun hammasi zonasiz ko'rinishga
    keltiriladi.
    """
    return value.replace(tzinfo=None) if value.tzinfo else value


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min)


def span_of(reservation) -> Span:
    """Bronning vaqt oralig'i.

    Kunlik bron kun boshidan joy egallaydi — taxtadagi ko'rinish ham
    shunday. Soatlik bron esa o'z aniq vaqtidan.
    """
    hourly = (
        reservation.booking_type == "HOURLY"
        and reservation.check_in_datetime is not None
        and reservation.check_out_datetime is not None
    )
    if hourly:
        return Span(
            start=_wall(reservation.check_in_datetime),
            end=_wall(reservation.check_out_datetime),
            hourly=True,
        )
    return Span(
        start=_day_start(reservation.check_in_date),
        end=_day_start(reservation.check_out_date),
        hourly=False,
    )


def extension_limit(
    current: Span, others: list[Span], turnover_minutes: int
) -> datetime | None:
    """Bronni eng ko'pi bilan qaysi paytgacha cho'zish mumkin.

    `None` — shu xonada keyingi bron yo'q, ya'ni cheklov ham yo'q.

    Soatlik bronda keyingi bron oldidan tozalash tanaffusi qoldiriladi
    (yangi bron yaratishdagi qoida bilan bir xil). Kunlik bronda tanaffus
    ayirilmaydi: chiqish kuni keyingi mehmonning kirish kuni bo'la oladi
    va bron yaratishda ham shunday hisoblanadi.
    """
    later = [o.start for o in others if o.start >= current.end]
    if not later:
        return None
    nearest = min(later)
    if current.hourly:
        return nearest - timedelta(minutes=turnover_minutes)
    return nearest


def assert_extendable(status: str) -> None:
    if status in LOCKED_STATUSES:
        raise ExtendError("RESERVATION_LOCKED")


def validate_new_end(
    current: Span, new_end: datetime, limit: datetime | None
) -> None:
    """Yangi tugash vaqti qoidalarga mos keladimi."""
    new_end = _wall(new_end)
    if new_end <= current.end:
        # Qisqartirish bu yerda emas — moduldagi izohga qarang
        raise ExtendError("NOT_AN_EXTENSION")
    if limit is not None and new_end > limit:
        raise ExtendError("EXTENSION_BLOCKED", limit)


def checkout_date_for(check_in_date: date, new_end: datetime, hourly: bool) -> date:
    """Yangi `check_out_date` qiymati.

    Soatlik bronda bu ustun hisobot uchun emas, bazadagi
    `check_out_date > check_in_date` cheklovi uchun ishlatiladi: bir kun
    ichidagi bronda u kirish kunidan bir kun keyin turadi. Shu qoida
    bron yaratishda ham qo'llanadi, cho'zishda ham buzilmasligi kerak.
    """
    day = new_end.date()
    if hourly and day <= check_in_date:
        return check_in_date + timedelta(days=1)
    return day
