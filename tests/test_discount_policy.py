"""Chegirma qoidalari.

Qoidani administrator belgilaydi, qolganlar shu doirada ishlaydi. Tekshiruv
serverda: brauzerdagi maydonni chetlab o'tib qoidadan oshirib bo'lmasligi
kerak, aks holda sozlamaning ma'nosi qolmaydi.
"""
import pytest

from app.core.exceptions import ValidationException
from app.application.services.discount_policy import (
    DISCOUNT_SETTINGS_KEY,
    check_discount,
    resolve_discount_rules,
    rule_for,
)


def rules(**overrides) -> dict:
    """Faqat soatlik qoidasi belgilangan sozlama."""
    return {DISCOUNT_SETTINGS_KEY: {"hourly": overrides}}


class TestDefaults:
    def test_an_unconfigured_hotel_keeps_working_as_before(self):
        """Sozlamagan mehmonxonada chegirma avvalgidek cheklovsiz."""
        resolved = resolve_discount_rules(None)
        for key in ("daily", "hourly"):
            assert resolved[key]["enabled"] is True
            assert resolved[key]["max_percent"] == 0
            assert resolved[key]["max_amount"] == 0

    def test_no_limits_means_any_discount_passes(self):
        check_discount(None, "HOURLY", 1, 100_000, 0, 90)
        check_discount(None, "DAILY", 30, 100_000, 99_000, 0)

    def test_a_wrong_shape_does_not_raise(self):
        assert resolve_discount_rules({DISCOUNT_SETTINGS_KEY: "yoq"})["daily"]["enabled"]
        assert resolve_discount_rules({DISCOUNT_SETTINGS_KEY: {"daily": 5}})["daily"]

    def test_negative_and_broken_limits_fall_back_to_unlimited(self):
        resolved = resolve_discount_rules(
            rules(max_percent=-10, max_amount="ko'p", min_duration=None)
        )
        assert resolved["hourly"]["max_percent"] == 0
        assert resolved["hourly"]["max_amount"] == 0
        assert resolved["hourly"]["min_duration"] == 0

    def test_percent_cannot_exceed_a_hundred(self):
        assert resolve_discount_rules(rules(max_percent=500))["hourly"]["max_percent"] == 100


class TestNoDiscount:
    def test_a_booking_without_a_discount_is_never_blocked(self):
        """Qoida qanchalik qattiq bo'lmasin, chegirmasiz bron o'tadi."""
        strict = rules(enabled=False, max_percent=0, min_duration=99)
        check_discount(strict, "HOURLY", 1, 100_000, 0, 0)


class TestDisabled:
    def test_a_discount_is_refused_when_switched_off(self):
        with pytest.raises(ValidationException) as excinfo:
            check_discount(rules(enabled=False), "HOURLY", 3, 100_000, 0, 5)
        assert excinfo.value.error_code == "DISCOUNT_DISABLED"

    def test_the_other_booking_type_is_untouched(self):
        """Soatlik yopilsa kunlik ochiq qoladi — ular alohida sozlanadi."""
        check_discount(rules(enabled=False), "DAILY", 3, 100_000, 0, 5)


class TestDuration:
    def test_a_short_booking_can_be_refused(self):
        with pytest.raises(ValidationException) as excinfo:
            check_discount(rules(min_duration=2), "HOURLY", 1, 100_000, 0, 5)
        assert excinfo.value.error_code == "DISCOUNT_MIN_DURATION"
        # Xabar aniq sonlarni aytadi — xodim nima qilishni bilishi kerak
        assert "2 soat" in excinfo.value.detail

    def test_the_threshold_itself_is_allowed(self):
        check_discount(rules(min_duration=2), "HOURLY", 2, 100_000, 0, 5)

    def test_a_long_booking_can_be_refused(self):
        with pytest.raises(ValidationException) as excinfo:
            check_discount(rules(max_duration=2), "HOURLY", 3, 100_000, 0, 5)
        assert excinfo.value.error_code == "DISCOUNT_MAX_DURATION"

    def test_daily_duration_is_measured_in_nights(self):
        settings = {DISCOUNT_SETTINGS_KEY: {"daily": {"min_duration": 3}}}
        with pytest.raises(ValidationException) as excinfo:
            check_discount(settings, "DAILY", 2, 100_000, 0, 5)
        assert "kecha" in excinfo.value.detail


class TestLimits:
    def test_percent_above_the_limit_is_refused(self):
        with pytest.raises(ValidationException) as excinfo:
            check_discount(rules(max_percent=10), "HOURLY", 3, 100_000, 0, 20)
        assert excinfo.value.error_code == "DISCOUNT_MAX_PERCENT"

    def test_the_limit_itself_is_allowed(self):
        check_discount(rules(max_percent=10), "HOURLY", 3, 100_000, 0, 10)

    def test_a_sum_that_exceeds_the_percent_limit_is_refused(self):
        """Foizda chegara qo'yilgan bo'lsa, so'mda kiritish bilan chetlab
        o'tib bo'lmasligi kerak."""
        with pytest.raises(ValidationException) as excinfo:
            check_discount(rules(max_percent=10), "HOURLY", 3, 100_000, 50_000, 0)
        assert excinfo.value.error_code == "DISCOUNT_MAX_PERCENT"

    def test_a_percent_that_exceeds_the_sum_limit_is_refused(self):
        """Aksincha ham: summada chegara bo'lsa, foiz orqali oshirib
        bo'lmaydi."""
        with pytest.raises(ValidationException) as excinfo:
            check_discount(rules(max_amount=10_000), "HOURLY", 3, 100_000, 0, 50)
        assert excinfo.value.error_code == "DISCOUNT_MAX_AMOUNT"

    def test_both_limits_apply_together(self):
        strict = rules(max_percent=20, max_amount=10_000)
        # 15% = 15 000 so'm — foizga sig'adi, summaga sig'maydi
        with pytest.raises(ValidationException):
            check_discount(strict, "HOURLY", 3, 100_000, 0, 15)
        # 5% = 5 000 so'm — ikkalasiga ham sig'adi
        check_discount(strict, "HOURLY", 3, 100_000, 0, 5)

    def test_a_zero_charge_does_not_crash(self):
        """Narxi 0 bo'lgan xona — nolga bo'lish bo'lmasligi kerak."""
        check_discount(rules(max_percent=10), "HOURLY", 3, 0, 5_000, 0)


class TestWiring:
    def test_creation_checks_the_policy(self):
        """Tekshiruv bron yaratishda — bu sozlamaning yagona tayanchi."""
        import inspect

        from app.application.services.reservation_service import ReservationService

        source = inspect.getsource(ReservationService.create_reservation)
        assert "check_discount(" in source

    def test_a_room_move_does_not_recheck(self):
        """Mavjud bronning chegirmasi ko'chirishda qayta tekshirilmaydi.

        U yaratilganda ruxsat etilgan edi; ko'chirishda davomiylik o'zgarib
        qoidaga tushmay qolishi mumkin va bu mavjud bronni qulflab qo'yardi.
        """
        import inspect

        from app.application.services.reservation_service import ReservationService

        source = inspect.getsource(ReservationService.move_room)
        assert "check_discount(" not in source

    def test_the_rule_is_picked_by_booking_type(self):
        settings = {
            DISCOUNT_SETTINGS_KEY: {
                "daily": {"max_percent": 5},
                "hourly": {"max_percent": 50},
            }
        }
        assert rule_for(settings, "DAILY")["max_percent"] == 5
        assert rule_for(settings, "HOURLY")["max_percent"] == 50
        # Notanish tur kunlik deb qaraladi
        assert rule_for(settings, "WEEKLY")["max_percent"] == 5
