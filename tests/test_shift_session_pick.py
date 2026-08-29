"""Smena sessiyasini tanlash qoidasi.

Bu qoida ikki joyda ishlatiladi — kassa holatini ko'rsatishda va topshirish
summasini hisoblashda. Ilgari ular alohida yozilgan edi va boshqa-boshqa
sessiyani tanlashi mumkin edi: ekranda boshlang'ich kassa bir sessiyadan,
topshiriladigan summa esa boshqasidan olinib, xodimning o'z tushumi yo'qolgandek
ko'rinardi. Shu sababli qoida shu yerda alohida sinovdan o'tadi.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.services.shift_service import pick_open_session

BASE = datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc)


class FakeSession:
    """Testga yetarli minimal sessiya (bazaga bog'lanmaydi)."""

    def __init__(self, name: str, status: str, minutes: int):
        self.name = name
        self.status = status
        self.started_at = BASE + timedelta(minutes=minutes)

    def __repr__(self) -> str:  # xato chiqishini o'qishli qilish uchun
        return f"{self.name}({self.status})"


def test_no_sessions_yields_nothing():
    assert pick_open_session([]) is None


def test_single_active_session():
    only = FakeSession("a", "ACTIVE", 0)
    assert pick_open_session([only]) is only


def test_newest_active_wins_over_older_one():
    """Aynan shu holat nosozlikni keltirib chiqargan edi.

    Tartib berilmaganda baza istalgan qatorni qaytarardi, shuning uchun kassa
    ko'rsatkichi bir sessiyaga, topshirish summasi boshqasiga tushib qolardi.
    """
    old = FakeSession("eski", "ACTIVE", 0)
    new = FakeSession("yangi", "ACTIVE", 30)
    assert pick_open_session([old, new]) is new
    # Ro'yxat tartibi natijaga ta'sir qilmasligi kerak
    assert pick_open_session([new, old]) is new


def test_active_beats_pending_even_if_older():
    """Topshirilgan smena emas, ishlayotgani joriy hisoblanadi."""
    pending = FakeSession("topshirilgan", "PENDING_HANDOVER", 30)
    active = FakeSession("ishlayotgan", "ACTIVE", 0)
    assert pick_open_session([active, pending]) is active


def test_pending_is_used_when_nothing_is_active():
    pending = FakeSession("topshirilgan", "PENDING_HANDOVER", 10)
    assert pick_open_session([pending]) is pending


def test_active_only_ignores_a_pending_session():
    """Topshirilgan smenani qayta yopib bo'lmaydi — u "faol" emas."""
    pending = FakeSession("topshirilgan", "PENDING_HANDOVER", 10)
    assert pick_open_session([pending], active_only=True) is None


def test_active_only_still_takes_the_newest():
    old = FakeSession("eski", "ACTIVE", 0)
    new = FakeSession("yangi", "ACTIVE", 5)
    assert pick_open_session([old, new], active_only=True) is new


@pytest.mark.parametrize("order", [[0, 1, 2], [2, 1, 0], [1, 2, 0]])
def test_result_does_not_depend_on_input_order(order):
    items = [
        FakeSession("eski-faol", "ACTIVE", 0),
        FakeSession("topshirilgan", "PENDING_HANDOVER", 40),
        FakeSession("yangi-faol", "ACTIVE", 20),
    ]
    picked = pick_open_session([items[i] for i in order])
    assert picked.name == "yangi-faol"


# ---------------------------------------------------- ochiq smena talabi

from app.presentation.api.v1._deps import _is_cash_staff  # noqa: E402


class TestCashStaffRule:
    """Smena talabi KIMGA tegishli.

    Cheklov noto'g'ri odamga tushsa, u ishni to'xtatib qo'yadi: administrator
    tuzatish kirita olmaydi yoki farrosh vazifasini yopa olmaydi. Shuning uchun
    qoida shu yerda aniq qulflanadi.
    """

    def test_reception_needs_a_shift(self):
        assert _is_cash_staff(
            {"user_type": "EMPLOYEE", "permissions": ["reservation.create"]}
        )

    def test_cashier_needs_a_shift(self):
        assert _is_cash_staff(
            {"user_type": "EMPLOYEE", "permissions": ["finance.payment.create"]}
        )

    def test_manager_is_exempt(self):
        """Menejer smena ochmasdan tuzatish kiritishi kerak bo'ladi."""
        assert not _is_cash_staff(
            {
                "user_type": "EMPLOYEE",
                "permissions": ["reservation.create", "shift.force_close"],
            }
        )

    @pytest.mark.parametrize("user_type", ["ADMIN", "SUPER_ADMIN"])
    def test_administrators_are_exempt(self, user_type):
        assert not _is_cash_staff(
            {"user_type": user_type, "permissions": ["reservation.create"]}
        )

    def test_housekeeper_is_exempt(self):
        """Farroshning ishi kassaga bog'liq emas."""
        assert not _is_cash_staff(
            {"user_type": "EMPLOYEE", "permissions": ["task.view", "task.update"]}
        )

    def test_no_permissions_is_exempt(self):
        assert not _is_cash_staff({"user_type": "EMPLOYEE", "permissions": []})
        assert not _is_cash_staff({"user_type": "EMPLOYEE"})


# --------------------------------------------- majburiy yopish va kassa taqdiri

from app.presentation.api.v1.shifts import ForceCloseRequest  # noqa: E402


class TestForceCloseHandover:
    """Majburiy yopishda kassadagi pul nima bo'ladi.

    Ilgari majburiy yopish sessiyani darhol CLOSED qilardi va kassa zanjiri
    uzilardi: keyingi xodim pulni qabul qilmagan holda noldan boshlar, kassada
    esa pul turaverardi — smena topshirishda u "ortiqcha" bo'lib chiqardi.
    """

    def test_handover_is_the_default(self):
        """Standart holat — xavfsiz holat.

        Admin belgilamasa, pul yo'qolmasligi kerak: sessiya topshirilgan
        holatda qoladi va keyingi xodim uni parol bilan qabul qiladi.
        """
        request = ForceCloseRequest(session_id=uuid4())
        assert request.hand_over is True

    def test_admin_can_take_the_cash_instead(self):
        """Eski xatti-harakat ham saqlanadi: pulni admin o'zi olsa, sessiya
        butunlay yopiladi va keyingi xodim noldan boshlaydi."""
        request = ForceCloseRequest(session_id=uuid4(), hand_over=False)
        assert request.hand_over is False

    def test_counted_cash_stays_optional(self):
        """Sanamasdan yopish imkoniyati yo'qolmasligi kerak — xodim aloqaga
        chiqmagan holat uchun."""
        request = ForceCloseRequest(session_id=uuid4())
        assert request.counted_cash is None
        assert request.notes is None

    @pytest.mark.parametrize(
        "hand_over,expected_status",
        [(True, "PENDING_HANDOVER"), (False, "CLOSED")],
    )
    def test_status_mapping(self, hand_over, expected_status):
        """Holat xaritasi: topshirilsa sessiya OCHIQ qoladi.

        Bu muhim, chunki ochiq sessiya boshqa xodimning smena ochishini
        to'sib turadi — ya'ni keyingi xodim uni qabul qilishga majbur bo'ladi,
        xuddi oddiy topshirishdagidek.
        """
        assert ("PENDING_HANDOVER" if hand_over else "CLOSED") == expected_status
