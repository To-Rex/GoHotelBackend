"""Kassani faqat OLDINGI SMENALAR bo'yicha topshirish (sozlama).

`handover_scope = "prev_shifts"` bo'lganda xodim faqat oldingi smenalardan
qolgan pulni — sessiyasining boshlang'ich kassasini — topshiradi. O'z
smenasidagi tushum kassada qoladi: smena tugagach keyingi xodimga o'tadi va
uni o'sha topshiradi.

Hisob kunlik kesimdagi bilan bir xil, faqat "kesim" — sessiya boshlanishi:
topshirilgan sessiya o'zi boshlangan momentda yopiladi, xodim esa o'sha
momentdan boshlanadigan yangi sessiyada davom etadi.

Topshirish tartibi uch holatli (`handover_scope`): erkin / yopilgan kunlar /
oldingi smenalar; eski ikki holatli `handover_closed_days` bayrog'i bilan
mos ishlaydi.
"""
import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.application.services import shift_service as ss
from app.application.services.shift_service import (
    ShiftService,
    handover_scope_of,
    kept_in_drawer,
    prev_shift_cash,
    resolve_shift_settings,
)
from app.core.exceptions import ValidationException
from tests.test_shift_day_cut import (
    ADMIN,
    ADMIN_ID,
    AZIZ,
    CUT_18,
    DILNOZA,
    GAP,
    HOTEL,
    ME,
    NOW,
    Db,
    FakeDatetime,
    Rows,
    local,
    make_service as make_free_service,
    make_session,
    pay,
    spend,
)

# Dilnoza smenani 17-noyabr 09:00 da Azizdan 1 000 000 so'm bilan qabul
# qilgan; hozir 18-noyabr 08:40 (kunlik kesim 00:05 sessiya ichida).
START = local(17, 9)
OPENING = 1_000_000
# O'z smenasida: 400 000 + 250 000 naqd, 120 000 online
OWN = [
    pay(400_000, local(17, 15)),
    pay(250_000, local(18, 7)),
    pay(120_000, local(17, 16), method="ONLINE"),
]
RECEIVED = (
    SimpleNamespace(accepted_at=START, counted_cash=OPENING),
    SimpleNamespace(first_name="Aziz", last_name="Karimov"),
)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(ss, "datetime", FakeDatetime)
    monkeypatch.setattr(ss.app_settings, "APP_TZ_OFFSET_MINUTES", 300)


class PrevDb(Db):
    """`Db` + "kassa kimdan qabul qilingan" so'rovi."""

    received = None

    async def execute(self, stmt):
        # Ustunlar ro'yxatida ham `accepted_by` bor — faqat SHART bo'yicha ajratiladi
        if "accepted_by" in str(stmt).split("WHERE", 1)[-1]:
            return Rows([self.received] if self.received else [])
        return await super().execute(stmt)


def make_service(monkeypatch, s, moves=(), *, approval=False, scope="prev_shifts", received=RECEIVED):
    svc, approvals = make_free_service(monkeypatch, s, moves, approval=approval)
    db = PrevDb(moves=moves, sessions=[s], session_obj=s)
    db.received = received
    svc.session = db

    async def get_settings(hotel_id):
        return {
            "mode": "cash",
            "day_close": "00:05",
            "day_close_required": True,
            "handover_approval": approval,
            "handover_scope": scope,
            "handover_closed_days": scope == "closed_days",
        }

    svc.get_settings = get_settings
    return svc, approvals


def my_session(opening=OPENING, **kw):
    return make_session(started=START, opening=opening, **kw)


# ------------------------------------------------------------------ sozlama

def test_scope_is_free_by_default():
    for stored in (None, {}, {"shift": {"mode": "cash"}}):
        out = resolve_shift_settings(stored)
        assert out["handover_scope"] == "free"
        assert out["handover_closed_days"] is False


def test_scope_is_read_from_settings():
    out = resolve_shift_settings({"shift": {"handover_scope": "prev_shifts"}})
    assert out["handover_scope"] == "prev_shifts"
    # Eski bayroq faqat "yopilgan kunlar" rejimini bildiradi
    assert out["handover_closed_days"] is False
    out = resolve_shift_settings({"shift": {"handover_scope": "closed_days"}})
    assert out["handover_scope"] == "closed_days" and out["handover_closed_days"] is True


def test_legacy_flag_means_closed_days():
    """Sozlama eski ko'rinishda saqlangan (faqat bayroq) — rejim o'zgarmaydi."""
    out = resolve_shift_settings({"shift": {"handover_closed_days": True}})
    assert out["handover_scope"] == "closed_days"
    assert handover_scope_of({"handover_closed_days": True}) == "closed_days"
    assert handover_scope_of({"handover_closed_days": "yes"}) == "free"


def test_unknown_scope_falls_back():
    assert resolve_shift_settings({"shift": {"handover_scope": "weekly"}})["handover_scope"] == "free"
    both = {"shift": {"handover_scope": 5, "handover_closed_days": True}}
    assert resolve_shift_settings(both)["handover_scope"] == "closed_days"
    # Yangi maydon ustun
    mixed = {"shift": {"handover_scope": "prev_shifts", "handover_closed_days": True}}
    assert resolve_shift_settings(mixed)["handover_scope"] == "prev_shifts"


def _settings_service(stored: dict):
    hotel = SimpleNamespace(settings=stored)
    svc = ShiftService.__new__(ShiftService)

    async def flush():
        return None

    svc.session = SimpleNamespace(flush=flush)

    async def get_hotel(hotel_id):
        return hotel

    svc._get_hotel = get_hotel
    return svc, hotel


def test_save_settings_stores_the_scope():
    svc, hotel = _settings_service({"other": 1})
    out = asyncio.run(
        svc.save_settings(HOTEL, "cash", "00:05", True, False, handover_scope="prev_shifts")
    )
    assert out["handover_scope"] == "prev_shifts" and out["handover_closed_days"] is False
    assert hotel.settings["shift"]["handover_scope"] == "prev_shifts"
    assert hotel.settings["other"] == 1

    out = asyncio.run(
        svc.save_settings(HOTEL, "cash", "00:05", True, False, handover_scope="closed_days")
    )
    assert out["handover_scope"] == "closed_days" and out["handover_closed_days"] is True
    out = asyncio.run(svc.save_settings(HOTEL, "cash", "00:05", True, False, handover_scope="free"))
    assert out["handover_scope"] == "free" and out["handover_closed_days"] is False


def test_scope_wins_over_the_legacy_flag():
    svc, _ = _settings_service({})
    out = asyncio.run(
        svc.save_settings(
            HOTEL, "cash", "00:05", True, False,
            handover_closed_days=True, handover_scope="prev_shifts",
        )
    )
    assert out["handover_scope"] == "prev_shifts"


def test_old_client_keeps_the_scope_it_does_not_know():
    """Eski klient faqat bayroqni biladi. U boshqa sozlamani saqlaganda
    (bayroq — o'chiq) "oldingi smenalar" rejimi yo'qolmasligi kerak."""
    svc, _ = _settings_service({"shift": {"mode": "cash", "handover_scope": "prev_shifts"}})
    out = asyncio.run(svc.save_settings(HOTEL, "cash", "06:00", True, True, handover_closed_days=False))
    assert out["handover_scope"] == "prev_shifts" and out["day_close"] == "06:00"
    # Hech narsa yubormasa ham
    out = asyncio.run(svc.save_settings(HOTEL, "cash", "06:00", True, None))
    assert out["handover_scope"] == "prev_shifts"
    # Eski klient bayroqni yoqsa — u bilgan rejim
    out = asyncio.run(svc.save_settings(HOTEL, "cash", "06:00", True, None, handover_closed_days=True))
    assert out["handover_scope"] == "closed_days"
    # ...va o'chirsa — erkin
    out = asyncio.run(svc.save_settings(HOTEL, "cash", "06:00", True, None, handover_closed_days=False))
    assert out["handover_scope"] == "free"


def test_settings_request_validates_scope():
    from pydantic import ValidationError

    from app.presentation.api.v1.shifts import ShiftSettingsRequest

    assert ShiftSettingsRequest(mode="cash", day_close="00:05").handover_scope is None
    for scope in ("free", "closed_days", "prev_shifts"):
        assert ShiftSettingsRequest(mode="cash", day_close="00:05", handover_scope=scope).handover_scope == scope
    with pytest.raises(ValidationError):
        ShiftSettingsRequest(mode="cash", day_close="00:05", handover_scope="weekly")


# -------------------------------------------------------- dialog uchun hisob

def test_expected_cash_offers_previous_shifts_money(monkeypatch):
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN)

    out = asyncio.run(svc.my_expected_cash(HOTEL, ME))

    assert out["handover_scope"] == "prev_shifts"
    assert out["closed_days_only"] is False
    assert out["expected_cash"] == 1_650_000.0
    plan = out["prev_shifts"]
    assert plan["cut_at"] == START.isoformat()
    assert plan["opening_cash"] == 1_000_000.0
    assert plan["expected_cash"] == 1_000_000.0
    assert plan["after_cash"] == 650_000.0
    assert plan["carried_cash"] == 0.0
    assert plan["due_cash"] == 1_000_000.0
    # Oldingi smenalar puli + o'z smenasi = kassadagi jami pul
    assert plan["expected_cash"] + plan["after_cash"] == out["expected_cash"]
    # Qabul qilingan pulning to'lov usullari tarkibi yo'q — u naqd
    assert plan["non_cash_income"] == 0.0
    assert plan["received_from"] == {
        "user_name": "Aziz Karimov",
        "accepted_at": START.isoformat(),
        "counted_cash": 1_000_000.0,
    }


def test_expected_cash_without_an_accepted_shift(monkeypatch):
    """Smena 0 dan ochilgan — topshiriladigan pul ham, manba ham yo'q."""
    s = my_session(opening=0)
    svc, _ = make_service(monkeypatch, s, OWN, received=None)
    out = asyncio.run(svc.my_expected_cash(HOTEL, ME))
    assert out["prev_shifts"]["due_cash"] == 0.0
    assert out["prev_shifts"]["received_from"] is None
    assert out["expected_cash"] == 650_000.0


def test_other_modes_do_not_send_the_plan(monkeypatch):
    for scope in ("free", "closed_days"):
        svc, _ = make_service(monkeypatch, my_session(), OWN, scope=scope)
        out = asyncio.run(svc.my_expected_cash(HOTEL, ME))
        assert out["handover_scope"] == scope
        assert "prev_shifts" not in out
        assert out["closed_days_only"] is (scope == "closed_days")


# ----------------------------------------------------------- kassani topshirish

def test_previous_shifts_money_is_handed_over(monkeypatch):
    s = my_session(continue_after_end=True)
    svc, _ = make_service(monkeypatch, s, OWN)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 1_000_000.0, cut_at=START))

    # Sessiya o'zi boshlangan momentda yopiladi: unda faqat qabul qilingan pul
    assert s.status == "CLOSED"
    assert s.ended_at == START and s.handed_at == NOW
    assert s.expected_cash == 1_000_000 and s.counted_cash == 1_000_000
    assert s.cash_diff == 0 and s.carried_cash == 0
    assert prev_shift_cash(s) is True and kept_in_drawer(s) is False
    assert report["prev_shift_cash"] is True
    assert report["expected_cash"] == 1_000_000.0

    # Xodim o'sha momentdan davom etadi — o'z tushumi shu sessiyada
    (rest,) = svc.session.added
    assert rest.status == "ACTIVE" and rest.user_id == DILNOZA
    assert rest.started_at == START + GAP
    assert rest.opening_cash == 0
    assert rest.continue_after_end is True
    own = asyncio.run(svc.cash_breakdown(rest))
    assert own["expected_cash"] == 650_000
    assert own["non_cash_income"] == 120_000.0
    assert report["new_session"]["started_at"] == (START + GAP).isoformat()
    # Yopilgan qismda xodimning harakatlari yo'q
    closed = asyncio.run(svc.cash_breakdown(s))
    assert closed["expected_cash"] == 1_000_000
    assert closed["payments_cash"] == 0 and closed["non_cash_income"] == 0


def test_difference_is_recorded_on_the_employee(monkeypatch):
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN)
    asyncio.run(svc.close_cash(HOTEL, ME, 950_000.0, cut_at=START))
    assert s.expected_cash == 1_000_000 and s.cash_diff == -50_000


def test_own_takings_cannot_be_handed_over(monkeypatch):
    """Kesimsiz so'rov — "hamma pul": o'z smenasidagi tushum ham ketardi."""
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN)

    with pytest.raises(ValidationException) as error:
        asyncio.run(svc.close_cash(HOTEL, ME, 1_650_000.0))

    assert error.value.error_code == "PREV_SHIFTS_ONLY"
    assert s.status == "ACTIVE" and s.ended_at is None and s.counted_cash is None
    assert svc.session.added == []


def test_daily_cut_is_not_accepted_in_this_mode(monkeypatch):
    """Sessiyaga kunlik kesim tushgan bo'lsa ham — bu rejimda u ishlatilmaydi."""
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN)
    assert [c["cut_at"] for c in asyncio.run(svc.my_expected_cash(HOTEL, ME))["cuts"]] == [
        CUT_18.isoformat()
    ]

    with pytest.raises(ValidationException) as error:
        asyncio.run(svc.close_cash(HOTEL, ME, 1_400_000.0, cut_at=CUT_18))

    assert error.value.error_code == "PREV_SHIFTS_ONLY"
    assert s.status == "ACTIVE" and s.ended_at is None


def test_nothing_to_hand_over_without_previous_money(monkeypatch):
    s = my_session(opening=0)
    svc, _ = make_service(monkeypatch, s, OWN, received=None)

    for cut_at in (None, START):
        with pytest.raises(ValidationException) as error:
            asyncio.run(svc.close_cash(HOTEL, ME, 650_000.0, cut_at=cut_at))
        assert error.value.error_code == "NO_PREV_SHIFT_CASH"
    assert s.status == "ACTIVE" and s.ended_at is None


def test_money_spent_in_own_shift_is_not_handed_over(monkeypatch):
    """O'z smenasida naqd xarajat tushumdan ko'p: shu qism oldingi smenalar
    pulidan to'langan — kassada yo'q, topshirilmaydi, kamomad ham emas."""
    s = my_session()
    moves = [spend(300_000, local(17, 12))]
    svc, _ = make_service(monkeypatch, s, moves)

    plan = asyncio.run(svc.my_expected_cash(HOTEL, ME))["prev_shifts"]
    assert (plan["due_cash"], plan["carried_cash"], plan["after_cash"]) == (700_000.0, 300_000.0, -300_000.0)

    asyncio.run(svc.close_cash(HOTEL, ME, 700_000.0, cut_at=START))
    assert s.expected_cash == 700_000 and s.cash_diff == 0 and s.carried_cash == 300_000
    (rest,) = svc.session.added
    assert rest.opening_cash == 300_000
    # Kassada hech narsa qolmadi: 300 000 allaqachon sarflangan
    assert asyncio.run(svc.cash_breakdown(rest))["expected_cash"] == 0

    # Keyin tushum keldi — sarflangan qism qoplandi va endi topshiriladi
    svc.session.sessions = [rest]
    svc.session.moves.append(pay(500_000, local(18, 6)))
    plan = asyncio.run(svc.my_expected_cash(HOTEL, ME))["prev_shifts"]
    assert (plan["due_cash"], plan["carried_cash"], plan["after_cash"]) == (300_000.0, 0.0, 200_000.0)
    svc.session.added.clear()
    asyncio.run(svc.close_cash(HOTEL, ME, 300_000.0, cut_at=rest.started_at))
    assert rest.status == "CLOSED" and rest.expected_cash == 300_000
    (last,) = svc.session.added
    # Jami topshirildi: 700 000 + 300 000 = qabul qilingan 1 000 000;
    # o'z smenasining sof tushumi (200 000) kassada
    assert s.counted_cash + rest.counted_cash == 1_000_000
    assert asyncio.run(svc.cash_breakdown(last))["expected_cash"] == 200_000


def test_fully_spent_previous_money_leaves_nothing(monkeypatch):
    s = my_session(opening=200_000)
    svc, _ = make_service(monkeypatch, s, [spend(250_000, local(17, 12))])
    plan = asyncio.run(svc.my_expected_cash(HOTEL, ME))["prev_shifts"]
    assert plan["due_cash"] == 0.0 and plan["carried_cash"] == 200_000.0
    with pytest.raises(ValidationException) as error:
        asyncio.run(svc.close_cash(HOTEL, ME, 0.0, cut_at=START))
    assert error.value.error_code == "NO_PREV_SHIFT_CASH"


def test_move_at_the_very_start_is_not_lost(monkeypatch):
    """Aynan sessiya boshlangan momentdagi harakat yopilgan qismda sanaladi,
    davomida esa yo'q — ikki marta ham, nol marta ham emas."""
    s = my_session()
    svc, _ = make_service(monkeypatch, s, [pay(70_000, START), pay(30_000, START + timedelta(seconds=1))])
    plan = asyncio.run(svc.my_expected_cash(HOTEL, ME))["prev_shifts"]
    assert plan["due_cash"] == 1_070_000.0 and plan["after_cash"] == 30_000.0
    asyncio.run(svc.close_cash(HOTEL, ME, 1_070_000.0, cut_at=START))
    (rest,) = svc.session.added
    assert asyncio.run(svc.cash_breakdown(s))["expected_cash"] == 1_070_000
    assert asyncio.run(svc.cash_breakdown(rest))["expected_cash"] == 30_000


def test_cut_without_timezone_is_accepted(monkeypatch):
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN)
    asyncio.run(svc.close_cash(HOTEL, ME, 1_000_000.0, cut_at=START.replace(tzinfo=None)))
    assert s.status == "CLOSED" and s.ended_at == START


def test_handover_waits_for_approval(monkeypatch):
    s = my_session(continue_after_end=True)
    svc, approvals = make_service(monkeypatch, s, OWN, approval=True)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 1_000_000.0, cut_at=START))

    assert s.status == "PENDING_HANDOVER" and s.pending_approval == "close_cash"
    assert s.ended_at == START and prev_shift_cash(s) is True
    assert svc.session.added == []
    assert "new_session" not in report
    assert len(approvals) == 1

    asyncio.run(svc.approve_handover(HOTEL, ADMIN, s.id))
    assert s.status == "CLOSED" and s.accepted_by == ADMIN_ID
    (rest,) = svc.session.added
    assert rest.started_at == START + GAP and rest.opening_cash == 0
    assert asyncio.run(svc.cash_breakdown(rest))["expected_cash"] == 650_000


def test_force_closing_awaited_handover_keeps_own_takings(monkeypatch):
    """Rahbar tasdiqlash o'rniga pulni olib majburiy yopsa ham xodimning
    o'z tushumi egasiz qolmaydi."""
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN, approval=True)
    asyncio.run(svc.close_cash(HOTEL, ME, 1_000_000.0, cut_at=START))

    report = asyncio.run(svc.force_close(HOTEL, ADMIN, s.id, hand_over=False))

    assert s.status == "CLOSED" and s.force_closed is True
    (rest,) = svc.session.added
    assert rest.started_at == START + GAP
    assert report["new_session"]["started_at"] == (START + GAP).isoformat()
    assert asyncio.run(svc.cash_breakdown(rest))["expected_cash"] == 650_000


# ------------------------------------------------------------ smenani tugallash

def test_end_shift_passes_the_whole_drawer(monkeypatch):
    """Smena tugallanganda kassa butunligicha keyingi xodimga o'tadi —
    sessiyaga kunlik kesim tushgan bo'lsa ham bo'linmaydi."""
    s = my_session()
    svc, approvals = make_service(monkeypatch, s, OWN)

    report = asyncio.run(svc.end_shift(HOTEL, ME, 1_650_000.0))

    assert s.status == "PENDING_HANDOVER"
    assert s.ended_at == NOW and s.handed_at is None and s.carried_cash is None
    assert s.expected_cash == 1_650_000 and s.cash_diff == 0
    assert prev_shift_cash(s) is False
    assert svc.session.added == []
    assert "closed_days" not in report
    assert approvals == []


# ------------------------------------------------------------------- belgilar

def test_other_handovers_are_not_marked(monkeypatch):
    # Kunlik kesim bo'yicha topshirish
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN, scope="closed_days")
    asyncio.run(svc.close_cash(HOTEL, ME, 1_400_000.0, cut_at=CUT_18))
    assert s.status == "CLOSED" and prev_shift_cash(s) is False
    # Oddiy topshirish
    s = my_session()
    svc, _ = make_service(monkeypatch, s, OWN, scope="free")
    asyncio.run(svc.close_cash(HOTEL, ME, 1_650_000.0))
    assert s.status == "CLOSED" and prev_shift_cash(s) is False
    # Ochiq sessiya
    assert prev_shift_cash(my_session()) is False


# ---------------------------------------------------------------------- holat

def _state(monkeypatch, scope, required=True):
    svc = ShiftService.__new__(ShiftService)

    async def get_settings(hotel_id):
        return {
            "mode": "cash",
            "day_close": "00:05",
            "day_close_required": required,
            "handover_approval": False,
            "handover_scope": scope,
            "handover_closed_days": scope == "closed_days",
        }

    async def open_sessions(hotel_id, branch_id):
        return []

    svc.get_settings = get_settings
    svc._open_sessions = open_sessions
    me = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": []}
    return asyncio.run(svc.get_state(HOTEL, me))


def test_daily_cut_is_not_mandatory_for_previous_shifts(monkeypatch):
    """Xodim o'z smenasidagi pulni topshira olmaydi — kesimda "topshirmaguncha
    ishlab bo'lmaydi" talabi bajarib bo'lmaydigan bo'lardi."""
    state = _state(monkeypatch, "prev_shifts")
    assert state["handover_scope"] == "prev_shifts"
    assert state["day_close_required"] is False


def test_daily_cut_obligation_is_kept_in_other_modes(monkeypatch):
    for scope in ("free", "closed_days"):
        assert _state(monkeypatch, scope)["day_close_required"] is True
        assert _state(monkeypatch, scope, required=False)["day_close_required"] is False
