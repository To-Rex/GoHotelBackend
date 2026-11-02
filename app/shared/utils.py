import secrets
import string
from datetime import date, datetime, time, timedelta, timezone


def generate_code(prefix: str, hotel_code: str) -> str:
    today = date.today().strftime("%Y%m%d")
    suffix = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4)
    )
    return f"{prefix}-{hotel_code}-{today}-{suffix}"


def generate_jti() -> str:
    return secrets.token_hex(32)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hotel_today(now: datetime | None = None) -> date:
    """Mehmonxonaning MAHALLIY sanasi (APP_TZ_OFFSET_MINUTES, standart +05:00).

    Server UTC da ishlaydi, shuning uchun `date.today()` Toshkent vaqti
    bilan 00:00–05:00 oralig'ida hali KECHAGI sanani qaytaradi. Tunda
    qabul qilingan to'lov, hisob-faktura yoki jarima kechagi kunga yozilib
    qolardi: moliya sahifasida "Bugun" ularni ko'rsatmas, "Kecha" esa
    ortiqcha ko'rsatardi. Hujjatga sana yoziladigan HAMMA joy shu
    funksiyani ishlatadi — `date.today()` emas. `now` — testlar uchun.
    """
    from app.core.config import settings  # aylanma importga yo'l qo'ymaslik uchun

    current = now or datetime.now(timezone.utc)
    return (current + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)).date()


def local_date_of(moment: datetime) -> date:
    """Vaqt belgisining MAHALLIY sanasi (qaysi kunga tegishli).

    Zonasiz qiymat UTC deb olinadi — SQLite (testlar) vaqtni shunday
    qaytaradi, PostgreSQL esa zonali.
    """
    from app.core.config import settings

    if not isinstance(moment, datetime):
        # Allaqachon sana (masalan, testdagi o'rinbosar) — o'zgarmaydi
        return moment
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    shifted = moment.astimezone(timezone.utc) + timedelta(
        minutes=settings.APP_TZ_OFFSET_MINUTES
    )
    return shifted.date()


def local_day_bounds(
    day_from: date | None, day_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """Mahalliy kunlar oralig'ining UTC chegaralari: [start, end).

    Vaqt belgisi (timestamptz) ustunlarini kun bo'yicha filtrlash uchun:
    `>= start` va `< end`. `end` — oxirgi kunning ERTASI 00:00 (mahalliy),
    shuning uchun 23:59:59.999999 kabi yaxlitlash xatosi bo'lmaydi.
    Oddiy `datetime.combine(day, time.min)` UTC yarim tunni berardi —
    mahalliy 00:00–05:00 dagi harakatlar oldingi kunga tushib qolardi.
    """
    from app.core.config import settings

    offset = timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
    start = (
        datetime.combine(day_from, time.min, tzinfo=timezone.utc) - offset
        if day_from
        else None
    )
    end = (
        datetime.combine(day_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        - offset
        if day_to
        else None
    )
    return start, end
