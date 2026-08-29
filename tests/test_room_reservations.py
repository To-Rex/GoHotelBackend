"""Xona bandlovlari ro'yxati.

Bu ro'yxat xona kartochkasidagi tugma orqasida turadi va uni bron ko'rish
ruxsati bo'lgan har qanday xodim ochadi. Shuning uchun ikki narsa muhim:
so'rov xonani mehmonxona bo'yicha tekshirishi (begona xonaning ID'sini
yozib qo'yish bilan uning tarixi ochilmasligi) va javob mehmon ismini o'zi
bilan olib kelishi (mehmonlar bazasini alohida yuklash shart bo'lmasligi).
"""
import inspect

import pytest

from app.application.dto.room import RoomReservationResponse
from app.application.services.room_service import RoomService
from app.presentation.api.v1.rooms import router


class TestEndpoint:
    def test_route_is_registered(self):
        paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
        assert ("/{room_id}/reservations", ("GET",)) in paths

    def test_the_room_wildcard_does_not_swallow_this_path(self):
        """`/{room_id}` oldin ro'yxatdan o'tgan — u bu manzilga tushmasligi kerak.

        Marshrutlar ro'yxat tartibida tekshiriladi, ya'ni oldingi `/{room_id}`
        "abc/reservations" ni ham xona ID'si deb qabul qilsa, bu endpoint
        hech qachon chaqirilmasdi.
        """
        wildcard = next(r for r in router.routes if r.path == "/{room_id}")
        assert wildcard.path_regex.match("/abc") is not None
        assert wildcard.path_regex.match("/abc/reservations") is None

    def test_requires_the_reservation_view_permission(self):
        """Xona ruxsati emas, aynan BRON ko'rish ruxsati talab qilinadi."""
        endpoint = next(
            r.endpoint for r in router.routes if r.path == "/{room_id}/reservations"
        )
        source = inspect.getsource(endpoint)
        assert 'require_permission("reservation.view")' in source


class TestResponseShape:
    def test_guest_name_travels_with_the_reservation(self):
        fields = RoomReservationResponse.model_fields
        assert "guest_name" in fields
        assert "guest_phone" in fields

    def test_money_fields_are_present(self):
        fields = RoomReservationResponse.model_fields
        for name in ("total_amount", "paid_amount", "discount_amount", "payment_status"):
            assert name in fields

    def test_guest_name_may_be_missing(self):
        """Mehmoni o'chirilgan eski bron ham ro'yxatda ko'rinishi kerak."""
        assert RoomReservationResponse.model_fields["guest_name"].default is None
        assert RoomReservationResponse.model_fields["guest_phone"].default is None


class TestService:
    def test_a_room_from_another_hotel_is_not_readable(self):
        """Xona so'ralgan mehmonxonaga tegishli bo'lmasa — topilmadi.

        Tekshiruv bronlarni o'qishdan OLDIN bo'lishi shart: aks holda begona
        xonaning ID'sini yozib qo'yish bilan uning mehmonlari ko'rinib qolardi.
        """
        import asyncio
        from uuid import uuid4

        from app.core.exceptions import NotFoundException

        class _Result:
            def scalar_one_or_none(self):
                return None

            def all(self):  # pragma: no cover — bu yergacha yetmasligi kerak
                raise AssertionError("Xona tekshirilmasdan bronlar o'qildi")

        class _Session:
            async def execute(self, *_args, **_kwargs):
                return _Result()

        service = RoomService.__new__(RoomService)
        service.session = _Session()

        with pytest.raises(NotFoundException):
            asyncio.run(service.get_room_reservations(uuid4(), uuid4()))

    def test_newest_reservation_comes_first(self):
        """Ro'yxat kirish sanasi bo'yicha teskari tartibda — oxirgi mehmon tepada."""
        source = inspect.getsource(RoomService.get_room_reservations)
        assert "check_in_date.desc()" in source
        assert "created_at.desc()" in source

    def test_deleted_reservations_are_skipped(self):
        source = inspect.getsource(RoomService.get_room_reservations)
        assert "is_deleted.is_(False)" in source
