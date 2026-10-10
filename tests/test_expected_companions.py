"""Kechikib keladigan hamrohlar — joy band, kelganda biriktiriladi (bazasiz).

Qoidalar (companion_ops):
* kutilayotgan hamroh joy egallaydi: ichkaridagilar + kutilayotganlar
  mehmonlar sonidan oshmaydi;
* "Keldi" (expected_id) — o'sha yozuv haqiqiy hamrohga aylanadi;
* umumiy "Hamroh qo'shish" joy faqat kutilayotganlar hisobiga band bo'lsa
  kelgan odamni kutilgan hamroh deb oladi; bo'sh joy bo'lsa — oddiy hamroh;
* majburiy rejimda kutilayotgan hamroh "hisobga olingan".
"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.application.services import companion_ops as ops
from app.core.exceptions import NotFoundException, ValidationException
from tests.test_reservation_companions import _service

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
STAFF = uuid4()
MAIN = uuid4()


def _expected(n=1):
    out = []
    for i in range(n):
        out = ops.add_expected([], out, adults=10, name=f"Hamroh {i}", at=NOW, by=STAFF)
    return out


def test_expected_entry_shape_and_cleaning():
    entry = ops.new_expected_entry(name="  Bobur  ", phone="", note=None, at=NOW, by=STAFF)
    assert entry["name"] == "Bobur" and entry["phone"] is None and entry["note"] is None
    assert len(entry["id"]) == 32 and entry["created_by"] == str(STAFF)


def test_expected_takes_a_seat():
    # 2 kishilik bron: asosiy mehmon + 1 kutilayotgan = to'la
    expected = ops.add_expected([], [], adults=2, at=NOW, by=STAFF)
    assert ops.expected_count(expected) == 1
    with pytest.raises(ValidationException) as err:
        ops.add_expected([], expected, adults=2, at=NOW, by=STAFF)
    assert err.value.error_code == "ROOM_GUESTS_FULL"


def test_arrived_by_expected_id_becomes_companion():
    expected = ops.add_expected([], [], adults=2, name="Bobur", at=NOW, by=STAFF)
    guest = uuid4()
    companions, waiting, consumed = ops.attach_companion(
        [], expected, guest_id=guest, name="Bobur Aliyev", main_guest_id=MAIN,
        adults=2, at=NOW, by=STAFF, expected_id=expected[0]["id"],
    )
    assert waiting == [] and consumed["name"] == "Bobur"
    assert companions[0]["guest_id"] == str(guest)
    assert companions[0]["arrived_late"] is True
    assert companions[0]["expected_since"] == expected[0]["created_at"]


def test_generic_add_consumes_expected_only_when_room_is_full():
    # To'la (joy kutilayotgan hisobiga) — kelgan odam kutilgan hamroh
    expected = ops.add_expected([], [], adults=2, at=NOW, by=STAFF)
    companions, waiting, consumed = ops.attach_companion(
        [], expected, guest_id=uuid4(), name="X", main_guest_id=MAIN,
        adults=2, at=NOW, by=STAFF,
    )
    assert consumed is not None and waiting == [] and companions[0]["arrived_late"] is True

    # Bo'sh joy bor — oddiy hamroh, kutish saqlanadi
    expected = ops.add_expected([], [], adults=3, at=NOW, by=STAFF)
    companions, waiting, consumed = ops.attach_companion(
        [], expected, guest_id=uuid4(), name="Y", main_guest_id=MAIN,
        adults=3, at=NOW, by=STAFF,
    )
    assert consumed is None and len(waiting) == 1 and "arrived_late" not in companions[0]


def test_unknown_expected_id_and_remove():
    with pytest.raises(NotFoundException):
        ops.attach_companion(
            [], [], guest_id=uuid4(), name="X", main_guest_id=MAIN,
            adults=2, at=NOW, by=STAFF, expected_id="nope",
        )
    expected = _expected(2)
    rest = ops.remove_expected(expected, expected[0]["id"])
    assert [e["id"] for e in rest] == [expected[1]["id"]]
    with pytest.raises(NotFoundException) as err:
        ops.remove_expected(rest, "nope")
    assert err.value.error_code == "EXPECTED_NOT_FOUND"


def test_create_counts_expected_in_capacity_and_required_mode():
    known = {uuid4()}
    service = _service(known, hotel_settings={"booking": {"require_all_guests": True}})
    # 3 kishi: asosiy + 1 hamroh + 1 kechikib keladi — majburiy rejimda yetarli
    companions = asyncio.run(
        service._resolve_companions(
            list(known), main_guest_id=MAIN, adults=3, hotel_id=uuid4(), expected_count=1
        )
    )
    assert len(companions) == 1
    # Kutilayotgan ham joy egallaydi — sig'imdan oshsa xato
    with pytest.raises(ValidationException) as err:
        asyncio.run(
            service._resolve_companions(
                list(known), main_guest_id=MAIN, adults=2, hotel_id=uuid4(), expected_count=1
            )
        )
    assert err.value.error_code == "TOO_MANY_COMPANIONS"
    # Kutilayotgansiz majburiy rejim avvalgidek talab qiladi
    with pytest.raises(ValidationException) as err:
        asyncio.run(
            service._resolve_companions(
                list(known), main_guest_id=MAIN, adults=3, hotel_id=uuid4()
            )
        )
    assert err.value.error_code == "GUESTS_REQUIRED"
