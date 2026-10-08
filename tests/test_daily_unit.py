"""Kunlik bron hisobi: 12 soatlik (standart, avvalgidek) yoki 24 soatlik.

Foydalanuvchi talabi: hozirgi holat — 12 soatlik rejim (bugun + ertaga
tanlansa 1 kecha); sozlamada 24 soatlik rejim ham bo'lsin (bugun + ertaga —
2 kun, narx 2 barobar). Server narxni avvalgidek kechalar soni bo'yicha
hisoblaydi — rejim faqat saqlanadi va veb kalendari shunga qarab sanalarni
quradi.
"""
import asyncio
import uuid
from types import SimpleNamespace

from app.presentation.api.v1 import hotels as hotels_api
from tests._branch_fakes import BranchResult, fake_branch, is_branch_query

KEY = hotels_api.BOOKING_SETTINGS_KEY
HOTEL = uuid.uuid4()
CONFIGURATOR = {"user_type": "ADMIN", "actual_user_type": "CONFIGURATOR", "hotel_id": HOTEL}


class Session:
    def __init__(self, settings=None):
        self.hotel = SimpleNamespace(id=HOTEL, settings=settings or {})
        # Sozlamalar filialda — mehmonxonaning yagona filiali
        self.branch = fake_branch(HOTEL, self.hotel.settings)

    async def get(self, model, key):
        return self.hotel if key == HOTEL else None

    async def execute(self, statement, *_a, **_k):
        assert is_branch_query(statement), str(statement)
        return BranchResult(self.branch)

    async def flush(self):
        return None


def test_default_is_12h_as_before():
    assert hotels_api._resolve_booking(None)["daily_unit"] == "12h"
    assert hotels_api._resolve_booking({})["daily_unit"] == "12h"
    assert hotels_api._resolve_booking({KEY: {"default_type": "HOURLY"}})["daily_unit"] == "12h"


def test_broken_value_falls_back_to_12h():
    for value in ("24", "", None, 24, "day"):
        assert hotels_api._resolve_booking({KEY: {"daily_unit": value}})["daily_unit"] == "12h"
    assert hotels_api._resolve_booking({KEY: {"daily_unit": "24h"}})["daily_unit"] == "24h"


def test_save_and_read():
    session = Session({KEY: {"default_type": "DAILY", "require_all_guests": True}})
    body = hotels_api.BookingSettingsRequest(default_type="DAILY", require_all_guests=True, daily_unit="24h")
    saved = asyncio.run(hotels_api.save_booking_settings(body, session=session, current_user=CONFIGURATOR))
    assert saved == {"default_type": "DAILY", "require_all_guests": True, "daily_unit": "24h"}
    got = asyncio.run(hotels_api.get_booking_settings(session=session, current_user={"hotel_id": HOTEL}))
    assert got["daily_unit"] == "24h"


def test_old_client_without_the_field_keeps_the_saved_mode():
    """Maydonni bilmaydigan (eski) klient saqlasa — 24 soatlik rejim
    o'chib ketmasligi kerak."""
    session = Session({KEY: {"default_type": "DAILY", "daily_unit": "24h"}})
    body = hotels_api.BookingSettingsRequest(default_type="HOURLY", require_all_guests=False)
    saved = asyncio.run(hotels_api.save_booking_settings(body, session=session, current_user=CONFIGURATOR))
    assert saved["daily_unit"] == "24h"
    assert saved["default_type"] == "HOURLY"
