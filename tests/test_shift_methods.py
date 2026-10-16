"""Smena to'lov usullari tarkibi — naqdsiz turlar ham smenaga bog'langan.

Foydalanuvchi talabi: kassa topshirilgandan so'ng naqtdan boshqa to'lov
turlari (masalan Online to'lovlar) ham naqd kabi har yangi smenada 0 dan
boshlansin.

Naqdsiz pul jismonan o'tmaydi (kassada turmaydi), shuning uchun u smenaning
KO'RSATKICHI bo'lib qoladi: summalar sessiya oynasi (created_by + vaqt)
bo'yicha hisoblanadi va yangi sessiya ochilishi bilan tabiiy 0 dan
boshlanadi. Bu testlar aynan shu qoidani va naqd hisobi o'zgarmaganini
qulflaydi.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.application.services.shift_service import ShiftService, _method_key

HOTEL = uuid.uuid4()
BRANCH = uuid.uuid4()
DILNOZA, AZIZ = uuid.uuid4(), uuid.uuid4()
T0 = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)
ADMIN = {"id": str(uuid.uuid4()), "user_type": "ADMIN", "permissions": []}


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


class Db:
    """Guruhlangan so'rovlarni jadval nomi bo'yicha javoblaydi.

    Ro'yxat elementlari `cash_breakdown` uchun (usul, summa), `methods_for_
    sessions` uchun esa (created_by, usul, summa, vaqt) shaklida beriladi.
    """

    def __init__(
        self, payments=(), shop=(), expenses=(), sessions=(), users=(), join_rows=()
    ):
        self.payments = list(payments)
        self.shop = list(shop)
        self.expenses = list(expenses)
        self.sessions = list(sessions)
        self.users = list(users)
        self.join_rows = list(join_rows)
        self.sql = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "FROM shop_sale_payments" in sql:
            return Rows(self.shop)
        if "FROM payments" in sql:
            return Rows(self.payments)
        if "FROM expenses" in sql:
            return Rows(self.expenses)
        if "JOIN users" in sql:
            return Rows(self.join_rows)
        if "FROM users" in sql:
            return Rows(self.users)
        if "FROM shift_sessions" in sql:
            return Rows(self.sessions)
        if "FROM branches" in sql:
            return Rows([])
        raise AssertionError(sql)


def session_obj(user=DILNOZA, *, start=T0, end=None, opening=0):
    return SimpleNamespace(
        id=uuid.uuid4(), hotel_id=HOTEL, branch_id=BRANCH, user_id=user,
        status="CLOSED" if end else "ACTIVE", started_at=start, ended_at=end,
        opening_cash=Decimal(str(opening)), expected_cash=None, counted_cash=None,
        cash_diff=None, accepted_by=None, accepted_at=None, force_closed=False,
        pending_approval=None,
        corrections=None, notes=None, closed_by=None, continue_after_end=False,
    )


def service(**kw):
    svc = ShiftService.__new__(ShiftService)
    svc.session = Db(**kw)
    return svc


# ------------------------------------------------- naqd hisobi o'zgarmagan --

def test_cash_part_is_unchanged_and_methods_are_grouped():
    s = session_obj(end=T0 + timedelta(hours=8), opening=100000)
    svc = service(
        payments=[
            ("CASH", 300000),
            ("ONLINE", 120000),
            ("ONLINE", 20000),
            ("CREDIT_CARD", 50000),
        ],
        shop=[("CASH", 40000), ("CARD", 60000)],
        expenses=[("CASH", 25000)],
    )
    out = asyncio.run(svc.cash_breakdown(s))

    # Naqd balans formulasi AYNAN o'zgarmagan
    assert out["opening_cash"] == Decimal("100000")
    assert out["payments_cash"] == Decimal("300000")  # faqat CASH bo'laklari
    assert out["shop_cash"] == Decimal("40000")
    assert out["expenses_cash"] == Decimal("25000")
    assert out["expected_cash"] == Decimal("415000")  # 100k + 300k + 40k − 25k

    # Usullar bo'yicha tarkib: CASH birinchi, keyin tushum kamayishi bo'yicha
    assert [r["method"] for r in out["methods"]] == [
        "CASH",
        "ONLINE",
        "CARD",
        "CREDIT_CARD",
    ]
    cash = out["methods"][0]
    assert cash["income"] == 340000.0 and cash["expenses"] == 25000.0
    online = out["methods"][1]
    assert online["payments"] == 140000.0 and online["income"] == 140000.0
    assert out["non_cash_income"] == 250000.0  # 140k + 60k + 50k


def test_legacy_and_blank_methods_are_normalized():
    s = session_obj(end=T0 + timedelta(hours=2))
    svc = service(
        payments=[("cash", 5000), ("CASH", 1000)],
        shop=[(None, 7000)],
    )
    out = asyncio.run(svc.cash_breakdown(s))

    # Kichik harfli eski yozuv ham naqd kassaga tushadi
    assert out["payments_cash"] == Decimal("6000")
    assert [r["method"] for r in out["methods"]] == ["CASH", "UNKNOWN"]
    assert out["methods"][1]["shop"] == 7000.0
    # Usuli yo'q (NULL) yozuv naqd DEB hisoblanmaydi — kassa raqamlari
    # faqat tasdiqlangan naqdni ko'rsatadi; tushumda UNKNOWN bo'lib qoladi
    assert out["non_cash_income"] == 7000.0
    assert _method_key(None) == "UNKNOWN" and _method_key("card") == "CARD"


def test_new_session_starts_from_zero_just_like_cash():
    """Yangi sessiyada hech narsa yo'q — barcha usullar 0 dan boshlanadi.

    Pul jismonan o'tmagani uchun "boshlang'ich" faqat naqdga tegishli:
    naqdsiz usullar ro'yxati bo'sh qaytadi va faqat shu sessiyada qabul
    qilingan to'lovlar paydo bo'lganda to'ladi.
    """
    s = session_obj(opening=50000, end=T0 + timedelta(hours=1))
    out = asyncio.run(service().cash_breakdown(s))
    assert out["expected_cash"] == Decimal("50000")
    assert out["methods"] == [] and out["non_cash_income"] == 0.0
    assert out["payments_cash"] == Decimal("0") and out["shop_cash"] == Decimal("0")


# --------------------------------------------------- tarix uchun to'plam --

def test_methods_are_assigned_to_the_owning_session_only():
    """Bitta xodimning ketma-ket sessiyalari aralashib ketmasligi kerak."""
    s1 = session_obj(DILNOZA, start=T0, end=T0 + timedelta(hours=2))
    s2 = session_obj(AZIZ, start=T0, end=T0 + timedelta(hours=2))
    s3 = session_obj(DILNOZA, start=T0 + timedelta(hours=3), end=T0 + timedelta(hours=4))
    svc = service(
        payments=[
            (DILNOZA, "ONLINE", 100, T0 + timedelta(minutes=30)),  # s1
            (DILNOZA, "ONLINE", 50, T0 + timedelta(hours=3, minutes=30)),  # s3
            # Sessiyalar orasidagi bo'shliqda to'langan — hech kimga tegishli emas
            (DILNOZA, "ONLINE", 70, T0 + timedelta(hours=2, minutes=30)),
            (AZIZ, "CARD", 200, T0 + timedelta(hours=1)),  # s2
        ],
        shop=[(DILNOZA, "CASH", 30, T0 + timedelta(hours=1))],  # s1
    )
    out = asyncio.run(svc.methods_for_sessions([s1, s2, s3]))

    assert set(out) == {s1.id, s2.id, s3.id}
    m1 = {r["method"]: r for r in out[s1.id]}
    assert m1["ONLINE"]["payments"] == 100.0 and m1["CASH"]["shop"] == 30.0
    m3 = {r["method"]: r for r in out[s3.id]}
    assert m3["ONLINE"]["payments"] == 50.0
    m2 = {r["method"]: r for r in out[s2.id]}
    assert m2["CARD"]["payments"] == 200.0

    # Yozuvsiz sessiya natijaga umuman kirmaydi
    empty = session_obj(AZIZ, start=T0 + timedelta(hours=5), end=T0 + timedelta(hours=6))
    assert asyncio.run(svc.methods_for_sessions([empty])) == {}
    # Bo'sh ro'yxat so'rov ham yubormaydi
    assert asyncio.run(svc.methods_for_sessions([])) == {}


def test_history_exposes_methods_only_for_closed_sessions():
    """Ochiq sessiyada "ko'r sanash" qoidasi: kassa raqamlari kabi usullar
    tarkibi ham yopilgunga qadar yashiriladi."""
    closed = session_obj(DILNOZA, start=T0, end=T0 + timedelta(hours=4), opening=100000)
    closed.expected_cash = Decimal("100000")
    closed.counted_cash = Decimal("100000")
    closed.cash_diff = Decimal("0")
    open_s = session_obj(AZIZ, start=T0 + timedelta(hours=5), end=None)

    svc = ShiftService.__new__(ShiftService)
    svc.session = Db(
        join_rows=[
            (closed, SimpleNamespace(first_name="Dilnoza", last_name="R")),
            (open_s, SimpleNamespace(first_name="Aziz", last_name="K")),
        ],
        users=[
            SimpleNamespace(id=DILNOZA, first_name="Dilnoza", last_name="R"),
            SimpleNamespace(id=AZIZ, first_name="Aziz", last_name="K"),
        ],
        payments=[(DILNOZA, "ONLINE", 90000, T0 + timedelta(hours=1))],
    )
    out = asyncio.run(svc.get_history(HOTEL, ADMIN))
    by_id = {i["id"]: i for i in out}
    assert by_id[str(closed.id)]["methods"][0]["method"] == "ONLINE"
    assert by_id[str(closed.id)]["methods"][0]["income"] == 90000.0
    assert "methods" not in by_id[str(open_s.id)]


def test_expected_cash_endpoint_keeps_methods_as_list():
    """`methods` — ro'yxat: float() konversiyasi unga tegmasligi kerak
    (ilgari barcha maydonlar float() qilinardi — ro'yxat yiqilardi)."""
    s = session_obj(start=T0)
    svc = ShiftService.__new__(ShiftService)
    svc.session = Db(sessions=[s], payments=[("ONLINE", 45000)])
    out = asyncio.run(svc.my_expected_cash(HOTEL, {"id": str(DILNOZA)}))
    assert isinstance(out["methods"], list)
    assert out["methods"][0]["income"] == 45000.0
    assert out["non_cash_income"] == 45000.0
    assert out["expected_cash"] == 0.0
