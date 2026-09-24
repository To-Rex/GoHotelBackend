"""Turish davomida hamrohlar: ketdi / qaytdi / o'rniga yangisi keldi.

Bron yaratilgandan keyin xonadagilar o'zgaradi. Qoidalar: yozuv o'chirilmaydi
(tarix), ichkaridagilar mehmonlar sonidan oshmaydi, bir odam ro'yxatda ikki
marta turmaydi, kirish ro'yxati o'zgartirilmaydi (JSONB o'zgarishni faqat
yangi obyektda sezadi).
"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundException, ValidationException
from app.application.services import companion_ops as ops
from app.application.services.reservation_service import ReservationService

NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
STAFF = uuid4()


def _entry(guest_id=None, **extra):
    return {"guest_id": str(guest_id or uuid4()), "name": "Ali Valiyev", **extra}


def _add(companions, guest_id, *, main, adults, name=None):
    return ops.add_companion(
        companions,
        guest_id=guest_id,
        name=name,
        main_guest_id=main,
        adults=adults,
        at=NOW,
        by=STAFF,
    )


class TestPresence:
    def test_legacy_entry_without_stamps_is_present(self):
        """Eski yozuvlar (faqat guest_id + name) — ichkarida deb hisoblanadi."""
        assert ops.is_present({"guest_id": "x", "name": None}) is True

    def test_left_entry_is_not_present(self):
        assert ops.is_present({"guest_id": "x", "left_at": NOW.isoformat()}) is False

    def test_present_count_ignores_departed(self):
        companions = [_entry(), _entry(left_at=NOW.isoformat()), _entry()]
        assert ops.present_count(companions) == 2

    def test_none_and_empty(self):
        assert ops.present_count(None) == 0
        assert ops.present_count([]) == 0
        assert ops.find_companion(None, uuid4()) is None


class TestLeave:
    def test_marks_left_and_keeps_the_entry(self):
        friend = uuid4()
        result = ops.mark_left([_entry(friend)], friend, at=NOW, by=STAFF)
        assert len(result) == 1
        assert result[0]["left_at"] == NOW.isoformat()
        assert result[0]["left_by"] == str(STAFF)
        assert result[0]["name"] == "Ali Valiyev"

    def test_input_is_not_mutated(self):
        friend = uuid4()
        companions = [_entry(friend)]
        result = ops.mark_left(companions, friend, at=NOW, by=STAFF)
        assert "left_at" not in companions[0]
        assert result is not companions

    def test_unknown_companion(self):
        with pytest.raises(NotFoundException) as e:
            ops.mark_left([_entry()], uuid4(), at=NOW, by=STAFF)
        assert e.value.error_code == "COMPANION_NOT_FOUND"

    def test_twice_is_rejected(self):
        friend = uuid4()
        once = ops.mark_left([_entry(friend)], friend, at=NOW, by=STAFF)
        with pytest.raises(ValidationException) as e:
            ops.mark_left(once, friend, at=NOW, by=STAFF)
        assert e.value.error_code == "COMPANION_ALREADY_LEFT"

    def test_accepts_uuid_or_string_ids(self):
        friend = uuid4()
        result = ops.mark_left([_entry(friend)], str(friend), at=NOW, by=STAFF)
        assert not ops.is_present(result[0])


class TestReturn:
    def test_clears_the_left_mark(self):
        friend = uuid4()
        left = ops.mark_left([_entry(friend)], friend, at=NOW, by=STAFF)
        back = ops.mark_returned(left, friend, adults=2)
        assert ops.is_present(back[0])
        assert "left_by" not in back[0]

    def test_not_left(self):
        friend = uuid4()
        with pytest.raises(ValidationException) as e:
            ops.mark_returned([_entry(friend)], friend, adults=2)
        assert e.value.error_code == "COMPANION_NOT_LEFT"

    def test_seat_already_taken_by_a_replacement(self):
        """Ketgan o'rniga boshqasi kirgan — "ketdi"ni bekor qilib bo'lmaydi."""
        main, old, new = uuid4(), uuid4(), uuid4()
        companions = ops.mark_left([_entry(old)], old, at=NOW, by=STAFF)
        companions = _add(companions, new, main=main, adults=2)
        with pytest.raises(ValidationException) as e:
            ops.mark_returned(companions, old, adults=2)
        assert e.value.error_code == "ROOM_GUESTS_FULL"


class TestAdd:
    def test_replacement_takes_the_freed_seat(self):
        """2 kishilik xona: hamroh ketdi — o'rniga boshqasi keladi."""
        main, old, new = uuid4(), uuid4(), uuid4()
        companions = ops.mark_left([_entry(old)], old, at=NOW, by=STAFF)
        result = _add(companions, new, main=main, adults=2, name="Vali Aliyev")
        assert [c["guest_id"] for c in result] == [str(old), str(new)]
        assert result[1]["name"] == "Vali Aliyev"
        assert result[1]["added_at"] == NOW.isoformat()
        assert result[1]["added_by"] == str(STAFF)
        # ketgan yozuv tarix uchun qoladi, lekin ichkarida faqat yangisi
        assert ops.present_count(result) == 1

    def test_room_full(self):
        """2 kishilik xonada asosiy mehmon + 1 hamroh bor — uchinchi sig'maydi."""
        main, friend = uuid4(), uuid4()
        with pytest.raises(ValidationException) as e:
            _add([_entry(friend)], uuid4(), main=main, adults=2)
        assert e.value.error_code == "ROOM_GUESTS_FULL"

    def test_empty_seat_in_a_bigger_room(self):
        """3 kishilik bronda 1 hamroh bor — ikkinchisi keyin qo'shiladi."""
        result = _add([_entry()], uuid4(), main=uuid4(), adults=3)
        assert len(result) == 2

    def test_main_guest_is_rejected(self):
        main = uuid4()
        with pytest.raises(ValidationException) as e:
            _add(None, main, main=main, adults=2)
        assert e.value.error_code == "COMPANION_IS_MAIN_GUEST"

    def test_already_present(self):
        main, friend = uuid4(), uuid4()
        with pytest.raises(ValidationException) as e:
            _add([_entry(friend)], friend, main=main, adults=3)
        assert e.value.error_code == "COMPANION_ALREADY_PRESENT"

    def test_departed_guest_comes_back_as_the_same_entry(self):
        """Ketgan hamroh qaytsa ro'yxatda ikki marta turmaydi."""
        main, friend = uuid4(), uuid4()
        left = ops.mark_left([_entry(friend)], friend, at=NOW, by=STAFF)
        result = _add(left, friend, main=main, adults=2, name="Ali Valiyev")
        assert len(result) == 1
        assert ops.is_present(result[0])
        assert "left_by" not in result[0]
        assert result[0]["returned_at"] == NOW.isoformat()
        assert result[0]["returned_by"] == str(STAFF)

    def test_single_room_has_no_seat(self):
        with pytest.raises(ValidationException) as e:
            _add(None, uuid4(), main=uuid4(), adults=1)
        assert e.value.error_code == "ROOM_GUESTS_FULL"

    def test_adults_zero_is_treated_as_one(self):
        with pytest.raises(ValidationException) as e:
            _add(None, uuid4(), main=uuid4(), adults=0)
        assert e.value.error_code == "ROOM_GUESTS_FULL"

    def test_input_is_not_mutated(self):
        companions = [_entry()]
        _add(companions, uuid4(), main=uuid4(), adults=3)
        assert len(companions) == 1


class TestRemove:
    def test_removes_the_entry(self):
        a, b = uuid4(), uuid4()
        result = ops.remove_companion([_entry(a), _entry(b)], a)
        assert [c["guest_id"] for c in result] == [str(b)]

    def test_unknown(self):
        with pytest.raises(NotFoundException):
            ops.remove_companion([_entry()], uuid4())


# ----------------------------------------------------------- servis qatlami --


class _Guest:
    first_name = "Vali"
    last_name = "Aliyev"


class _Reservation:
    def __init__(self, status="CHECKED_IN", companions=None, adults=2):
        self.id = uuid4()
        self.guest_id = uuid4()
        self.status = status
        self.companions = companions
        self.adults = adults
        self.checkout_requested_at = None


class _Repo:
    def __init__(self, reservation):
        self.reservation = reservation

    async def get_by_id(self, rid, hotel_id):
        return self.reservation if rid == self.reservation.id else None


class _GuestRepo:
    def __init__(self, known):
        self.known = known

    async def get_by_id_unscoped(self, gid):
        return _Guest() if gid in self.known else None


class _Session:
    def __init__(self):
        self.flushed = 0
        self.refreshed = 0

    async def flush(self):
        self.flushed += 1

    async def refresh(self, obj):
        self.refreshed += 1


def _service(reservation, known=()):
    service = ReservationService.__new__(ReservationService)
    service.session = _Session()
    service.repo = _Repo(reservation)
    service.guest_repo = _GuestRepo(set(known))
    return service


class TestServiceFlow:
    def test_leave_needs_a_checked_in_reservation(self):
        friend = uuid4()
        res = _reservation = _Reservation("CONFIRMED", [_entry(friend)])
        service = _service(res)
        with pytest.raises(ValidationException) as e:
            asyncio.run(service.companion_leave(res.id, uuid4(), friend, STAFF))
        assert e.value.error_code == "INVALID_STATUS"

    def test_leave_then_replacement(self):
        """Hamroh ketdi, o'rniga yangisi kirdi — saqlandi va yangilandi."""
        old, new = uuid4(), uuid4()
        res = _Reservation("CHECKED_IN", [_entry(old)], adults=2)
        original = res.companions
        service = _service(res, known={new})

        asyncio.run(service.companion_leave(res.id, uuid4(), old, STAFF))
        assert res.companions is not original  # JSONB uchun yangi obyekt
        assert not ops.is_present(res.companions[0])

        asyncio.run(service.add_companion(res.id, uuid4(), new, STAFF))
        assert [c["guest_id"] for c in res.companions] == [str(old), str(new)]
        assert res.companions[1]["name"] == "Vali Aliyev"
        assert service.session.flushed == 2
        assert service.session.refreshed == 2

    def test_add_before_check_in_is_allowed(self):
        new = uuid4()
        res = _Reservation("CONFIRMED", None, adults=2)
        service = _service(res, known={new})
        asyncio.run(service.add_companion(res.id, uuid4(), new, STAFF))
        assert len(res.companions) == 1

    def test_add_unknown_guest(self):
        res = _Reservation("CHECKED_IN", None, adults=2)
        service = _service(res)
        with pytest.raises(NotFoundException) as e:
            asyncio.run(service.add_companion(res.id, uuid4(), uuid4(), STAFF))
        assert e.value.error_code == "COMPANION_NOT_FOUND"

    def test_add_during_checkout_is_rejected(self):
        """Chiqish boshlangan — farroshga vazifa ketgan, yangi odam joylashmaydi."""
        new = uuid4()
        res = _Reservation("CHECKED_IN", None, adults=2)
        res.checkout_requested_at = NOW
        service = _service(res, known={new})
        with pytest.raises(ValidationException) as e:
            asyncio.run(service.add_companion(res.id, uuid4(), new, STAFF))
        assert e.value.error_code == "CHECKOUT_IN_PROGRESS"
        # ketishni belgilash esa mumkin — xonadagilar haqiqatga mos qolsin
        friend = uuid4()
        res.companions = [_entry(friend)]
        asyncio.run(service.companion_leave(res.id, uuid4(), friend, STAFF))
        assert not ops.is_present(res.companions[0])

    def test_return_is_capacity_checked(self):
        """Ketgan o'rniga yangisi kirgan — "qaytdi" xonani to'ldirib yubormaydi."""
        old, new = uuid4(), uuid4()
        res = _Reservation("CHECKED_IN", [_entry(old)], adults=2)
        service = _service(res, known={new})
        asyncio.run(service.companion_leave(res.id, uuid4(), old, STAFF))
        asyncio.run(service.add_companion(res.id, uuid4(), new, STAFF))
        with pytest.raises(ValidationException) as e:
            asyncio.run(service.companion_return(res.id, uuid4(), old, STAFF))
        assert e.value.error_code == "ROOM_GUESTS_FULL"

    def test_add_after_check_out_is_rejected(self):
        new = uuid4()
        res = _Reservation("CHECKED_OUT", None, adults=2)
        service = _service(res, known={new})
        with pytest.raises(ValidationException) as e:
            asyncio.run(service.add_companion(res.id, uuid4(), new, STAFF))
        assert e.value.error_code == "INVALID_STATUS"

    def test_remove_only_before_check_in(self):
        friend = uuid4()
        res = _Reservation("CHECKED_IN", [_entry(friend)])
        service = _service(res)
        with pytest.raises(ValidationException) as e:
            asyncio.run(service.remove_companion(res.id, uuid4(), friend, STAFF))
        assert e.value.error_code == "INVALID_STATUS"

    def test_remove_last_companion_stores_none(self):
        """Bo'sh ro'yxat None bo'lib saqlanadi — yaratilgandagi bilan bir xil."""
        friend = uuid4()
        res = _Reservation("CONFIRMED", [_entry(friend)])
        service = _service(res)
        asyncio.run(service.remove_companion(res.id, uuid4(), friend, STAFF))
        assert res.companions is None

    def test_unknown_reservation(self):
        res = _Reservation()
        service = _service(res)
        with pytest.raises(NotFoundException):
            asyncio.run(service.companion_return(uuid4(), uuid4(), uuid4(), STAFF))
