"""Kassani KUNLIK KESIM bo'yicha topshirish.

Xodim hamma pulni emas, tanlangan kesim momentigacha bo'lgan kunlarni
topshiradi (masalan kesim 00:05 bo'lsa — 00:05 dan 00:05 gacha), ketma-ket:
eng eskisidan boshlab. Sessiya kesim momentida yopiladi, kesimdan keyingi
tushum esa o'sha momentdan boshlanadigan yangi sessiyada qoladi. Kesim
tanlanmasa — avvalgidek hamma pul topshiriladi.

Bazasiz: xotiradagi kichik "baza" so'rov shartlarini (xodim, vaqt oynasi)
haqiqatan qo'llaydi — guruhlangan hisob (`cash_breakdown`) va kunlarga
bo'lingan hisob (`plan_cuts`) bir-biriga mos kelishi shu bilan tekshiriladi.
"""
import asyncio
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.services import shift_service as ss
from app.application.services.notification_service import NotificationService
from app.application.services.shift_service import (
    ShiftService,
    cut_boundaries,
    cut_business_day,
    parse_day_close,
    plan_cuts,
)
from app.core.exceptions import ValidationException

HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()
DILNOZA, AZIZ, ADMIN_ID = (uuid.uuid4() for _ in range(3))
ADMIN = {"id": str(ADMIN_ID), "user_type": "ADMIN", "permissions": []}
ME = {"id": str(DILNOZA), "user_type": "EMPLOYEE", "permissions": ["reservation.create"]}


def local(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """2026-yil noyabr, Toshkent vaqti (+05:00) → UTC."""
    return datetime(2026, 11, day, hour, minute, second, tzinfo=timezone.utc) - timedelta(hours=5)


# Misol: kesim 00:05. Smena 15-noyabr 09:12 da ochilgan, hozir 18-noyabr 08:40
START = local(15, 9, 12)
NOW = local(18, 8, 40)
CUT_16, CUT_17, CUT_18 = local(16, 0, 5), local(17, 0, 5), local(18, 0, 5)
GAP = timedelta(microseconds=1)


class FakeDatetime(datetime):
    """`datetime.now()` — qotirilgan vaqt (qolgan hammasi haqiqiy)."""

    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(ss, "datetime", FakeDatetime)
    monkeypatch.setattr(ss.app_settings, "APP_TZ_OFFSET_MINUTES", 300)


# ------------------------------------------------------------ xotiradagi baza

class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


TABLES = {"payments": "payments", "shop_sale_payments": "shop", "expenses": "expenses"}


class Db:
    """Pul harakatlari: (tur, xodim, usul, summa, vaqt). So'rovdagi xodim va
    vaqt oynasi shartlari haqiqatan qo'llanadi."""

    def __init__(self, moves=(), sessions=(), session_obj=None):
        self.moves = list(moves)
        self.sessions = list(sessions)
        self.session_obj = session_obj
        self.added: list = []

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM shift_sessions" in sql:
            return Rows([s for s in self.sessions if s.status == "ACTIVE"])
        for table, kind in TABLES.items():
            if f"FROM {table}" in sql:
                return Rows(self._money(stmt, sql, kind))
        raise AssertionError(sql)

    def _money(self, stmt, sql, kind):
        params = stmt.compile().params
        user = next(v for k, v in params.items() if k.startswith("created_by"))
        bounds = sorted(v for v in params.values() if isinstance(v, datetime))
        start, end = bounds[0], bounds[-1]
        rows = [
            (method, Decimal(str(amount)), at)
            for k, who, method, amount, at in self.moves
            if k == kind and who == user and start <= at <= end
        ]
        if "GROUP BY" in sql:
            totals: dict = {}
            for method, amount, _ in rows:
                totals[method] = totals.get(method, Decimal("0")) + amount
            return list(totals.items())
        return rows

    async def get(self, model, key):
        if model.__name__ == "User":
            return SimpleNamespace(first_name="Dilnoza", last_name="R")
        if model.__name__ == "ShiftSession":
            return self.session_obj
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


def make_session(started=START, opening=0, status="ACTIVE", continue_after_end=False):
    return SimpleNamespace(
        id=uuid.uuid4(), hotel_id=HOTEL, branch_id=BRANCH, user_id=DILNOZA,
        status=status, started_at=started, ended_at=None,
        opening_cash=Decimal(str(opening)), expected_cash=None, counted_cash=None,
        cash_diff=None, accepted_by=None, accepted_at=None, force_closed=False,
        corrections=None, notes=None, closed_by=None,
        continue_after_end=continue_after_end, pending_approval=None,
        handed_at=None, carried_cash=None,
    )


def make_service(monkeypatch, s, moves=(), *, approval=False, day_close="00:05"):
    svc = ShiftService.__new__(ShiftService)
    svc.session = Db(moves=moves, sessions=[s], session_obj=s)

    async def get_settings(hotel_id):
        return {
            "mode": "cash",
            "day_close": day_close,
            "day_close_required": True,
            "handover_approval": approval,
        }

    svc.get_settings = get_settings
    approvals: list[tuple[str, str]] = []

    async def fake_notify_approvers(session, title, body):
        approvals.append((title, body))

    svc._notify_approvers = fake_notify_approvers

    async def fake_notify(self, **kw):
        return None

    monkeypatch.setattr(NotificationService, "notify", fake_notify)
    return svc, approvals


def pay(amount, at, method="CASH", who=DILNOZA):
    return ("payments", who, method, amount, at)


def shop(amount, at, method="CASH", who=DILNOZA):
    return ("shop", who, method, amount, at)


def spend(amount, at, method="CASH", who=DILNOZA):
    return ("expenses", who, method, amount, at)


# Uch kun: 15-kuni 1 200 000, 16-kuni 850 000 (xarajat 50 000), 17-kuni
# 400 000, kesimdan keyin (18-kuni ertalab) 250 000. Boshqa xodimning puli va
# naqdsiz to'lovlar kassaga kirmaydi.
MOVES = [
    pay(1_200_000, local(15, 14)),
    pay(700_000, local(16, 11)),
    shop(200_000, local(16, 18)),
    spend(50_000, local(16, 20)),
    pay(300_000, local(16, 12), method="ONLINE"),
    pay(400_000, local(17, 13)),
    pay(250_000, local(18, 7)),
    pay(999_999, local(16, 12), who=AZIZ),
]


# ------------------------------------------------------------ kesim momentlari

def test_parse_day_close():
    assert parse_day_close("00:05") == time(0, 5)
    assert parse_day_close("23:00") == time(23, 0)
    # Buzuq qiymat hisobni yiqitmaydi — standart kesim
    assert parse_day_close(None) == time(0, 0)
    assert parse_day_close("25:99") == time(0, 0)
    assert parse_day_close("tun") == time(0, 0)


def test_boundaries_inside_session_in_local_time():
    """Kesim 00:05 (Toshkent): 15-kuni ochilgan sessiyaga 3 ta kesim tushadi."""
    assert cut_boundaries(START, NOW, "00:05") == [CUT_16, CUT_17, CUT_18]


def test_no_boundary_when_session_opened_after_last_cut():
    assert cut_boundaries(local(18, 0, 30), NOW, "00:05") == []
    assert cut_boundaries(None, NOW, "00:05") == []


def test_boundary_at_exact_edges():
    # Kesim hozirgina keldi — u ham topshiriladigan kesim
    assert cut_boundaries(START, CUT_16, "00:05") == [CUT_16]
    assert cut_boundaries(START, CUT_16 - timedelta(seconds=1), "00:05") == []
    # Sessiya aynan kesimda (yoki undan bir mikrosekund keyin) boshlangan:
    # o'sha kesim endi orqada qolgan, keyingisi — ertasi kuni
    assert cut_boundaries(CUT_16, NOW, "00:05") == [CUT_17, CUT_18]
    assert cut_boundaries(CUT_16 + GAP, NOW, "00:05") == [CUT_17, CUT_18]


def test_boundaries_with_evening_cut_and_other_timezone(monkeypatch):
    assert cut_boundaries(START, NOW, "23:00") == [local(15, 23), local(16, 23), local(17, 23)]
    # Vaqt zonasi sozlamadan: UTC da kesim 00:05 UTC bo'ladi
    monkeypatch.setattr(ss.app_settings, "APP_TZ_OFFSET_MINUTES", 0)
    utc = datetime(2026, 11, 17, 0, 5, tzinfo=timezone.utc)
    assert cut_boundaries(utc - timedelta(hours=3), utc + timedelta(hours=1), "00:05") == [utc]


def test_business_day_closed_by_the_cut():
    # Tungi kesim kechagi kunni yopadi
    assert cut_business_day(CUT_18) == date(2026, 11, 17)
    assert cut_business_day(local(18, 8, 0)) == date(2026, 11, 17)
    assert cut_business_day(local(18, 12, 0)) == date(2026, 11, 17)
    # Kechki kesim — shu kunning o'zini
    assert cut_business_day(local(17, 23, 0)) == date(2026, 11, 17)
    assert cut_business_day(local(17, 12, 1)) == date(2026, 11, 17)


# ---------------------------------------------------------------- hisob (sof)

def rows_of(moves, who=DILNOZA):
    return [(kind, method, Decimal(str(amount)), at) for kind, u, method, amount, at in moves if u == who]


def test_plan_splits_cash_by_cut_days():
    plans = plan_cuts(Decimal("100000"), [CUT_16, CUT_17, CUT_18], rows_of(MOVES), START)

    assert [p["day"] for p in plans] == [date(2026, 11, 15), date(2026, 11, 16), date(2026, 11, 17)]
    assert [p["from_at"] for p in plans] == [START, CUT_16, CUT_17]
    # Har kunning o'z sof naqd harakati
    assert [p["day_cash"] for p in plans] == [1_200_000, 850_000, 400_000]
    # Kesimgacha yig'ilgan: boshlang'ich + tushum − xarajat
    assert [p["expected_cash"] for p in plans] == [1_300_000, 2_150_000, 2_550_000]
    # Kesimdan keyin tushum bor — hamma pul topshiriladi, kassada hech narsa
    # "qoldirilmaydi"
    assert [p["carried_cash"] for p in plans] == [0, 0, 0]
    assert [p["due_cash"] for p in plans] == [1_300_000, 2_150_000, 2_550_000]
    assert plans[-1]["after_cash"] == 250_000

    second = plans[1]
    assert second["payments_cash"] == 1_900_000
    assert second["shop_cash"] == 200_000
    assert second["expenses_cash"] == 50_000
    # Naqdsiz to'lov kassaga kirmaydi, lekin usullar tarkibida ko'rinadi
    assert second["methods"]["ONLINE"]["payments"] == 300_000
    assert "ONLINE" not in plans[0]["methods"]


def test_move_exactly_at_cut_belongs_to_the_closed_day():
    plans = plan_cuts(Decimal("0"), [CUT_16, CUT_17], rows_of([pay(10, CUT_16), pay(7, CUT_16 + GAP)]), START)
    assert [p["day_cash"] for p in plans] == [10, 7]


def test_expense_after_cut_is_left_in_drawer_not_a_shortage():
    """Kesimdan keyin (ertalab) kassadan 50 000 xarajat qilingan, tushum
    bo'lmagan: kechagi 1 000 000 dan kassada 950 000 qolgan. Topshiriladigan
    summa 950 000 — 50 000 yangi kassaning boshlang'ichi bo'ladi."""
    moves = [pay(1_000_000, local(17, 15)), spend(50_000, local(18, 7))]
    (plan,) = plan_cuts(Decimal("0"), [CUT_18], rows_of(moves), local(17, 9))
    assert plan["expected_cash"] == 1_000_000
    assert plan["after_cash"] == -50_000
    assert plan["carried_cash"] == 50_000
    assert plan["due_cash"] == 950_000


def test_carry_never_exceeds_what_the_drawer_had():
    moves = [pay(100_000, local(17, 15)), spend(300_000, local(18, 7))]
    (plan,) = plan_cuts(Decimal("0"), [CUT_18], rows_of(moves), local(17, 9))
    assert plan["carried_cash"] == 100_000
    assert plan["due_cash"] == 0
    # Kesimgacha kassa manfiy bo'lsa — "qoldiriladigan" pulning o'zi yo'q
    moves = [spend(40_000, local(17, 15)), spend(10_000, local(18, 7))]
    (plan,) = plan_cuts(Decimal("0"), [CUT_18], rows_of(moves), local(17, 9))
    assert plan["carried_cash"] == 0
    assert plan["due_cash"] == -40_000


def test_plan_matches_grouped_breakdown_for_every_cut(monkeypatch):
    """Kunlarga bo'lingan hisob va sessiya shu kesimda yopilgandagi odatdagi
    hisob (`cash_breakdown`) bir xil natija beradi."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    plans = asyncio.run(svc.cut_plans(s, "00:05", NOW))
    assert len(plans) == 3
    for plan in plans:
        s.ended_at = plan["cut_at"]
        breakdown = asyncio.run(svc.cash_breakdown(s))
        assert breakdown["expected_cash"] == plan["expected_cash"]
        assert breakdown["payments_cash"] == plan["payments_cash"]
        assert breakdown["expenses_cash"] == plan["expenses_cash"]


def test_cut_plans_skip_database_when_no_cut(monkeypatch):
    s = make_session(started=local(18, 1))
    svc, _ = make_service(monkeypatch, s, MOVES)

    async def boom(*a, **kw):
        raise AssertionError("kesim yo'q — baza so'ralmasligi kerak")

    svc._cash_rows = boom
    assert asyncio.run(svc.cut_plans(s, "00:05", NOW)) == []


def test_only_recent_cuts_are_offered(monkeypatch):
    """Juda uzoq ochiq turgan sessiya: oxirgi kesimlar ko'rsatiladi, eskilari
    birinchi variantga qo'shilib ketadi (topshirish ketma-ket)."""
    s = make_session(started=NOW - timedelta(days=200), opening=5)
    svc, _ = make_service(monkeypatch, s, [pay(1000, NOW - timedelta(days=150))])
    plans = asyncio.run(svc.cut_plans(s, "00:05", NOW))
    assert len(plans) == ss.MAX_CUT_OPTIONS
    assert plans[0]["from_at"] == s.started_at
    assert plans[0]["expected_cash"] == 1005
    assert plans[-1]["cut_at"] == CUT_18


# ------------------------------------------------------- expected-cash javobi

def test_expected_cash_lists_cut_options(monkeypatch):
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    out = asyncio.run(svc.my_expected_cash(HOTEL, ME))

    # Avvalgi maydonlar o'zgarmagan: hozir kassada bo'lishi kerak bo'lgan pul
    assert out["expected_cash"] == 2_800_000.0
    cuts = out["cuts"]
    assert [c["day"] for c in cuts] == ["2026-11-15", "2026-11-16", "2026-11-17"]
    assert [c["cut_at"] for c in cuts] == [b.isoformat() for b in (CUT_16, CUT_17, CUT_18)]
    assert [c["due_cash"] for c in cuts] == [1_300_000.0, 2_150_000.0, 2_550_000.0]
    assert cuts[1]["non_cash_income"] == 300_000.0
    assert isinstance(cuts[1]["methods"], list)
    assert cuts[1]["methods"][0]["method"] == "CASH"
    # Qatorlar yig'indisi = kassadagi jami pul
    total = cuts[0]["opening_cash"] + sum(c["day_cash"] for c in cuts) + cuts[-1]["after_cash"]
    assert total == out["expected_cash"]


def test_expected_cash_without_cuts_is_empty_list(monkeypatch):
    s = make_session(started=local(18, 1))
    svc, _ = make_service(monkeypatch, s, [pay(250_000, local(18, 7))])
    out = asyncio.run(svc.my_expected_cash(HOTEL, ME))
    assert out["cuts"] == []
    assert out["expected_cash"] == 250_000.0


# ----------------------------------------------------------- kassani topshirish

def test_close_cash_by_cut_hands_over_days_up_to_the_cut(monkeypatch):
    """Kechagi kun kesimigacha topshirish: sessiya kesim momentida yopiladi,
    bugungi 250 000 yangi sessiyada qoladi."""
    s = make_session(opening=100_000, continue_after_end=True)
    svc, _ = make_service(monkeypatch, s, MOVES)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 2_550_000.0, cut_at=CUT_18))

    assert s.status == "CLOSED"
    assert s.ended_at == CUT_18
    assert s.handed_at == NOW
    assert s.expected_cash == 2_550_000
    assert s.counted_cash == 2_550_000
    assert s.cash_diff == 0
    assert s.carried_cash == 0
    assert report["handed_at"] == NOW.isoformat()
    assert report["expected_cash"] == 2_550_000.0

    (new_s,) = svc.session.added
    assert new_s.status == "ACTIVE"
    assert new_s.user_id == DILNOZA
    # Yangi sessiya kesimdan boshlanadi — ustma-ust tushmasdan
    assert new_s.started_at == CUT_18 + GAP
    assert new_s.opening_cash == 0
    assert new_s.continue_after_end is True
    assert report["new_session"]["started_at"] == (CUT_18 + GAP).isoformat()
    # Kesimdan keyingi tushum yangi sessiyada, yopilganida esa yo'q
    assert asyncio.run(svc.cash_breakdown(new_s))["expected_cash"] == 250_000
    assert asyncio.run(svc.cash_breakdown(s))["expected_cash"] == 2_550_000


def test_close_cash_by_older_cut_then_next_day_sequentially(monkeypatch):
    """Ketma-ket: avval eng eski kun, keyin navbatdagisi — har biri alohida
    topshirish. Yig'indi hamma pulni bir yo'la topshirgandagi bilan teng."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)

    asyncio.run(svc.close_cash(HOTEL, ME, 1_300_000.0, cut_at=CUT_16))
    assert (s.ended_at, s.expected_cash, s.cash_diff) == (CUT_16, 1_300_000, 0)
    second = svc.session.added[-1]
    assert second.started_at == CUT_16 + GAP

    # Davom etayotgan sessiyada birinchi kesim endi yo'q
    svc.session.sessions = [second]
    plans = asyncio.run(svc.cut_plans(second, "00:05", NOW))
    assert [p["cut_at"] for p in plans] == [CUT_17, CUT_18]
    assert [p["due_cash"] for p in plans] == [850_000, 1_250_000]

    asyncio.run(svc.close_cash(HOTEL, ME, 850_000.0, cut_at=CUT_17))
    assert (second.ended_at, second.expected_cash) == (CUT_17, 850_000)
    third = svc.session.added[-1]
    assert third.started_at == CUT_17 + GAP
    # Qolgani: 17-kun (400 000) + kesimdan keyin (250 000)
    assert asyncio.run(svc.cash_breakdown(third))["expected_cash"] == 650_000
    assert 1_300_000 + 850_000 + 650_000 == 2_800_000


def test_close_cash_by_cut_records_difference_on_the_employee(monkeypatch):
    s = make_session()
    svc, _ = make_service(monkeypatch, s, MOVES)
    asyncio.run(svc.close_cash(HOTEL, ME, 2_400_000.0, cut_at=CUT_18))
    assert s.expected_cash == 2_450_000
    assert s.cash_diff == -50_000


def test_close_cash_by_cut_leaves_spent_money_in_drawer(monkeypatch):
    s = make_session(started=local(17, 9))
    moves = [pay(1_000_000, local(17, 15)), spend(50_000, local(18, 7))]
    svc, _ = make_service(monkeypatch, s, moves)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 950_000.0, cut_at=CUT_18))

    assert s.expected_cash == 950_000
    assert s.carried_cash == 50_000
    assert s.cash_diff == 0  # kamomad emas
    assert report["carried_cash"] == 50_000.0
    (new_s,) = svc.session.added
    assert new_s.opening_cash == 50_000
    # Yangi kassa: 50 000 boshlang'ich − 50 000 xarajat = 0 (kassa bo'sh)
    assert asyncio.run(svc.cash_breakdown(new_s))["expected_cash"] == 0


def test_close_cash_rejects_unknown_cut(monkeypatch):
    s = make_session()
    svc, _ = make_service(monkeypatch, s, MOVES)
    for wrong in (CUT_18 + timedelta(minutes=5), local(18, 8, 0), START):
        with pytest.raises(ValidationException) as err:
            asyncio.run(svc.close_cash(HOTEL, ME, 1.0, cut_at=wrong))
        assert err.value.error_code == "INVALID_CUT"
    # Hech narsa o'zgarmagan
    assert s.status == "ACTIVE" and s.ended_at is None and s.handed_at is None
    assert svc.session.added == []


def test_close_cash_accepts_cut_without_timezone_and_rounding(monkeypatch):
    s = make_session()
    svc, _ = make_service(monkeypatch, s, MOVES)
    naive = CUT_17.replace(tzinfo=None, microsecond=400_000)
    asyncio.run(svc.close_cash(HOTEL, ME, 1.0, cut_at=naive))
    assert s.ended_at == CUT_17


def test_close_cash_without_cut_is_unchanged(monkeypatch):
    """Kesim tanlanmagan — avvalgidek: hamma pul, yangi kassa hozirdan va 0 dan."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 2_800_000.0))

    assert s.status == "CLOSED"
    assert s.ended_at == NOW
    assert s.handed_at is None and s.carried_cash is None
    assert s.expected_cash == 2_800_000
    assert report["handed_at"] is None
    (new_s,) = svc.session.added
    assert new_s.started_at == NOW
    assert new_s.opening_cash == 0


def test_end_shift_ignores_cuts(monkeypatch):
    """Smenani tugallashda kassa keyingi xodimga butunligicha o'tadi."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    asyncio.run(svc.end_shift(HOTEL, ME, 2_800_000.0))
    assert s.status == "PENDING_HANDOVER"
    assert s.ended_at == NOW and s.handed_at is None
    assert s.expected_cash == 2_800_000


def test_close_cash_request_cut_is_optional():
    """Eski klient `cut_at` yubormaydi — so'rov avvalgidek qabul qilinadi."""
    from app.presentation.api.v1.shifts import CloseCashRequest

    assert CloseCashRequest(counted_cash=5).cut_at is None
    assert CloseCashRequest(counted_cash=5, cut_at=CUT_18.isoformat()).cut_at == CUT_18


# ------------------------------------------------------------- tasdiq rejimi

def test_cut_handover_waits_for_approval_then_continues_from_cut(monkeypatch):
    s = make_session(started=local(17, 9), continue_after_end=True)
    moves = [pay(1_000_000, local(17, 15)), spend(50_000, local(18, 7))]
    svc, approvals = make_service(monkeypatch, s, moves, approval=True)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 950_000.0, cut_at=CUT_18))

    assert s.status == "PENDING_HANDOVER"
    assert s.pending_approval == "close_cash"
    assert s.ended_at == CUT_18 and s.handed_at == NOW
    assert svc.session.added == []  # yangi sessiya tasdiqdan keyin
    assert "new_session" not in report
    # Rahbar qaysi kesimgacha topshirilganini xabarning o'zida ko'radi
    assert "18.11 00:05 kesimigacha" in approvals[0][1]

    result = asyncio.run(svc.approve_handover(HOTEL, ADMIN, s.id))

    assert s.status == "CLOSED" and s.accepted_by == ADMIN_ID
    (new_s,) = svc.session.added
    # Tasdiq qachon bo'lmasin — kassir kesim momentidan davom etadi
    assert new_s.started_at == CUT_18 + GAP
    assert new_s.opening_cash == 50_000
    assert new_s.continue_after_end is True
    assert result["new_session"]["opening_cash"] == 50_000.0


def test_force_closing_awaited_cut_handover_keeps_later_takings(monkeypatch):
    """Rahbar tasdiqlash o'rniga pulni olib majburiy yopsa ham kassirning
    kesimdan keyingi tushumi egasiz qolmaydi."""
    s = make_session()
    svc, _ = make_service(monkeypatch, s, MOVES, approval=True)
    asyncio.run(svc.close_cash(HOTEL, ME, 2_450_000.0, cut_at=CUT_18))

    report = asyncio.run(svc.force_close(HOTEL, ADMIN, s.id, hand_over=False))

    assert s.status == "CLOSED" and s.force_closed is True
    assert s.ended_at == CUT_18
    (new_s,) = svc.session.added
    assert new_s.started_at == CUT_18 + GAP
    assert report["new_session"]["id"] == str(new_s.id)
    assert asyncio.run(svc.cash_breakdown(new_s))["expected_cash"] == 250_000


def test_force_close_with_handover_still_waits_for_approval(monkeypatch):
    s = make_session()
    svc, _ = make_service(monkeypatch, s, MOVES, approval=True)
    asyncio.run(svc.close_cash(HOTEL, ME, 2_450_000.0, cut_at=CUT_18))

    report = asyncio.run(svc.force_close(HOTEL, ADMIN, s.id, hand_over=True))

    assert s.status == "PENDING_HANDOVER" and s.pending_approval == "close_cash"
    assert svc.session.added == []
    assert "new_session" not in report


def test_force_close_of_ordinary_session_opens_nothing(monkeypatch):
    """Oddiy (kesimsiz) sessiyani majburiy yopish — avvalgidek."""
    s = make_session()
    svc, _ = make_service(monkeypatch, s, MOVES)
    report = asyncio.run(svc.force_close(HOTEL, ADMIN, s.id, hand_over=False))
    assert s.status == "CLOSED"
    assert svc.session.added == []
    assert "new_session" not in report
