"""Do'kon sotuvlari: bronga yozilgan savdo KIMGA sotilgani.

Foydalanuvchi talabi: Do'kon sahifasining "Sotuvlar" qismida bronga yozilgan
savdo kimga sotilgani ham ko'rinsin. Javobga xona raqami qo'shildi
(`room_number`; mehmon ismi va bron raqami avvaldan bor), qidiruv mijoz
ismi va xona bo'yicha ham ishlaydi. Mavjud maydonlar o'zgarmadi.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi import Response

from app.presentation.api.v1 import shop as shop_api

HOTEL = uuid.uuid4()
USER = {"id": str(uuid.uuid4()), "user_type": "ADMIN", "hotel_id": HOTEL}


def sale(reservation_id=None, status="PENDING"):
    return SimpleNamespace(
        id=uuid.uuid4(), reservation_id=reservation_id, total_amount=Decimal("45000"),
        payment_method=None if status == "PENDING" else "CASH", payments=None, status=status,
        paid_at=None, created_by=uuid.uuid4(), created_at=datetime(2026, 10, 8, 9, tzinfo=timezone.utc),
        items=[SimpleNamespace(product_id=uuid.uuid4(), product_name="Coca-Cola", quantity=3,
                               unit_price=Decimal("15000"), total_price=Decimal("45000"))],
    )


class Rows:
    def __init__(self, rows=None, scalar=None):
        self.rows, self._scalar = rows or [], scalar

    def all(self):
        return self.rows

    def scalar(self):
        return self._scalar


class Db:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    async def execute(self, stmt):
        self.sql.append(str(stmt))
        if "count(" in str(stmt).lower():
            return Rows(scalar=len(self.rows))
        return Rows(self.rows)


def test_charged_sale_shows_who_it_was_sold_to():
    res_id = uuid.uuid4()
    reservation = SimpleNamespace(reservation_number="RES-123-20261008-AB12")
    guest = SimpleNamespace(first_name="Gulamboy", last_name="Sadaddinov")
    creator = SimpleNamespace(first_name="Temur", last_name="Karimov")
    walk_in = sale(status="PAID")
    rows = [
        (sale(res_id), creator, reservation, guest, "32"),
        (walk_in, creator, None, None, None),
    ]
    db = Db(rows)
    out = asyncio.run(shop_api.list_sales(
        response=Response(), date_from=None, date_to=None, status=None, date_by="created",
        search=None, sort_by=None, sort_dir=None, skip=0, hotel_id=None, limit=50,
        session=db, current_user=USER,
    ))
    charged, plain = out
    assert charged["guest_name"] == "Gulamboy Sadaddinov"
    assert charged["room_number"] == "32"
    assert charged["reservation_number"] == "RES-123-20261008-AB12"
    # Bronsiz (naqd) sotuvda bo'sh — mavjud maydonlar avvalgidek
    assert plain["room_number"] is None and plain["guest_name"] is None
    assert plain["payment_method"] == "CASH" and plain["items"][0]["quantity"] == 3
    # Xona bronga bog'langan (LEFT JOIN — bronsiz sotuv ham chiqadi)
    main = db.sql[-1]
    assert "LEFT OUTER JOIN rooms" in main and "LEFT OUTER JOIN guests" in main


def test_search_by_guest_name_and_room():
    db = Db([])
    asyncio.run(shop_api.list_sales(
        response=Response(), date_from=None, date_to=None, status=None, date_by="created",
        search="Gulamboy", sort_by=None, sort_dir=None, skip=0, hotel_id=None, limit=50,
        session=db, current_user=USER,
    ))
    count_sql, main_sql = db.sql
    for sql in (count_sql, main_sql):
        assert "guests.first_name" in sql and "guests.last_name" in sql
        assert "rooms.room_number" in sql
        assert "reservations.reservation_number" in sql
        # Sanoq ham xuddi shu jadvallarni ulaydi — sahifalagich to'g'ri bo'lsin
        assert "LEFT OUTER JOIN rooms" in sql


def test_room_number_helper():
    room = SimpleNamespace(room_number="21")

    class Session:
        async def get(self, model, key):
            return room

    assert asyncio.run(shop_api._room_number(Session(), SimpleNamespace(room_id=uuid.uuid4()))) == "21"
    assert asyncio.run(shop_api._room_number(Session(), None)) is None
    assert asyncio.run(shop_api._room_number(Session(), SimpleNamespace(room_id=None))) is None
