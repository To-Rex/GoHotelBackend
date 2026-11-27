"""E-mehmon integratsiyasi: imzo, so'rov tanasi, ro'yxatga olish va chiqarish.

Ishga tushirish: pytest tests/test_emehmon.py

Haqiqiy E-mehmon'ga so'rov KETMAYDI: `emehmon_client.TRANSPORT` ga soxta
server qo'yiladi. U hujjatdagi qoidalarni takrorlaydi — imzoni tekshiradi,
`Idempotency-Key` bo'yicha javobni saqlaydi, faol dublni rad etadi.

Nozik joylar:

1. Imzo: HMAC-SHA256(login + vaqt + so'rov id), har so'rovga yangi id.
2. Maxfiy kalit bazada faqat shifrlangan, javoblarda chiqmaydi; kalitsiz
   yoqib bo'lmaydi. O'chiq filialda hech narsa yozilmaydi.
3. Kirish vaqti Toshkent vaqtida (+05:00) va faqat oxirgi 12 soat.
4. Mahalliy fuqaro PINFL bilan (bo'lmasa yuborilmaydi), chet ellik —
   `id_person` bilan; bo'sh maydonlar tanaga kirmaydi.
5. Javob yo'qolsa qayta yuborish O'SHA kalit va O'SHA tana bilan ketadi —
   mehmon ikki marta yozilmaydi. Rad etilgan urinish ham saqlanadi.
6. Mehmon bizda chiqarilgach E-mehmon'dan avtomatik chiqariladi; xonadagi
   mehmonga tegilmaydi, muvaffaqiyatsiz urinish keyinroq takrorlanadi.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.application.services import emehmon_client  # noqa: E402
from app.application.services import emehmon_crypto  # noqa: E402
from app.application.services import emehmon_payload as ep  # noqa: E402
from app.application.services import emehmon_service as es  # noqa: E402
from app.application.services.emehmon_service import EmehmonService  # noqa: E402
from app.core.exceptions import (  # noqa: E402
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.base import Base  # noqa: E402
from app.infrastructure.database.models.branch import Branch  # noqa: E402
from app.infrastructure.database.models.emehmon import EmehmonRegistration  # noqa: E402
from app.infrastructure.database.models.guest import Guest  # noqa: E402
from app.infrastructure.database.models.hotel import Hotel  # noqa: E402
from app.infrastructure.database.models.invoice import Invoice  # noqa: E402
from app.infrastructure.database.models.payment import Payment  # noqa: E402
from app.infrastructure.database.models.reservation import Reservation  # noqa: E402
from app.infrastructure.database.models.room import Room  # noqa: E402
from app.infrastructure.database.models.room_status_history import RoomStatusHistory  # noqa: E402
from app.infrastructure.database.models.user import User  # noqa: E402
from app.shared.utils import hotel_today  # noqa: E402

HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()
USERNAME = "hotel-0000001"
SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ENABLED = {
    "enabled": True,
    "environment": "sandbox",
    "username": USERNAME,
    "secret_enc": emehmon_crypto.encrypt(SECRET),
    "secret_hint": "cdef",
    "default_visit_type_id": 2,
    "default_guest_type_id": 1,
    "enabled_at": "2026-01-01T00:00:00+00:00",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- Soxta E-mehmon serveri -------------------------------------------------


class FakeEmehmon:
    """Hujjatdagi xatti-harakatni takrorlaydigan soxta server."""

    def __init__(self, secret: str = SECRET):
        self.secret = secret
        self.calls: list[dict] = []
        self.request_ids: set[str] = set()
        self.listoks: dict[int, dict] = {}
        self.idempotent: dict[str, tuple[bytes, int, dict]] = {}
        self.next_id = 8826912
        #: Keyingi yozuvchi so'rov BAJARILADI, lekin javob "yo'qoladi"
        self.drop_response = False
        #: Keyingi so'rovga shu xato qaytadi: (status, error_code, meta)
        self.fail_next: tuple[int, str, dict] | None = None
        self.people = {
            ("AB1234567", "1987-08-16"): {
                "pinfl": "31234567890123", "surname": "NASIROV", "firstname": "AXROR",
                "lastname": "IBRAGIMOVICH", "datebirth": "1987-08-16", "sex": "M",
                "datePassport": "2018-07-18", "passportIssuedBy": "YAKKASAROY IIB",
                "birthPlace": "TOSHKENT SHAHRI", "source": "mvd",
            },
        }

    def paths(self, method: str | None = None) -> list[str]:
        return [c["path"] for c in self.calls if method is None or c["method"] == method]

    def bodies(self, suffix: str) -> list[dict]:
        return [c["body"] for c in self.calls if c["path"].endswith(suffix) and c["body"] is not None]

    @staticmethod
    def ok(data: dict, status: int = 200, headers: dict | None = None) -> httpx.Response:
        return httpx.Response(
            status,
            json={"code": status, "message": "OK", "data": data, "request_id": "r-1"},
            headers={"X-Sandbox": "1", **(headers or {})},
        )

    @staticmethod
    def error(status: int, code: str, meta: dict | None = None, headers: dict | None = None) -> httpx.Response:
        return httpx.Response(
            status,
            json={"code": status, "error_code": code, "message": code.lower(), "meta": meta or {},
                  "request_id": "r-1"},
            headers=headers or {},
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(ep.API_PREFIX)
        raw = request.content
        body = json.loads(raw) if raw else None
        self.calls.append({
            "method": request.method, "path": path, "body": body, "raw": raw,
            "headers": dict(request.headers), "query": dict(request.url.params),
        })
        name = request.headers.get("X-User-Name", "")
        ts = request.headers.get("X-User-Ts", "")
        rid = request.headers.get("X-User-Request-Id", "")
        expected = hmac.new(self.secret.encode(), f"{name}{ts}{rid}".encode(), hashlib.sha256).hexdigest()
        if request.headers.get("X-User-Hash") != expected:
            return self.error(401, "INVALID_SIGNATURE")
        if rid in self.request_ids:
            return self.error(403, "REPLAY_ATTACK")
        self.request_ids.add(rid)
        if self.fail_next is not None:
            status, code, meta = self.fail_next
            self.fail_next = None
            return self.error(status, code, meta)

        key = request.headers.get("Idempotency-Key")
        if key and key in self.idempotent:
            stored_raw, status, data = self.idempotent[key]
            if stored_raw != raw:
                return self.error(422, "IDEMPOTENCY_KEY_REUSED_DIFFERENT_BODY")
            return self.ok(data, status, {"Idempotent-Replay": "1"})
        response = self.route(request.method, path, body, dict(request.url.params))
        if key and response.status_code < 300:
            self.idempotent[key] = (raw, response.status_code, response.json()["data"])
        if self.drop_response and request.method == "POST":
            self.drop_response = False
            raise httpx.ReadTimeout("javob yo'qoldi", request=request)
        return response

    def route(self, method: str, path: str, body: dict | None, query: dict) -> httpx.Response:
        if method == "GET" and path == "/hotel":
            return self.ok({
                "hotel_id": 1234, "name": "Hotel Example", "inn": "300123456",
                "address": "Tashkent", "license": {"number": "TR-1", "expires_at": "2027-12-31"},
                "deposit": 4500000, "can_register_listok": True, "blocking_reasons": [],
            })
        if method == "GET" and path == "/listok/check-can-create":
            return self.ok({
                "can_create": True, "blocking_reasons": [], "deposit": 4500000,
                "license_expires_at": "2027-12-31", "deposit_alert": None,
            })
        if method == "GET" and path == "/dictionaries/countries":
            return self.ok({"name": "countries", "locale": "uz", "count": 2, "items": [
                {"id": 173, "name": "O'zbekiston", "code": "UZB"},
                {"id": 76, "name": "Germaniya", "code": "DEU"},
            ]})
        if method == "GET" and path == "/hotel/rooms":
            return self.ok({"count": 1, "items": [
                {"id": 1011, "room_number": "101", "room_type_id": 1, "room_type_name": "Standart"},
            ]})
        if method == "POST" and path == "/listok/person-lookup":
            person = self.people.get((body["passportNumber"], body["datebirth"]))
            if person is None:
                return self.error(422, "PERSON_NOT_FOUND_IN_MVD", {"passportNumber": body["passportNumber"]})
            return self.ok(person)
        if method == "POST" and path == "/listok":
            for listok in self.listoks.values():
                if listok["passportNumber"] == body["passportNumber"] and not listok["datevisitoff"]:
                    return self.error(
                        422, "LISTOK_DUPLICATE_ACTIVE", {"existing_listok_id": listok["listok_id"]}
                    )
            if body["id_citizen"] in ep.LOCAL_CITIZENS and not body.get("pinfl"):
                if (body["passportNumber"], body["datebirth"]) not in self.people:
                    return self.error(422, "PINFL_REQUIRED_FOR_LOCAL")
            listok_id = self.next_id
            self.next_id += 1
            self.listoks[listok_id] = {
                **body, "listok_id": listok_id, "regNum": f"125-{listok_id % 10000}-26",
                "datevisitoff": None,
            }
            data = {
                "listok_id": listok_id, "regNum": f"125-{listok_id % 10000}-26",
                "registered_via": 2, "foreign_duplicates": [],
            }
            person = self.people.get((body["passportNumber"], body["datebirth"]))
            if person and not body.get("pinfl"):
                data["resolved_person"] = person
            return self.ok(data, 201)
        if path.startswith("/listok/"):
            parts = path.split("/")
            listok = self.listoks.get(int(parts[2])) if parts[2].isdigit() else None
            if listok is None:
                return self.error(404, "LISTOK_NOT_FOUND")
            if method == "GET" and len(parts) == 3:
                return self.ok(listok)
            if method == "POST" and parts[3:] == ["checkout"]:
                if listok["datevisitoff"]:
                    return self.error(422, "LISTOK_ALREADY_CHECKED_OUT")
                listok["datevisitoff"] = "2026-05-25 17:00:00"
                listok["summa"] = body["sum"]
                return self.ok({
                    "listok_id": listok["listok_id"], "checked_out": True,
                    "dateVisitOff": "2026-05-25T17:00:00+05:00",
                })
        return self.error(404, "NOT_FOUND")


# --- Sinov bazasi -------------------------------------------------------------


async def seed(session, *, arrived: timedelta = timedelta(hours=1), status: str = "CHECKED_IN") -> dict:
    """Bitta bron (xona 101, 3 sutka, 900 000): asosiy mehmon — O'zbekiston
    fuqarosi, hamroh — Germaniya fuqarosi, yana bir hamroh ketgan."""
    now = utc_now()
    user = User(user_type="RECEPTION", username="aziz", password_hash="x",
                first_name="Aziz", last_name="Karimov")
    main = Guest(hotel_id=HOTEL, first_name="Ахрор", last_name="Nasirov", passport_number="ab 1234567",
                 birth_date=date(1987, 8, 16), nationality="O'zbekiston")
    friend = Guest(hotel_id=HOTEL, first_name="Max", last_name="Mustermann", passport_number="C01X00T47",
                   birth_date=date(1985, 7, 12), nationality="Germaniya")
    gone = Guest(hotel_id=HOTEL, first_name="Vali", last_name="Ketgan", passport_number="AC7654321",
                 birth_date=date(1990, 1, 1), nationality="O'zbekiston")
    session.add_all([user, main, friend, gone])
    await session.flush()
    room = Room(hotel_id=HOTEL, branch_id=BRANCH, floor_id=uuid.uuid4(), room_type_id=uuid.uuid4(),
                room_number="101")
    session.add(room)
    await session.flush()
    today = hotel_today()
    reservation = Reservation(
        hotel_id=HOTEL, branch_id=BRANCH, reservation_number="RES-7", guest_id=main.id,
        room_id=room.id, check_in_date=today, check_out_date=today + timedelta(days=3),
        status=status, total_amount=900_000, created_by=user.id,
        companions=[
            {"guest_id": str(friend.id), "name": "Max Mustermann"},
            {"guest_id": str(gone.id), "name": "Vali Ketgan",
             "left_at": (now - timedelta(minutes=30)).isoformat()},
        ],
    )
    session.add(reservation)
    await session.flush()
    session.add(RoomStatusHistory(
        hotel_id=HOTEL, branch_id=BRANCH, room_id=room.id, status="OCCUPIED",
        notes="Check-in for reservation RES-7", created_at=now - arrived,
    ))
    invoice = Invoice(
        hotel_id=HOTEL, branch_id=BRANCH, reservation_id=reservation.id, guest_id=main.id,
        invoice_number="INV-7", invoice_date=today, total_amount=900_000, paid_amount=900_000,
        status="PAID", created_by=user.id,
    )
    session.add(invoice)
    await session.flush()
    session.add_all([
        Payment(hotel_id=HOTEL, branch_id=BRANCH, invoice_id=invoice.id, payment_number="PAY-1",
                amount=300_000, payment_method="CASH", payment_date=today, created_by=user.id,
                created_at=now),
        Payment(hotel_id=HOTEL, branch_id=BRANCH, invoice_id=invoice.id, payment_number="PAY-2",
                amount=600_000, payment_method="CREDIT_CARD", payment_date=today, created_by=user.id,
                created_at=now),
    ])
    await session.commit()
    return {
        "user": user.id, "main": main.id, "friend": friend.id, "gone": gone.id,
        "reservation": reservation.id, "room": room.id,
    }


def run(body, settings: dict | None = ENABLED, fake: FakeEmehmon | None = None, **seed_args):
    fake = fake or FakeEmehmon()

    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [
            Hotel.__table__, Branch.__table__, User.__table__, Guest.__table__, Room.__table__,
            Reservation.__table__, Invoice.__table__, Payment.__table__,
            RoomStatusHistory.__table__, EmehmonRegistration.__table__,
        ]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        emehmon_client.TRANSPORT = httpx.MockTransport(fake)
        es._dictionary_cache.clear()
        es._busy.clear()
        try:
            async with maker() as session:
                ids = await seed(session, **seed_args)
                session.add(Branch(
                    id=BRANCH, hotel_id=HOTEL, name="Asosiy", code="MAIN", is_main_branch=True,
                    settings={ep.SETTINGS_KEY: settings} if settings else {},
                ))
                await session.commit()
                await body(session, ids, fake, maker)
        finally:
            emehmon_client.TRANSPORT = None
            await engine.dispose()

    asyncio.run(main())


async def rows_of(session) -> list[EmehmonRegistration]:
    return list((await session.execute(
        select(EmehmonRegistration).order_by(EmehmonRegistration.created_at)
    )).scalars().all())


def local_form(**over) -> dict:
    """To'liq to'ldirilgan shakl — O'zbekiston fuqarosi."""
    return {
        "surname": "Nasirov", "firstname": "Axror", "citizen_id": 173,
        "passport_number": "ab 1234567", "passport_date": "2018-07-18",
        "birth_date": "1987-08-16", "sex": "M", "visit_type_id": 2, "guest_type_id": 1,
        "arrival": ep.tashkent_iso(utc_now() - timedelta(hours=1)), "days": 3, "room": "101",
        **over,
    }


def foreign_form(**over) -> dict:
    return {
        "surname": "Mustermann", "firstname": "Max", "citizen_id": 76,
        "passport_number": "C01X00T47", "passport_date": "2021-03-15",
        "birth_date": "1985-07-12", "sex": "M", "visit_type_id": 2, "guest_type_id": 1,
        "arrival": ep.tashkent_iso(utc_now() - timedelta(hours=1)), "days": 3,
        "person_id": 1234567890, **over,
    }


# --- 1. Sof funksiyalar -------------------------------------------------------


def test_signature_matches_documented_formula():
    # Mustaqil hisob: printf '%s' "<login><ts><rid>" | openssl dgst -sha256 -hmac "<secret>"
    rid = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    assert ep.sign(USERNAME, "1769053020", rid, SECRET) == (
        "9c64e46c8bfea821ce5cb9196adae5bc421ec1d19e1800bea8628217d88becac"
    )
    first = ep.auth_headers(USERNAME, SECRET, now=1769053020.9)
    second = ep.auth_headers(USERNAME, SECRET, now=1769053020.9)
    assert first["X-User-Name"] == USERNAME and first["X-User-Ts"] == "1769053020"
    # Har so'rovga YANGI id (takrori — REPLAY_ATTACK), UUID v4
    assert first["X-User-Request-Id"] != second["X-User-Request-Id"]
    assert uuid.UUID(first["X-User-Request-Id"]).version == 4
    assert first["X-User-Hash"] == ep.sign(USERNAME, "1769053020", first["X-User-Request-Id"], SECRET)
    assert len(first["X-User-Hash"]) == 64 and first["X-User-Hash"] == first["X-User-Hash"].lower()


def test_settings_defaults_validation_and_secret_never_public():
    empty = ep.resolve(None)
    assert empty["enabled"] is False and empty["environment"] == "sandbox"
    assert empty["secret_set"] is False and empty["username"] == ""
    assert empty["auto_checkout"] is True and empty["prompt_on_checkin"] is True
    assert empty["card_payment_type_id"] == ep.PAYMENT_UZCARD
    problems = ep.validate(empty)
    assert any("login" in p for p in problems) and any("kalit" in p for p in problems)

    cfg = ep.resolve({**ENABLED, "environment": "production", "card_payment_type_id": 3,
                      "auto_checkout": False})
    assert ep.validate(cfg) == [] and cfg["secret_set"] is True
    assert ep.base_url(cfg) == "https://api.emehmon.uz" and cfg["card_payment_type_id"] == 3
    assert cfg["auto_checkout"] is False
    shown = ep.public(cfg)
    assert "secret_enc" not in shown and SECRET not in json.dumps(shown)
    assert shown["secret_hint"] == "cdef" and shown["base_url"] == "https://api.emehmon.uz"
    # Noto'g'ri qiymatlar standartga qaytadi — erkin manzil kiritib bo'lmaydi
    bad = ep.resolve({"environment": "http://evil.example", "card_payment_type_id": 9})
    assert bad["environment"] == "sandbox" and bad["card_payment_type_id"] == ep.PAYMENT_UZCARD
    assert ep.enabled_since(cfg) == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert ep.enabled_since(empty) is None


def test_names_passport_and_citizenship():
    assert ep.latin_name("  nasirov ") == "NASIROV"
    assert ep.latin_name("Ахрор") == "AXROR" and ep.latin_name("Ўткир Ғуломов") == "O'TKIR G'ULOMOV"
    assert ep.latin_name("Oʻgʻiloy") == "O'G'ILOY" and ep.latin_name("Anne-Marie 3rd!") == "ANNE-MARIE RD"
    assert len(ep.latin_name("A" * 80)) == 50
    assert ep.normalize_passport(" ab 123-4567 ") == "AB1234567"
    assert ep.country_code("O'zbekiston") == "UZB" and ep.country_code("Oʻzbekiston") == "UZB"
    assert ep.country_code("Germaniya") == "DEU" and ep.country_code("deu") == "DEU"
    assert ep.country_code("Boshqa") is None and ep.country_code(None) is None
    countries = [{"id": 173, "code": "UZB"}, {"id": 76, "code": "DEU"}]
    assert ep.citizen_id_for("O'zbekiston", None) == 173           # ma'lumotnomasiz ham
    assert ep.citizen_id_for("Germaniya", countries) == 76
    assert ep.citizen_id_for("Germaniya", None) is None and ep.citizen_id_for("Boshqa", countries) is None
    assert ep.is_local(173) and ep.is_local("231") and not ep.is_local(76) and not ep.is_local(None)


def test_arrival_time_is_tashkent_and_within_12_hours():
    moment = datetime(2026, 5, 22, 9, 30, 15, 123, tzinfo=timezone.utc)
    assert ep.tashkent_iso(moment) == "2026-05-22T14:30:15+05:00"
    # Zonasiz qiymat — Toshkent vaqti (brauzerdagi datetime-local)
    assert ep.parse_moment("2026-05-22T14:30") == datetime(2026, 5, 22, 9, 30, tzinfo=timezone.utc)
    assert ep.parse_moment("2026-05-22T09:30:00Z") == datetime(2026, 5, 22, 9, 30, tzinfo=timezone.utc)
    assert ep.parse_moment("kecha") is None and ep.parse_moment("") is None
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    assert ep.arrival_problem(now - timedelta(hours=11, minutes=59), now) is None
    assert ep.arrival_problem(now, now) is None
    assert "12 soat" in ep.arrival_problem(now - timedelta(hours=12, minutes=1), now)
    assert "kelajak" in ep.arrival_problem(now + timedelta(minutes=5), now)
    assert ep.arrival_problem(now + timedelta(seconds=30), now) is None      # soatlar farqi
    assert "ko'rsatilmagan" in ep.arrival_problem(None, now)
    assert ep.stay_days(date(2026, 5, 22), date(2026, 5, 25)) == 3
    assert ep.stay_days(date(2026, 5, 22), date(2026, 5, 22)) == 1           # kamida 1
    assert ep.stay_days(date(2026, 1, 1), date(2028, 1, 1)) == 365 and ep.stay_days(None, None) == 1


def test_listok_body_for_local_and_foreigner():
    now = utc_now()
    clean = ep.clean_form(local_form(pinfl="3123 4567 8901 23", amount="900000.4", paid=True))
    assert ep.form_problems(clean, now) == []
    body = ep.build_listok(clean, actor_id="u-1", actor_name="Aziz Karimov")
    assert body["surname"] == "NASIROV" and body["firstname"] == "AXROR"
    assert body["passportNumber"] == "AB1234567" and body["id_citizen"] == 173
    assert body["pinfl"] == "31234567890123" and "id_person" not in body
    assert body["datevisiton"].endswith("+05:00") and body["wdays"] == 3 and body["sex"] == "M"
    assert body["propiska"] == "101" and body["amount"] == 900000 and body["payed"] == 1
    assert body["crm_actor_id"] == "u-1" and body["crm_actor_name"] == "Aziz Karimov"
    # Bo'sh maydonlar tanaga kirmaydi; standart pasport turi (1) ham yuborilmaydi
    for absent in ("lastname", "id_passporttype", "visaNumber", "id_country", "children", "kppNumber"):
        assert absent not in body
    # PINFL bo'lmasa — yuborilmaydi (E-mehmon IIV bazasidan o'zi topadi)
    assert "pinfl" not in ep.build_listok(ep.clean_form(local_form()))

    foreign = ep.clean_form(foreign_form(
        visa_number="V-1", visa_from="2026-01-01", visa_to="2026-12-31",
        children=[{"full_name": "Mustermann Anna", "gen": "W", "birth_day": "2018-04-12"}, {"gen": "M"}],
    ))
    assert ep.form_problems(foreign, now) == []
    body = ep.build_listok(foreign)
    assert body["id_person"] == "1234567890" and "pinfl" not in body          # satr bo'lib ketadi
    assert body["visaNumber"] == "V-1" and body["dateVisaOff"] == "2026-12-31"
    assert body["children"] == [{"full_name": "Mustermann Anna", "gen": "W", "birth_day": "2018-04-12"}]
    assert "crm_actor_id" not in body
    # Tana DOIM bir xil baytlar (Idempotency-Key uchun)
    assert ep.encode_body(body) == ep.encode_body(dict(reversed(list(body.items()))))
    assert ep.body_hash(body) == hashlib.sha256(ep.encode_body(body)).hexdigest()


def test_form_problems_are_reported_before_sending():
    now = utc_now()
    problems = ep.form_problems(ep.clean_form({}), now)
    for word in ("Familiya", "Ism", "Fuqarolik", "Pasport seriya", "Tug'ilgan", "Jinsi",
                 "Tashrif maqsadi", "Mehmon turi", "Kirish vaqti", "muddati"):
        assert any(word in p for p in problems), word
    # Chet ellik `id_person` siz yuborilmaydi
    no_person = ep.form_problems(ep.clean_form(foreign_form(person_id="")), now)
    assert len(no_person) == 1 and "Bazadan tekshirish" in no_person[0]
    late = ep.form_problems(ep.clean_form(local_form(arrival=ep.tashkent_iso(now - timedelta(hours=13)))), now)
    assert len(late) == 1 and "12 soat" in late[0]
    assert any("tug'ilgan sanadan keyin" in p for p in ep.form_problems(
        ep.clean_form(local_form(passport_date="1987-08-16")), now))
    assert any("14 ta raqam" in p for p in ep.form_problems(ep.clean_form(local_form(pinfl="123")), now))
    # Noto'g'ri qiymatlar tozalanadi
    odd = ep.clean_form({"sex": "X", "days": 9999, "amount": "abc", "birth_date": "2026-13-40"})
    assert odd["sex"] is None and odd["days"] == 365 and odd["amount"] is None and odd["birth_date"] is None


def test_checkout_amount_and_payment_type():
    cfg = ep.resolve(ENABLED)
    assert ep.payment_type_for("CASH", cfg) == 2 and ep.payment_type_for("credit_card", cfg) == 4
    assert ep.payment_type_for("CARD", ep.resolve({**ENABLED, "card_payment_type_id": 3})) == 3
    assert ep.payment_type_for("TRANSFER", cfg) == 5 and ep.payment_type_for("ONLINE", cfg) == 1
    assert ep.payment_type_for(None, cfg) == 1
    assert ep.dominant_method({"CASH": 300_000.0, "CREDIT_CARD": 600_000.0}) == "CREDIT_CARD"
    assert ep.dominant_method({}) is None and ep.dominant_method({"CASH": 0.0}) is None
    # Bron summasi mehmonlar o'rtasida teng bo'linadi, yig'indi saqlanadi
    assert ep.checkout_share(900_000, 0, 2) == 450_000 and ep.checkout_share(900_000, 450_000, 1) == 450_000
    assert ep.checkout_share(100_000, 0, 3) == 33_333 and ep.checkout_share(100_000, 66_666, 1) == 33_334
    assert ep.checkout_share(100_000, 150_000, 1) == 0 and ep.checkout_share(0, 0, 0) == 0


def test_envelope_and_error_texts():
    assert ep.parse_envelope(201, {"code": 201, "data": {"listok_id": 5}}) == {"listok_id": 5}
    with pytest.raises(ep.EmehmonError) as caught:
        ep.parse_envelope(422, {"code": 422, "error_code": "ROOM_CAPACITY_EXCEEDED",
                                "message": "x", "meta": {"reason": "room_full"}})
    assert caught.value.error_code == "ROOM_CAPACITY_EXCEEDED" and "o'rinlar band" in caught.value.text
    assert caught.value.uncertain is False and caught.value.as_dict()["meta"] == {"reason": "room_full"}
    with pytest.raises(ep.EmehmonError) as bad:
        ep.parse_envelope(200, "<html>")
    assert bad.value.error_code == ep.BAD_RESPONSE and bad.value.uncertain is True
    with pytest.raises(ep.EmehmonError) as unknown:
        ep.parse_envelope(418, {"message": "teapot"})
    assert unknown.value.error_code == "HTTP_418" and unknown.value.text == "teapot"
    assert "field" not in ep.error_text("VALIDATION_ERROR") and "datevisiton" in ep.error_text(
        "VALIDATION_ERROR", "bad date", {"field": "datevisiton"})
    assert "maxfiy kalit" in ep.error_text("INVALID_SIGNATURE").lower()
    # Natijasi noma'lum kodlar: qayta yuborishda o'sha kalit ishlatiladi
    for code in (ep.NETWORK_ERROR, "SERVICE_UNAVAILABLE", "INTERNAL_ERROR", "TOO_MANY_REQUESTS"):
        assert ep.EmehmonError(code).uncertain is True
    for code in ("VALIDATION_ERROR", "LISTOK_DUPLICATE_ACTIVE", "INVALID_SIGNATURE"):
        assert ep.EmehmonError(code).uncertain is False


def test_lookup_answer_maps_to_form_fields():
    local = ep.person_from_lookup(FakeEmehmon().people[("AB1234567", "1987-08-16")])
    assert local == {
        "surname": "NASIROV", "firstname": "AXROR", "lastname": "IBRAGIMOVICH",
        "birth_date": "1987-08-16", "sex": "M", "passport_date": "2018-07-18",
        "passport_issued_by": "YAKKASAROY IIB", "pinfl": "31234567890123",
    }
    foreign = ep.person_from_lookup({"person_id": 987654321, "visaNumber": "V-9", "id_visatype": 3})
    assert foreign == {"person_id": "987654321", "visa_number": "V-9", "visa_type_id": 3}
    assert ep.person_from_lookup(None) == {}


# --- 2. Sozlama ---------------------------------------------------------------


def test_disabled_branch_writes_nothing_and_calls_nobody():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        assert await svc.pending(HOTEL) == {"enabled": False, "register": [], "checkout": [], "count": 0}
        with pytest.raises(ValidationException) as caught:
            await svc.register(HOTEL, ids["reservation"], ids["main"], local_form(), ids["user"])
        assert caught.value.error_code == "EMEHMON_DISABLED"
        with pytest.raises(ValidationException):
            await svc.skip(HOTEL, ids["reservation"], ids["main"], None, ids["user"])
        assert (await svc.sheet(HOTEL, ids["reservation"]))["enabled"] is False
        assert await rows_of(session) == [] and fake.calls == []
        assert await es.due_checkouts(session) == []

    run(body, settings=None)


def test_settings_secret_is_encrypted_and_required():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        with pytest.raises(ValidationException) as caught:
            await svc.save_settings(HOTEL, {"enabled": True, "username": USERNAME})
        assert caught.value.error_code == "EMEHMON_SETTINGS_INVALID"
        with pytest.raises(ValidationException) as short:
            await svc.save_settings(HOTEL, {"secret": "qisqa"})
        assert short.value.error_code == "EMEHMON_SECRET_INVALID"

        saved = await svc.save_settings(HOTEL, {
            "enabled": True, "username": f" {USERNAME} ", "secret": f"  {SECRET}\n",
            "environment": "production", "default_visit_type_id": 2,
        })
        assert saved["enabled"] and saved["secret_set"] and saved["secret_hint"] == "cdef"
        assert saved["username"] == USERNAME and saved["enabled_at"]
        branch = await session.get(Branch, BRANCH)
        stored = branch.settings[ep.SETTINGS_KEY]
        # Bazada faqat shifrlangan; ochiq kalit hech qayerda yo'q
        assert SECRET not in json.dumps(branch.settings) and "secret" not in stored
        assert emehmon_crypto.decrypt(stored["secret_enc"]) == SECRET
        assert es.secret_of(await svc.config(HOTEL)) == SECRET

        # Kalit yubormasdan saqlash — avvalgisi qoladi, yoqilgan vaqt o'zgarmaydi
        again = await svc.save_settings(HOTEL, {"auto_checkout": False})
        assert again["secret_set"] and again["enabled_at"] == saved["enabled_at"]
        assert again["auto_checkout"] is False and again["environment"] == "production"
        # Kalit o'chirilsa yoqilgan holda qolmaydi
        with pytest.raises(ValidationException):
            await svc.save_settings(HOTEL, {"clear_secret": True})
        off = await svc.save_settings(HOTEL, {"clear_secret": True, "enabled": False})
        assert off["secret_set"] is False and off["enabled_at"] is None
        with pytest.raises(ValidationException) as missing:
            await svc.status(HOTEL)
        assert missing.value.error_code == "EMEHMON_NOT_CONFIGURED"

    run(body, settings=None)


def test_connection_test_signs_requests_and_reports_wrong_secret():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        status = await svc.status(HOTEL)
        assert status["ok"] is True and status["sandbox"] is True
        assert status["hotel"]["name"] == "Hotel Example" and status["deposit"] == 4500000
        assert status["can_create"] is True and status["license_expires_at"] == "2027-12-31"
        assert fake.paths() == ["/hotel", "/listok/check-can-create"]
        first, second = fake.calls
        assert first["headers"]["x-user-name"] == USERNAME
        assert first["headers"]["x-user-request-id"] != second["headers"]["x-user-request-id"]
        assert first["headers"]["accept-language"] == "uz"

    run(body)

    async def wrong(session, ids, fake, maker):
        status = await EmehmonService(session).status(HOTEL)
        assert status["ok"] is False and status["error_code"] == "INVALID_SIGNATURE"
        assert "kalit" in status["error"]

    run(wrong, fake=FakeEmehmon(secret="boshqa-kalit-boshqa-kalit"))


def test_dictionaries_are_cached_and_limited_to_known_names():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        items = await svc.dictionary(HOTEL, "countries", "ru")
        assert [i["code"] for i in items] == ["UZB", "DEU"]
        assert fake.calls[-1]["headers"]["accept-language"] == "ru"
        await svc.dictionary(HOTEL, "countries", "ru")
        assert len(fake.calls) == 1                              # ikkinchisi keshdan
        await svc.dictionary(HOTEL, "countries", "ru", refresh=True)
        assert len(fake.calls) == 2
        # Vaqtincha uzilishda eski ro'yxat ishlatiladi
        fake.fail_next = (503, "SERVICE_UNAVAILABLE", {})
        assert len(await svc.dictionary(HOTEL, "countries", "ru", refresh=True)) == 2
        with pytest.raises(NotFoundException):
            await svc.dictionary(HOTEL, "../hotel", "ru")
        assert [r["room_number"] for r in await svc.rooms(HOTEL)] == ["101"]

    run(body)


# --- 3. Bron varag'i va ro'yxatga olish ---------------------------------------


def test_sheet_lists_people_with_prefilled_forms():
    async def body(session, ids, fake, maker):
        sheet = await EmehmonService(session).sheet(HOTEL, ids["reservation"])
        assert sheet["enabled"] and sheet["reservation"]["room_number"] == "101"
        main, friend, gone = sheet["persons"]
        assert (main["role"], friend["role"], gone["role"]) == ("main", "companion", "companion")
        assert main["present"] and friend["present"] and not gone["present"]
        assert main["state"] == "none" and main["registration"] is None and not main["overdue"]
        form = main["form"]
        # Mehmon kartasidan: kirillcha ism lotinga o'giriladi, pasport tozalanadi
        assert (form["surname"], form["firstname"]) == ("NASIROV", "AXROR")
        assert form["passport_number"] == "AB1234567" and form["birth_date"] == "1987-08-16"
        assert form["citizen_id"] == 173 and main["citizen_code"] == "UZB"
        assert form["visit_type_id"] == 2 and form["guest_type_id"] == 1      # sozlamadagi standart
        assert form["room"] == "101" and form["amount"] == 900000
        # Sutkalar — kirgan (mahalliy) kundan bron tugashigacha
        start = (utc_now() - timedelta(hours=1)).astimezone(ep.TASHKENT).date()
        assert form["days"] == ep.stay_days(start, hotel_today() + timedelta(days=3))
        # Kirish vaqti — haqiqiy (xona tarixidan), Toshkent vaqtida
        arrived = ep.parse_moment(form["arrival"])
        assert form["arrival"].endswith("+05:00")
        assert abs((utc_now() - arrived) - timedelta(hours=1)) < timedelta(minutes=1)
        # Chet ellik: id ma'lumotnomadan topiladi (brauzer `citizen_code` bo'yicha)
        assert friend["form"]["citizen_id"] is None and friend["citizen_code"] == "DEU"
        assert friend["form"]["amount"] is None
        # Ketgan hamrohga shakl berilmaydi
        assert gone["form"] is None and gone["state"] == "none"
        assert fake.calls == []                                   # varaq tarmoqqa chiqmaydi

    run(body)


def test_sheet_flags_arrival_older_than_12_hours():
    async def body(session, ids, fake, maker):
        sheet = await EmehmonService(session).sheet(HOTEL, ids["reservation"])
        main = sheet["persons"][0]
        # Haqiqiy vaqt qabul qilinmaydi — soxta vaqt taklif QILINMAYDI, xodim o'zi tanlaydi
        assert main["overdue"] is True and main["form"]["arrival"] is None
        assert main["form"]["days"] == 3                           # bugundan hisoblanadi

    run(body, arrived=timedelta(hours=20))


def test_register_success_sends_documented_body():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        result = await svc.register(HOTEL, ids["reservation"], ids["main"], local_form(), ids["user"])
        assert result["status"] == "registered" and result["listok_id"] == 8826912
        assert result["reg_num"] == "125-6912-26" and result["error"] is None
        sent = fake.calls[-1]
        assert sent["method"] == "POST" and sent["path"] == "/listok"
        assert uuid.UUID(sent["headers"]["idempotency-key"]).version == 4
        assert sent["body"]["passportNumber"] == "AB1234567" and sent["body"]["propiska"] == "101"
        assert sent["body"]["crm_actor_name"] == "Aziz Karimov"
        assert sent["body"]["crm_actor_id"] == str(ids["user"])
        assert sent["raw"] == ep.encode_body(sent["body"])
        (row,) = await rows_of(session)
        assert row.status == "registered" and row.uncertain is False and row.attempts == 1
        assert row.branch_id == BRANCH and row.role == "main" and row.registered_by == ids["user"]
        assert row.passport_number == "AB1234567" and row.environment == "sandbox"
        # E-mehmon IIV bazasidan topgan PINFL keyingi safar uchun saqlanadi
        assert row.form["pinfl"] == "31234567890123" and row.form["lastname"] == "IBRAGIMOVICH"

        with pytest.raises(ConflictException) as again:
            await svc.register(HOTEL, ids["reservation"], ids["main"], local_form(), ids["user"])
        assert again.value.error_code == "EMEHMON_ALREADY_REGISTERED"
        assert len(fake.listoks) == 1
        sheet = await svc.sheet(HOTEL, ids["reservation"])
        main = sheet["persons"][0]
        assert main["state"] == "registered" and main["form"] is None
        assert main["registration"]["reg_num"] == "125-6912-26"
        assert main["checkout"] == {"sum": 900000, "payment_type_id": 4}

    run(body)


def test_register_refuses_incomplete_form_and_absent_people():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        with pytest.raises(ValidationException) as caught:
            await svc.register(HOTEL, ids["reservation"], ids["main"], local_form(sex=""), ids["user"])
        assert caught.value.error_code == "EMEHMON_FORM_INVALID" and "Jinsi" in caught.value.detail
        with pytest.raises(ValidationException) as left:
            await svc.register(HOTEL, ids["reservation"], ids["gone"], local_form(), ids["user"])
        assert left.value.error_code == "EMEHMON_PERSON_LEFT"
        with pytest.raises(NotFoundException):
            await svc.register(HOTEL, ids["reservation"], uuid.uuid4(), local_form(), ids["user"])
        # Begona mehmonxona nomidan — o'sha mehmonxonada integratsiya yo'q
        with pytest.raises(ValidationException) as foreign:
            await svc.register(uuid.uuid4(), ids["reservation"], ids["main"], local_form(), ids["user"])
        assert foreign.value.error_code == "EMEHMON_DISABLED"
        # So'rov umuman ketmagan, bazaga ham yozilmagan
        assert fake.calls == [] and await rows_of(session) == []

    run(body)

    async def not_checked_in(session, ids, fake, maker):
        with pytest.raises(ValidationException) as caught:
            await EmehmonService(session).register(
                HOTEL, ids["reservation"], ids["main"], local_form(), ids["user"])
        assert caught.value.error_code == "EMEHMON_NOT_CHECKED_IN" and fake.calls == []

    run(not_checked_in, status="CONFIRMED")


def test_rejected_attempt_is_kept_and_retry_uses_new_key():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        unknown = local_form(passport_number="ZZ0000001", birth_date="1990-02-02")
        result = await svc.register(HOTEL, ids["reservation"], ids["main"], unknown, ids["user"])
        assert result["status"] == "failed" and result["error_code"] == "PINFL_REQUIRED_FOR_LOCAL"
        assert "PINFL" in result["error"] and result["uncertain"] is False
        first_key = fake.calls[-1]["headers"]["idempotency-key"]
        (row,) = await rows_of(session)
        assert row.status == "failed" and row.attempts == 1

        # Varaqda xodim kiritgani saqlanib qoladi
        sheet = await svc.sheet(HOTEL, ids["reservation"])
        main = sheet["persons"][0]
        assert main["state"] == "failed" and main["form"]["passport_number"] == "ZZ0000001"
        pending = await svc.pending(HOTEL)
        assert pending["register"][0]["state"] == "failed" and "PINFL" in pending["register"][0]["error"]

        fixed = await svc.register(
            HOTEL, ids["reservation"], ids["main"], {**unknown, "pinfl": "30202900000011"}, ids["user"])
        assert fixed["status"] == "registered" and fixed["id"] == result["id"]    # o'sha qator
        assert fake.calls[-1]["headers"]["idempotency-key"] != first_key           # tana o'zgardi
        (row,) = await rows_of(session)
        assert row.attempts == 2 and row.error is None and row.error_code is None

    run(body)


def test_lost_response_is_retried_with_same_key_and_body():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        form = local_form()
        fake.drop_response = True
        result = await svc.register(HOTEL, ids["reservation"], ids["main"], form, ids["user"])
        assert result["status"] == "failed" and result["error_code"] == ep.NETWORK_ERROR
        assert result["uncertain"] is True and len(fake.listoks) == 1      # aslida yozilgan!
        first = fake.calls[-1]

        retry = await svc.register(HOTEL, ids["reservation"], ids["main"], form, ids["user"])
        second = fake.calls[-1]
        assert second["headers"]["idempotency-key"] == first["headers"]["idempotency-key"]
        assert second["raw"] == first["raw"]                                # aynan o'sha baytlar
        assert second["headers"]["x-user-request-id"] != first["headers"]["x-user-request-id"]
        assert retry["status"] == "registered" and retry["listok_id"] == 8826912
        assert len(fake.listoks) == 1                                       # ikki marta yozilmadi
        (row,) = await rows_of(session)
        assert row.attempts == 2 and row.uncertain is False

    run(body)


def test_interrupted_attempt_shows_as_uncertain_then_blocks_double_send():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        key = str(uuid.uuid4())
        clean = ep.clean_form(local_form())
        row = EmehmonRegistration(
            hotel_id=HOTEL, branch_id=BRANCH, reservation_id=ids["reservation"], guest_id=ids["main"],
            role="main", status="sending", uncertain=True, idempotency_key=key,
            form={**clean, "_actor": {"id": "u-9", "name": "Bobur"}}, attempts=1,
        )
        session.add(row)
        await session.commit()
        # Yangi boshlangan urinish — ikkinchi xodim yubora olmaydi
        with pytest.raises(ConflictException) as busy:
            await svc.register(HOTEL, ids["reservation"], ids["main"], local_form(), ids["user"])
        assert busy.value.error_code == "EMEHMON_BUSY" and fake.calls == []
        # Uzilib qolgan (eskirgan) urinish — "natija noma'lum", o'sha kalit bilan takrorlanadi
        row.updated_at = utc_now() - timedelta(minutes=10)
        await session.commit()
        shown = (await svc.sheet(HOTEL, ids["reservation"]))["persons"][0]
        assert shown["state"] == "failed" and "noma'lum" in shown["registration"]["error"]
        result = await svc.register(HOTEL, ids["reservation"], ids["main"], clean, ids["user"])
        assert result["status"] == "registered"
        assert fake.calls[-1]["headers"]["idempotency-key"] == key
        assert fake.calls[-1]["body"]["crm_actor_name"] == "Bobur"          # tana o'zgarmasin

    run(body)


def test_duplicate_listok_can_be_linked_only_with_matching_passport():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        # Kabinetda qo'lda kiritilgan faol ro'yxatlar
        fake.listoks[555] = {"listok_id": 555, "regNum": "125-555-26", "passportNumber": "AB1234567",
                             "surname": "NASIROV", "firstname": "AXROR", "datevisitoff": None,
                             "datevisiton": "2026-05-22 14:30:00"}
        fake.listoks[777] = {"listok_id": 777, "regNum": "125-777-26", "passportNumber": "XX9999999",
                             "datevisitoff": None}
        result = await svc.register(HOTEL, ids["reservation"], ids["main"], local_form(), ids["user"])
        assert result["status"] == "failed" and result["error_code"] == "LISTOK_DUPLICATE_ACTIVE"
        assert result["error_meta"] == {"existing_listok_id": 555}

        with pytest.raises(ValidationException) as wrong:
            await svc.link(HOTEL, ids["reservation"], ids["main"], 777, ids["user"])
        assert wrong.value.error_code == "EMEHMON_LINK_MISMATCH"
        with pytest.raises(ValidationException) as absent:
            await svc.link(HOTEL, ids["reservation"], ids["main"], 999, ids["user"])
        assert absent.value.error_code == "EMEHMON_LISTOK_NOT_FOUND"

        linked = await svc.link(HOTEL, ids["reservation"], ids["main"], 555, ids["user"])
        assert linked["status"] == "registered" and linked["listok_id"] == 555
        assert linked["reg_num"] == "125-555-26" and linked["id"] == result["id"]
        (row,) = await rows_of(session)
        assert row.arrived_at == datetime(2026, 5, 22, 9, 30, tzinfo=timezone.utc)
        # Yopilgan ro'yxat bog'lanmaydi
        fake.listoks[888] = {"listok_id": 888, "passportNumber": "C01X00T47",
                             "datevisitoff": "2026-05-23 10:00:00"}
        with pytest.raises(ValidationException) as closed:
            await svc.link(HOTEL, ids["reservation"], ids["friend"], 888, ids["user"])
        assert closed.value.error_code == "EMEHMON_LINK_CLOSED"

    run(body)


def test_lookup_returns_official_data_or_reason():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        found = await svc.lookup(HOTEL, "ab 1234567", "1987-08-16", 173)
        assert found["found"] is True and found["source"] == "mvd"
        assert found["person"]["pinfl"] == "31234567890123" and found["person"]["sex"] == "M"
        assert fake.calls[-1]["body"] == {
            "passportNumber": "AB1234567", "datebirth": "1987-08-16", "id_citizen": 173}
        missing = await svc.lookup(HOTEL, "ZZ0000001", "1990-02-02", 173)
        assert missing["found"] is False and missing["error_code"] == "PERSON_NOT_FOUND_IN_MVD"
        assert "IIV" in missing["error"]
        fake.fail_next = (429, "LOOKUP_TEMPORARILY_BLOCKED", {"retry_after": 3480})
        blocked = await svc.lookup(HOTEL, "AB1234567", "1987-08-16", 173)
        assert blocked["found"] is False and blocked["meta"]["retry_after"] == 3480
        calls = len(fake.calls)
        with pytest.raises(ValidationException):
            await svc.lookup(HOTEL, "", "1987-08-16", 173)
        assert len(fake.calls) == calls

    run(body)


def test_skip_removes_from_queue_and_can_be_undone():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        before = await svc.pending(HOTEL)
        assert [p["guest_id"] for p in before["register"]] == [str(ids["main"]), str(ids["friend"])]
        assert before["count"] == 2 and before["register"][0]["room_number"] == "101"
        skipped = await svc.skip(HOTEL, ids["reservation"], ids["friend"], "Kabinetda kiritilgan", ids["user"])
        assert skipped["status"] == "skipped" and skipped["note"] == "Kabinetda kiritilgan"
        after = await svc.pending(HOTEL)
        assert [p["guest_id"] for p in after["register"]] == [str(ids["main"])] and after["count"] == 1
        sheet = await svc.sheet(HOTEL, ids["reservation"])
        assert sheet["persons"][1]["state"] == "skipped" and sheet["persons"][1]["form"] is not None
        await svc.unskip(HOTEL, ids["reservation"], ids["friend"])
        assert (await svc.pending(HOTEL))["count"] == 2 and await rows_of(session) == []
        assert fake.calls == []

    run(body)


def test_queue_ignores_guests_who_arrived_before_enabling():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        # Integratsiya mehmon kirgandan KEYIN yoqilgan — eski turish so'ralmaydi
        assert (await svc.pending(HOTEL)) == {"enabled": True, "register": [], "checkout": [], "count": 0}
        # Lekin xodim varaqni ochib, o'zi ro'yxatga olishi mumkin
        assert (await svc.sheet(HOTEL, ids["reservation"]))["persons"][0]["form"] is not None

    run(body, settings={**ENABLED, "enabled_at": (utc_now() - timedelta(minutes=5)).isoformat()})


# --- 4. Chiqarish -------------------------------------------------------------


async def register_two(svc, ids) -> tuple[dict, dict]:
    main = await svc.register(HOTEL, ids["reservation"], ids["main"], local_form(), ids["user"])
    friend = await svc.register(HOTEL, ids["reservation"], ids["friend"], foreign_form(), ids["user"])
    assert main["status"] == friend["status"] == "registered"
    # So'rov oxiridagi commit (get_db) — fon vazifasi boshqa sessiyada ko'radi
    await svc.session.commit()
    return main, friend


def test_manual_checkout_sends_sum_and_payment_type():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        main, friend = await register_two(svc, ids)
        sheet = await svc.sheet(HOTEL, ids["reservation"])
        # Bron summasi ikki mehmonga teng bo'linadi; ko'proq pul karta bilan tushgan
        assert sheet["persons"][0]["checkout"] == {"sum": 450000, "payment_type_id": 4}
        out = await svc.checkout(HOTEL, uuid.UUID(main["id"]), 500_000, 2, ids["user"])
        assert out["status"] == "checked_out" and out["checkout_sum"] == 500000.0
        assert out["checkout_auto"] is False and out["date_visit_off"] == "2026-05-25T17:00:00+05:00"
        sent = fake.calls[-1]
        assert sent["path"] == "/listok/8826912/checkout"
        assert sent["body"] == {"sum": 500000, "payment_type_id": 2}
        assert sent["headers"]["idempotency-key"]
        # Qolgan mehmonga — bron summasining qolgani
        sheet = await svc.sheet(HOTEL, ids["reservation"])
        assert sheet["persons"][0]["state"] == "checked_out" and sheet["persons"][0]["form"] is None
        assert sheet["persons"][1]["checkout"] == {"sum": 400000, "payment_type_id": 4}
        # Takror chaqiruv E-mehmon'ga bormaydi
        calls = len(fake.calls)
        assert (await svc.checkout(HOTEL, uuid.UUID(main["id"]), 1, 2, ids["user"]))["status"] == "checked_out"
        assert len(fake.calls) == calls
        with pytest.raises(ValidationException) as bad:
            await svc.checkout(HOTEL, uuid.UUID(friend["id"]), 100, 9, ids["user"])
        assert bad.value.error_code == "EMEHMON_PAYMENT_TYPE_INVALID"

    run(body)


def test_checkout_failure_is_kept_and_closed_in_cabinet_counts_as_done():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        main, friend = await register_two(svc, ids)
        fake.fail_next = (503, "SERVICE_UNAVAILABLE", {})
        failed = await svc.checkout(HOTEL, uuid.UUID(main["id"]), 450_000, 2, ids["user"])
        assert failed["status"] == "registered" and failed["checkout_error_code"] == "SERVICE_UNAVAILABLE"
        assert failed["checkout_attempts"] == 1 and "ishlamayapti" in failed["checkout_error"]
        first_key = fake.calls[-1]["headers"]["idempotency-key"]
        # O'sha summa bilan qayta urinish — o'sha kalit
        done = await svc.checkout(HOTEL, uuid.UUID(main["id"]), 450_000, 2, ids["user"])
        assert done["status"] == "checked_out" and done["checkout_error"] is None
        assert fake.calls[-1]["headers"]["idempotency-key"] == first_key
        # Kabinetda qo'lda yopilgan — bizda ham yopilgan bo'ladi
        fake.listoks[friend["listok_id"]]["datevisitoff"] = "2026-05-24 09:00:00"
        closed = await svc.checkout(HOTEL, uuid.UUID(friend["id"]), None, None, ids["user"])
        assert closed["status"] == "checked_out" and "avvalroq" in closed["note"]

    run(body)


def test_refresh_follows_cabinet_state():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        main, _friend = await register_two(svc, ids)
        assert (await svc.refresh(HOTEL, uuid.UUID(main["id"])))["status"] == "registered"
        fake.listoks[main["listok_id"]]["datevisitoff"] = "2026-05-24 09:00:00"
        synced = await svc.refresh(HOTEL, uuid.UUID(main["id"]))
        assert synced["status"] == "checked_out" and synced["date_visit_off"] == "2026-05-24 09:00:00"

    run(body)


def test_auto_checkout_only_after_guest_left():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        main, friend = await register_two(svc, ids)
        # Hamma xonada — hech kim chiqarilmaydi
        assert await es.due_checkouts(session) == []
        assert (await svc.pending(HOTEL))["checkout"] == []

        reservation = await session.get(Reservation, ids["reservation"])
        now = utc_now()
        # Hamroh hozirgina "ketdi" deb belgilandi — xato bosilgan bo'lishi mumkin, kutiladi
        reservation.companions = [
            {"guest_id": str(ids["friend"]), "name": "Max", "left_at": now.isoformat()},
        ]
        await session.commit()
        assert await es.due_checkouts(session) == []
        reservation.companions = [
            {"guest_id": str(ids["friend"]), "name": "Max",
             "left_at": (now - timedelta(minutes=10)).isoformat()},
        ]
        await session.commit()
        due = await es.due_checkouts(session)
        assert [str(r.id) for r in due] == [friend["id"]]
        pending = await svc.pending(HOTEL)
        assert [c["id"] for c in pending["checkout"]] == [friend["id"]]
        assert pending["checkout"][0]["auto"] is True and pending["count"] == 0   # o'zi yopiladi

        # Bron yopildi — asosiy mehmon ham chiqariladi
        reservation.status = "CHECKED_OUT"
        await session.commit()
        assert {str(r.id) for r in await es.due_checkouts(session)} == {main["id"], friend["id"]}

    run(body)


def test_due_checkouts_are_not_crowded_out_by_in_house_guests():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        main, friend = await register_two(svc, ids)
        # Xonada turgan ko'p mehmon (boshqa bronlar) — ularning ro'yxati ochiq turadi
        user = ids["user"]
        today = hotel_today()
        for index in range(12):
            guest = Guest(hotel_id=HOTEL, first_name=f"G{index}", last_name="Turibdi")
            session.add(guest)
            await session.flush()
            stay = Reservation(
                hotel_id=HOTEL, branch_id=BRANCH, reservation_number=f"RES-IN-{index}", guest_id=guest.id,
                room_id=ids["room"], check_in_date=today, check_out_date=today + timedelta(days=1),
                status="CHECKED_IN", created_by=user,
            )
            session.add(stay)
            await session.flush()
            session.add(EmehmonRegistration(
                hotel_id=HOTEL, branch_id=BRANCH, reservation_id=stay.id, guest_id=guest.id,
                role="main", status="registered", listok_id=9000 + index, environment="sandbox",
            ))
        reservation = await session.get(Reservation, ids["reservation"])
        reservation.status = "CHECKED_OUT"
        await session.commit()
        # Chegara kichik bo'lsa ham navbatga FAQAT ketganlar tushadi
        due = await es.due_checkouts(session, limit=3)
        assert {str(r.id) for r in due} == {main["id"], friend["id"]}

    run(body)


def test_checkout_sweep_closes_listoks_and_backs_off(monkeypatch):
    async def body(session, ids, fake, maker):
        from app.core import database

        monkeypatch.setattr(database, "_get_session_factory", lambda: maker)
        svc = EmehmonService(session)
        await register_two(svc, ids)
        assert await es.run_checkout_sweep() == 0                 # hamma xonada
        reservation = await session.get(Reservation, ids["reservation"])
        reservation.status = "CHECKED_OUT"
        await session.commit()

        # Birinchi urinish muvaffaqiyatsiz — keyingi tikda darhol takrorlanmaydi
        fake.fail_next = (503, "SERVICE_UNAVAILABLE", {})
        assert await es.run_checkout_sweep() == 1
        session.expire_all()
        by_status = {r.status: r for r in await rows_of(session)}
        assert set(by_status) == {"registered", "checked_out"}
        waiting = by_status["registered"]
        assert waiting.checkout_attempts == 1 and waiting.checkout_error_code == "SERVICE_UNAVAILABLE"
        assert await es.run_checkout_sweep() == 0
        assert await es.due_checkouts(session, now=utc_now() + timedelta(minutes=2)) == [waiting]

        waiting.checkout_tried_at = utc_now() - timedelta(minutes=5)
        await session.commit()
        assert await es.run_checkout_sweep() == 1
        session.expire_all()
        rows = await rows_of(session)
        assert [r.status for r in rows] == ["checked_out", "checked_out"]
        assert all(r.checkout_by is None for r in rows)            # avtomatik
        # Bron summasi (900 000) ikki mehmonga bo'linib ketgan; to'lov turi — karta (Uzcard)
        sums = sorted(c["body"]["sum"] for c in fake.calls if c["path"].endswith("/checkout") and c["body"])
        assert sums[-2:] == [450000, 450000]
        assert {c["body"]["payment_type_id"] for c in fake.calls if c["path"].endswith("/checkout")} == {4}
        assert all(l["datevisitoff"] for l in fake.listoks.values())
        assert serialize_auto(rows)

    def serialize_auto(rows) -> bool:
        return all(es.serialize(r)["checkout_auto"] for r in rows)

    run(body)


def test_checkout_sweep_respects_switch_environment_and_attempt_limit(monkeypatch):
    async def body(session, ids, fake, maker):
        from app.core import database

        monkeypatch.setattr(database, "_get_session_factory", lambda: maker)
        svc = EmehmonService(session)
        await register_two(svc, ids)
        reservation = await session.get(Reservation, ids["reservation"])
        reservation.status = "CHECKED_OUT"
        await session.commit()
        calls = len(fake.calls)

        # «Avtomatik chiqarish» o'chirilgan — fon vazifasi tegmaydi, xodimga ko'rinadi
        await svc.save_settings(HOTEL, {"auto_checkout": False})
        await session.commit()
        assert await es.run_checkout_sweep() == 0 and len(fake.calls) == calls
        pending = await svc.pending(HOTEL)
        assert len(pending["checkout"]) == 2 and pending["count"] == 2
        assert all(c["auto"] is False for c in pending["checkout"])

        # Boshqa muhitda (ishchi) yaratilgan ro'yxatga sinov sozlamasi bilan tegilmaydi
        await svc.save_settings(HOTEL, {"auto_checkout": True})
        rows = await rows_of(session)
        rows[0].environment = "production"
        rows[1].checkout_attempts = es.AUTO_CHECKOUT_ATTEMPTS      # urinishlar tugagan
        await session.commit()
        assert await es.run_checkout_sweep() == 0 and len(fake.calls) == calls
        with pytest.raises(ValidationException) as mismatch:
            await svc.checkout(HOTEL, rows[0].id, 1, 2, ids["user"])
        assert mismatch.value.error_code == "EMEHMON_ENVIRONMENT_MISMATCH"
        # Urinishlari tugagani xodim e'tiborini kutadi
        pending = await svc.pending(HOTEL)
        assert {c["id"]: c["auto"] for c in pending["checkout"]}[str(rows[1].id)] is False

    run(body)


def test_returned_companion_needs_new_registration():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        _main, friend = await register_two(svc, ids)
        await svc.checkout(HOTEL, uuid.UUID(friend["id"]), 0, 1, ids["user"])
        reservation = await session.get(Reservation, ids["reservation"])
        reservation.companions = [{
            "guest_id": str(ids["friend"]), "name": "Max",
            "returned_at": (utc_now() + timedelta(seconds=5)).isoformat(),
        }]
        await session.commit()
        person = (await svc.sheet(HOTEL, ids["reservation"]))["persons"][1]
        assert person["state"] == "none" and person["form"] is not None
        # Avvalgi ro'yxatdagi shaxsiy ma'lumot qayta to'ldiriladi, Chegara xizmati id — yo'q
        assert person["form"]["passport_date"] == "2021-03-15" and person["form"]["citizen_id"] == 76
        assert person["form"]["person_id"] == ""
        again = await svc.register(
            HOTEL, ids["reservation"], ids["friend"],
            foreign_form(arrival=ep.tashkent_iso(utc_now())), ids["user"])
        assert again["status"] == "registered" and again["id"] != friend["id"]
        assert [r.status for r in await rows_of(session)].count("registered") == 2

    run(body)


def test_journal_filters_by_status_and_search():
    async def body(session, ids, fake, maker):
        svc = EmehmonService(session)
        main, friend = await register_two(svc, ids)
        await svc.checkout(HOTEL, uuid.UUID(friend["id"]), 0, 1, ids["user"])
        everything = await svc.journal(HOTEL)
        assert everything["total"] == 2 and everything["items"][0]["reservation_number"] == "RES-7"
        assert (await svc.journal(HOTEL, status="checked_out"))["items"][0]["id"] == friend["id"]
        assert (await svc.journal(HOTEL, search="nasir"))["items"][0]["id"] == main["id"]
        assert (await svc.journal(HOTEL, search="C01X"))["total"] == 1
        assert (await svc.journal(HOTEL, search=main["reg_num"]))["total"] == 1
        assert (await svc.journal(uuid.uuid4()))["total"] == 0

    run(body)


# --- 5. Ulanish joylari -------------------------------------------------------


def test_network_routes_do_not_hold_branch_write_lock():
    from app.presentation.api.v1 import emehmon as api
    from app.presentation.middleware.auth import LOCK_EXEMPT_PREFIXES

    assert "/api/v1/emehmon/gate/" in LOCK_EXEMPT_PREFIXES
    writers = {
        route.path for route in api.router.routes
        if {"POST", "PUT", "DELETE", "PATCH"} & set(route.methods)
    }
    # Qulf oladigan (tarmoqqa CHIQMAYDIGAN) yozuvchi yo'llar — faqat shular
    assert {p for p in writers if not p.startswith("/gate/")} == {
        "/settings", "/reservations/{reservation_id}/skip",
    }
    assert {p for p in writers if p.startswith("/gate/")} == {
        "/gate/test", "/gate/lookup", "/gate/reservations/{reservation_id}/register",
        "/gate/reservations/{reservation_id}/link",
        "/gate/registrations/{registration_id}/checkout",
        "/gate/registrations/{registration_id}/refresh",
    }


def test_request_journal_masks_secret_and_pinfl():
    from app.superadmin import api_log

    masked = api_log.redact(json.dumps({
        "username": USERNAME, "secret": SECRET,
        "form": {"pinfl": "31234567890123", "passport_number": "AB1234567"},
    }))
    assert SECRET not in masked and "31234567890123" not in masked
    # Tashxis uchun kerakli maydonlar qoladi
    assert USERNAME in masked and "AB1234567" in masked


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
