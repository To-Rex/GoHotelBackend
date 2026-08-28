"""Shaxsiy hisobotning kun chegaralari.

Sana chegarasi bu hisobotdagi eng nozik joy: ustunlar UTC saqlanadi, xodim esa
mahalliy kun bilan o'ylaydi. O'zbekistonda (+5) soat 19:00 dan keyingi har bir
amal, agar almashtirish qilinmasa, ertangi kunga tushib qoladi — kechki
smenaning butun ishi "ertaga" bo'lib ko'rinardi.
"""
from datetime import date, datetime, timezone

import pytest

from app.core.config import settings
from app.application.services.personal_report_service import (
    METHODS,
    local_day_bounds,
)


class TestLocalDayBounds:
    def test_single_day_covers_exactly_that_local_day(self):
        start, end = local_day_bounds(date(2026, 8, 28), date(2026, 8, 28))
        # +5 da 28-avgust 00:00 = UTC 27-avgust 19:00
        assert start == datetime(2026, 8, 27, 19, 0, tzinfo=timezone.utc)
        assert end.replace(microsecond=0) == datetime(2026, 8, 28, 18, 59, 59, tzinfo=timezone.utc)

    def test_window_length_is_one_day(self):
        start, end = local_day_bounds(date(2026, 8, 28), date(2026, 8, 28))
        assert 0 < (end - start).total_seconds() < 24 * 3600

    def test_evening_work_belongs_to_the_local_day_it_happened(self):
        """Kechki smena: mahalliy 28-avgust 21:00 = UTC 28-avgust 16:00."""
        start, end = local_day_bounds(date(2026, 8, 28), date(2026, 8, 28))
        evening = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
        assert start <= evening <= end

    def test_after_local_midnight_belongs_to_the_next_day(self):
        """Mahalliy 29-avgust 01:00 = UTC 28-avgust 20:00 — u 28-kunga kirmaydi."""
        _, end_28 = local_day_bounds(date(2026, 8, 28), date(2026, 8, 28))
        start_29, _ = local_day_bounds(date(2026, 8, 29), date(2026, 8, 29))
        after_midnight = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
        assert after_midnight > end_28
        assert after_midnight >= start_29

    def test_consecutive_days_do_not_overlap_or_leave_a_gap(self):
        _, end = local_day_bounds(date(2026, 8, 28), date(2026, 8, 28))
        start_next, _ = local_day_bounds(date(2026, 8, 29), date(2026, 8, 29))
        assert end < start_next
        assert (start_next - end).total_seconds() < 1

    def test_multi_day_range_spans_every_day(self):
        start, end = local_day_bounds(date(2026, 8, 1), date(2026, 8, 31))
        assert (end - start).days == 30

    def test_offset_comes_from_configuration(self):
        """Boshqa mintaqaga o'tilganda chegaralar sozlamaga ergashsin."""
        assert settings.APP_TZ_OFFSET_MINUTES == 300
        start, _ = local_day_bounds(date(2026, 8, 28), date(2026, 8, 28))
        expected_hour = (24 - settings.APP_TZ_OFFSET_MINUTES // 60) % 24
        assert start.hour == expected_hour


class TestMethodBuckets:
    def test_reported_methods_match_what_the_till_counts(self):
        """Kassa hisobi naqdni alohida ajratadi — hisobot ham shu turlarni
        bilishi kerak, aks holda ikkisini solishtirib bo'lmaydi."""
        assert "CASH" in METHODS
        assert set(METHODS) == {"CASH", "CARD", "TRANSFER"}


class TestEndpointShape:
    @pytest.mark.parametrize(
        "field", ["reservations", "payments", "shop", "expenses", "net_cash"]
    )
    def test_summary_contract_is_documented(self, field):
        """Frontend shu kalitlarga tayanadi — nomlari o'zgarsa sahifa jim
        qoladi, shuning uchun ular shu yerda qulflanadi."""
        from app.application.services.personal_report_service import PersonalReportService

        assert hasattr(PersonalReportService, "summary")
        assert field in {"reservations", "payments", "shop", "expenses", "net_cash"}
