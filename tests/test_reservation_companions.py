"""Xonadagi hamrohlarni ro'yxatga olish.

Bir xonaga bir necha kishi joylashsa, qolganlari ham mehmon sifatida
kiritiladi. Bu yerdagi qoidalar ikki narsani himoya qiladi: bir odam ikki
marta sanalmasligi va xonaga sig'maydigan ro'yxat jimgina saqlanib
qolmasligi.
"""
import asyncio
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NotFoundException, ValidationException
from app.application.services.reservation_service import (
    ReservationService,
    require_all_guests,
)


class _Guest:
    def __init__(self, first_name="Ali", last_name="Valiyev"):
        self.first_name = first_name
        self.last_name = last_name


class _GuestRepo:
    """Berilgan ID'lardan boshqasini topmaydigan soxta ombor."""

    def __init__(self, known: set[UUID], guest: _Guest | None = None):
        self.known = known
        self.guest = guest or _Guest()

    async def get_by_id_unscoped(self, gid):
        return self.guest if gid in self.known else None


class _Session:
    def __init__(self, hotel_settings=None):
        self._settings = hotel_settings

    async def get(self, _model, _pk):
        class _Hotel:
            settings = self._settings

        return _Hotel()


def _service(known: set[UUID], hotel_settings=None) -> ReservationService:
    service = ReservationService.__new__(ReservationService)
    service.session = _Session(hotel_settings)
    service.guest_repo = _GuestRepo(known)
    return service


def _resolve(service, ids, main, adults):
    return asyncio.run(
        service._resolve_companions(ids, main_guest_id=main, adults=adults, hotel_id=uuid4())
    )


class TestRequireAllGuestsSetting:
    def test_default_is_not_mandatory(self):
        """Sozlama yoqilmaguncha eski xatti-harakat saqlanadi."""
        assert require_all_guests(None) is False
        assert require_all_guests({}) is False
        assert require_all_guests({"booking": {}}) is False

    def test_enabled_when_switched_on(self):
        assert require_all_guests({"booking": {"require_all_guests": True}}) is True

    def test_a_wrong_shape_does_not_raise(self):
        assert require_all_guests({"booking": "HOURLY"}) is False
        assert require_all_guests({"booking": {"require_all_guests": "ha"}}) is False


class TestCompanions:
    def test_empty_list_is_allowed_by_default(self):
        service = _service(set())
        assert _resolve(service, [], uuid4(), 1) == []

    def test_the_main_guest_is_not_counted_twice(self):
        """Asosiy mehmon hamrohlar ro'yxatiga tushib qolsa — tashlanadi."""
        main = uuid4()
        service = _service({main})
        assert _resolve(service, [main], main, 2) == []

    def test_duplicates_are_dropped(self):
        main, friend = uuid4(), uuid4()
        service = _service({friend})
        result = _resolve(service, [friend, friend], main, 3)
        assert len(result) == 1
        assert result[0]["guest_id"] == str(friend)

    def test_the_guest_name_travels_with_the_reservation(self):
        """Ism bronda saqlanadi — ro'yxatni ko'rsatish uchun qayta so'rov shart emas."""
        main, friend = uuid4(), uuid4()
        service = _service({friend})
        result = _resolve(service, [friend], main, 2)
        assert result[0]["name"] == "Ali Valiyev"

    def test_more_companions_than_guests_is_rejected(self):
        """3 kishilik xonaga 4 kishi yozib bo'lmaydi."""
        main = uuid4()
        friends = [uuid4() for _ in range(3)]
        service = _service(set(friends))
        with pytest.raises(ValidationException):
            _resolve(service, friends, main, 3)

    def test_an_unknown_guest_is_rejected(self):
        """Bazada yo'q hamroh — bron yaratilmaydi."""
        service = _service(set())
        with pytest.raises(NotFoundException):
            _resolve(service, [uuid4()], uuid4(), 2)

    def test_a_broken_id_is_rejected(self):
        service = _service(set())
        with pytest.raises(ValidationException):
            _resolve(service, ["bu-uuid-emas"], uuid4(), 2)


class TestMandatoryMode:
    SETTINGS = {"booking": {"require_all_guests": True}}

    def test_all_guests_must_be_registered(self):
        """3 kishi bo'lsa 3 tasi ham kiritilishi kerak."""
        main, friend = uuid4(), uuid4()
        service = _service({friend}, self.SETTINGS)
        with pytest.raises(ValidationException) as excinfo:
            _resolve(service, [friend], main, 3)
        assert "3" in str(excinfo.value.detail)

    def test_a_complete_list_passes(self):
        main = uuid4()
        friends = [uuid4(), uuid4()]
        service = _service(set(friends), self.SETTINGS)
        assert len(_resolve(service, friends, main, 3)) == 2

    def test_a_single_guest_room_needs_nothing_extra(self):
        service = _service(set(), self.SETTINGS)
        assert _resolve(service, [], uuid4(), 1) == []

    def test_optional_mode_accepts_an_incomplete_list(self):
        """Sozlama o'chirilgan bo'lsa yarim ro'yxat ham qabul qilinadi."""
        main, friend = uuid4(), uuid4()
        service = _service({friend})
        assert len(_resolve(service, [friend], main, 3)) == 1
