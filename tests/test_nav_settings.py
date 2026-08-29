"""Yon menyu tartibi.

Tartib mehmonxona sozlamasida saqlanadi va o'sha mehmonxonaning barcha
xodimlariga amal qiladi, ya'ni bitta yozuv butun jamoaning menyusini
belgilaydi. Shuning uchun u yerga tushadigan qiymat qat'iy tozalanishi kerak:
buzuq yozuv hammaning menyusini buzadi.
"""
from app.presentation.api.v1.hotels import NAV_SETTINGS_KEY, _resolve_nav


def test_no_saved_order_is_not_an_error():
    """Sozlanmagan mehmonxona standart tartibda ishlaydi."""
    assert _resolve_nav(None) == {"order": []}
    assert _resolve_nav({}) == {"order": []}
    assert _resolve_nav({NAV_SETTINGS_KEY: {}}) == {"order": []}


def test_saved_order_survives_a_round_trip():
    saved = {NAV_SETTINGS_KEY: {"order": ["/booking", "/rooms", "/"]}}
    assert _resolve_nav(saved) == {"order": ["/booking", "/rooms", "/"]}


def test_broken_values_are_dropped_not_rejected():
    """Menyu har doim chizilishi kerak — buzuq band tashlanadi, xolos."""
    saved = {NAV_SETTINGS_KEY: {"order": ["/a", 5, None, {"x": 1}, "nisbiy", "/b"]}}
    assert _resolve_nav(saved) == {"order": ["/a", "/b"]}


def test_duplicates_are_removed():
    """Takrorlangan manzil saralashda ikki xil o'rin bermasligi kerak."""
    saved = {NAV_SETTINGS_KEY: {"order": ["/a", "/b", "/a"]}}
    assert _resolve_nav(saved) == {"order": ["/a", "/b"]}


def test_order_is_capped():
    """Cheksiz ro'yxat sozlamalar JSONB'sini shishirmasin."""
    saved = {NAV_SETTINGS_KEY: {"order": [f"/p{i}" for i in range(500)]}}
    assert len(_resolve_nav(saved)["order"]) == 100


def test_a_wrong_shape_falls_back_to_default():
    for broken in ("/a", 42, {"order": {"a": 1}}):
        assert _resolve_nav({NAV_SETTINGS_KEY: broken if isinstance(broken, dict) else {"order": broken}}) == {
            "order": []
        }


def test_other_hotel_settings_are_untouched():
    """Tartib boshqa sozlamalar (smena, chek, skaner) yonida yashaydi."""
    settings = {"shift": {"mode": "cash"}, NAV_SETTINGS_KEY: {"order": ["/a"]}}
    new_settings = dict(settings)
    new_settings[NAV_SETTINGS_KEY] = _resolve_nav({NAV_SETTINGS_KEY: {"order": ["/b"]}})
    assert new_settings["shift"] == {"mode": "cash"}
    assert _resolve_nav(new_settings) == {"order": ["/b"]}


class TestBookingDefaults:
    """Yangi bandlov dialogining standart turi.

    Bu ham mehmonxona bo'yicha bitta yozuv — buzuq qiymat butun jamoaning
    bron oynasini ishlamas holga keltirmasligi kerak.
    """

    def test_unset_hotel_keeps_the_daily_default(self):
        from app.presentation.api.v1.hotels import _resolve_booking

        assert _resolve_booking(None) == {"default_type": "DAILY"}
        assert _resolve_booking({}) == {"default_type": "DAILY"}

    def test_hourly_is_stored_and_returned(self):
        from app.presentation.api.v1.hotels import BOOKING_SETTINGS_KEY, _resolve_booking

        saved = {BOOKING_SETTINGS_KEY: {"default_type": "HOURLY"}}
        assert _resolve_booking(saved) == {"default_type": "HOURLY"}

    def test_an_unknown_type_falls_back_instead_of_breaking(self):
        from app.presentation.api.v1.hotels import BOOKING_SETTINGS_KEY, _resolve_booking

        for broken in ("WEEKLY", "", None, 5, ["HOURLY"]):
            saved = {BOOKING_SETTINGS_KEY: {"default_type": broken}}
            assert _resolve_booking(saved) == {"default_type": "DAILY"}

    def test_a_wrong_shape_does_not_raise(self):
        """Bazaga qo'lda yozilgan noto'g'ri shakl 500 bermasligi kerak."""
        from app.presentation.api.v1.hotels import (
            BOOKING_SETTINGS_KEY,
            NAV_SETTINGS_KEY,
            _resolve_booking,
            _resolve_nav,
        )

        assert _resolve_booking({BOOKING_SETTINGS_KEY: "HOURLY"}) == {"default_type": "DAILY"}
        assert _resolve_nav({NAV_SETTINGS_KEY: "/rooms"}) == {"order": []}

    def test_other_settings_are_untouched(self):
        from app.presentation.api.v1.hotels import BOOKING_SETTINGS_KEY, _resolve_booking

        settings = {"shift": {"mode": "cash"}, "nav": {"order": ["/rooms"]}}
        new_settings = dict(settings)
        new_settings[BOOKING_SETTINGS_KEY] = _resolve_booking(
            {BOOKING_SETTINGS_KEY: {"default_type": "HOURLY"}}
        )
        assert new_settings["shift"] == {"mode": "cash"}
        assert new_settings["nav"] == {"order": ["/rooms"]}
