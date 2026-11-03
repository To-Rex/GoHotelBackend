"""Mahalliy sana: server UTC da, mehmonxona esa +05:00 (APP_TZ_OFFSET_MINUTES).

Muammo: to'lov/hisob-faktura/jarima sanasi `date.today()` bilan yozilardi.
Toshkent vaqti bilan 00:00–05:00 oralig'ida bu hali KECHAGI sana — tungi
to'lov kechagi hisobotga tushar, moliya sahifasida "Bugun" uni ko'rsatmas,
"Kecha" esa ortiqcha ko'rsatardi. Do'kon savdosi (vaqt belgisi) ham UTC
yarim tun bo'yicha kesilardi. Endi hammasi `hotel_today` /
`local_day_bounds` / `local_date_of` orqali mahalliy kun bo'yicha.

Bazasiz.
"""
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.shared.utils import hotel_today, local_date_of, local_day_bounds


@pytest.fixture(autouse=True)
def tashkent(monkeypatch):
    monkeypatch.setattr(settings, "APP_TZ_OFFSET_MINUTES", 300)


def test_hotel_today_after_midnight_is_already_the_new_day():
    # 1-noyabr 21:30 UTC = 2-noyabr 02:30 Toshkent — bugun 2-noyabr
    assert hotel_today(datetime(2026, 11, 1, 21, 30, tzinfo=timezone.utc)) == date(2026, 11, 2)
    # Kunduzi farq yo'q
    assert hotel_today(datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)) == date(2026, 11, 2)
    # 18:59 UTC — hali 23:59 mahalliy, 1-noyabr
    assert hotel_today(datetime(2026, 11, 1, 18, 59, tzinfo=timezone.utc)) == date(2026, 11, 1)


def test_hotel_today_defaults_to_now():
    assert isinstance(hotel_today(), date)


def test_local_day_bounds_cover_the_local_night():
    start, end = local_day_bounds(date(2026, 11, 1), date(2026, 11, 1))
    # Mahalliy 1-noyabr 00:00 = 31-oktabr 19:00 UTC; ertasi 00:00 = 1-noyabr 19:00 UTC
    assert start == datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 1, 19, 0, tzinfo=timezone.utc)
    # 1-noyabr 02:30 (mahalliy) = 31-oktabr 21:30 UTC — oraliqqa KIRADI
    night = datetime(2026, 10, 31, 21, 30, tzinfo=timezone.utc)
    assert start <= night < end
    # 2-noyabr 00:30 (mahalliy) = 1-noyabr 19:30 UTC — kirmaydi
    assert not (start <= datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc) < end)


def test_local_day_bounds_open_ended():
    assert local_day_bounds(None, None) == (None, None)
    start, end = local_day_bounds(date(2026, 11, 1), None)
    assert start is not None and end is None
    start, end = local_day_bounds(None, date(2026, 11, 1))
    assert start is None and end == datetime(2026, 11, 1, 19, 0, tzinfo=timezone.utc)


def test_local_date_of_uses_hotel_day_and_treats_naive_as_utc():
    assert local_date_of(datetime(2026, 10, 31, 21, 30, tzinfo=timezone.utc)) == date(2026, 11, 1)
    # SQLite zonasiz qaytaradi — UTC deb olinadi
    assert local_date_of(datetime(2026, 10, 31, 21, 30)) == date(2026, 11, 1)
    # Boshqa zonadagi vaqt ham to'g'ri keltiriladi (+03:00 dagi 23:30 = 20:30 UTC = 01:30 Toshkent)
    from datetime import timedelta, timezone as tz

    assert local_date_of(datetime(2026, 10, 31, 23, 30, tzinfo=tz(timedelta(hours=3)))) == date(2026, 11, 1)
    # Allaqachon sana bo'lsa o'zgarmaydi
    assert local_date_of(date(2026, 10, 31)) == date(2026, 10, 31)


def test_no_business_date_is_stamped_with_server_today():
    """Hujjat sanasi yoziladigan joylarda `date.today()` qolmagan bo'lsin —
    aks holda tungi to'lov yana kechaga tushadi."""
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"^(?!\s*#).*\b(\w+_date)\s*=\s*[^#\n]*\bdate\.today\(\)", text, re.MULTILINE
        ):
            offenders.append(f"{path.relative_to(root)}: {match.group(0).strip()}")
    assert offenders == [], "\n".join(offenders)


def test_server_today_is_not_used_outside_allowlist():
    """`date.today()` (server UTC kuni) ilovada umuman qolmasin — hujjat
    raqami, sana, muddat: hammasi `hotel_today()` orqali. Istisno — hujjat
    amal qilish muddatini tekshiruvchi OCR (bir necha soat farqi ahamiyatsiz)
    va `hotel_today` ning o'zi haqidagi izohlar."""
    root = Path(__file__).resolve().parents[1] / "app"
    allowed = {
        "application/services/document_ocr/verify.py",
        "application/services/document_ocr/mrz.py",
        "shared/utils.py",  # faqat izoh/hujjatlashda tilga olinadi
    }
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Izoh va backtick ichidagi eslatma (`date.today()` haqidagi izoh) kod emas
            code = re.sub(r"`[^`]*`", "", line.split("#", 1)[0])
            if re.search(r"\bdate\.today\(\)|\butcnow\(\)\.date\(\)|\bdatetime\.now\([^)]*\)\.date\(\)", code):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_utils_only_mentions_server_today_in_prose():
    """`shared/utils.py` da `date.today()` kod sifatida emas, faqat izohda."""
    path = Path(__file__).resolve().parents[1] / "app" / "shared" / "utils.py"
    code_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if "date.today()" in line.split("#", 1)[0] and not line.strip().startswith(("#", "*", '"""'))
    ]
    # docstring ichidagi qatorlar: `hotel_today` izohi ("`date.today()` emas")
    assert all("`date.today()`" in line for line in code_lines), code_lines
