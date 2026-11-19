"""Kassani faqat YOPILGAN KUNLAR bo'yicha topshirish (sozlama).

`handover_closed_days` yoqilganda:
  * "Kassani topshirish" — tanlov yo'q: kunlik kesim yopgan kunlarning
    hammasi birga topshiriladi (oxirgi kesimgacha). Bugungi (kesimdan
    keyingi) pulni ham, eski kunlarning bir qisminigina ham topshirib
    bo'lmaydi; yopilgan kun bo'lmasa — umuman topshirib bo'lmaydi.
  * "Smenani tugallash" — sessiya kunlik kesimdan oshib o'tmaydi: yopilgan
    kunlar kesim momentida alohida yopiladi, kesimdan keyingi qism esa
    kassa bilan birga keyingi xodimga o'tadi.

O'chiq bo'lsa (standart) — hammasi avvalgidek (`test_shift_day_cut.py`).
"""
import asyncio
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.services import shift_service as ss
from app.application.services.shift_service import (
    ShiftService,
    kept_in_drawer,
    next_cut_boundary,
    resolve_shift_settings,
)
from app.core.exceptions import ConflictException, ValidationException
from tests.test_shift_day_cut import (
    ADMIN,
    ADMIN_ID,
    CUT_16,
    CUT_17,
    CUT_18,
    DILNOZA,
    GAP,
    HOTEL,
    ME,
    MOVES,
    NOW,
    FakeDatetime,
    local,
    make_service as make_free_service,
    make_session,
    pay,
    spend,
)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(ss, "datetime", FakeDatetime)
    monkeypatch.setattr(ss.app_settings, "APP_TZ_OFFSET_MINUTES", 300)


def make_service(monkeypatch, s, moves=(), *, approval=False, day_close="00:05", closed_days=True):
    svc, approvals = make_free_service(
        monkeypatch, s, moves, approval=approval, day_close=day_close
    )

    async def get_settings(hotel_id):
        return {
            "mode": "cash",
            "day_close": day_close,
            "day_close_required": True,
            "handover_approval": approval,
            "handover_closed_days": closed_days,
        }

    svc.get_settings = get_settings
    return svc, approvals


# ------------------------------------------------------------------ sozlama

def test_setting_is_off_by_default():
    """Mavjud mehmonxonalarda hech narsa o'zgarmaydi."""
    assert resolve_shift_settings(None)["handover_closed_days"] is False
    assert resolve_shift_settings({})["handover_closed_days"] is False
    assert resolve_shift_settings({"shift": {"mode": "cash"}})["handover_closed_days"] is False


def test_setting_is_read_only_as_boolean():
    on = {"shift": {"handover_closed_days": True}}
    assert resolve_shift_settings(on)["handover_closed_days"] is True
    for junk in ("yes", 1, None, "true"):
        broken = {"shift": {"handover_closed_days": junk}}
        assert resolve_shift_settings(broken)["handover_closed_days"] is False


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


def test_save_settings_stores_the_flag():
    svc, hotel = _settings_service({"other": 1})
    out = asyncio.run(
        svc.save_settings(HOTEL, "cash", "00:05", True, False, handover_closed_days=True)
    )
    assert out["handover_closed_days"] is True
    assert hotel.settings["shift"]["handover_closed_days"] is True
    # Boshqa sozlamalarga tegilmagan
    assert hotel.settings["other"] == 1
    assert out["handover_approval"] is False


def test_old_client_does_not_reset_the_flag():
    """Eski klient maydonni yubormaydi — saqlangan qiymat o'zgarmaydi."""
    svc, hotel = _settings_service(
        {"shift": {"mode": "cash", "day_close": "00:05", "handover_closed_days": True}}
    )
    out = asyncio.run(svc.save_settings(HOTEL, "cash", "06:00", True, True))
    assert out["handover_closed_days"] is True
    assert out["day_close"] == "06:00" and out["handover_approval"] is True
    # Ochiq o'chirish ishlaydi
    out = asyncio.run(
        svc.save_settings(HOTEL, "cash", "06:00", True, None, handover_closed_days=False)
    )
    assert out["handover_closed_days"] is False
    assert out["handover_approval"] is True


def test_settings_request_field_is_optional():
    from app.presentation.api.v1.shifts import ShiftSettingsRequest

    assert ShiftSettingsRequest(mode="cash", day_close="00:05").handover_closed_days is None
    assert (
        ShiftSettingsRequest(
            mode="cash", day_close="00:05", handover_closed_days=True
        ).handover_closed_days
        is True
    )


# ------------------------------------------------------------- keyingi kesim

def test_next_cut_is_today_when_not_reached_yet():
    assert next_cut_boundary(local(18, 8, 40), "23:00") == local(18, 23)


def test_next_cut_is_tomorrow_when_already_passed():
    assert next_cut_boundary(local(18, 8, 40), "00:05") == local(19, 0, 5)
    # Aynan kesim momentida — navbatdagisi ertaga
    assert next_cut_boundary(local(18, 0, 5), "00:05") == local(19, 0, 5)
    assert next_cut_boundary(local(18, 0, 4, 59), "00:05") == local(18, 0, 5)


def test_next_cut_follows_the_hotel_timezone(monkeypatch):
    monkeypatch.setattr(ss.app_settings, "APP_TZ_OFFSET_MINUTES", 0)
    now = local(18, 8, 40)  # UTC 03:40
    assert next_cut_boundary(now, "03:00") == now.replace(hour=3, minute=0) + timedelta(days=1)
    assert next_cut_boundary(now, "04:00") == now.replace(hour=4, minute=0)


# -------------------------------------------------------- dialog uchun hisob

def test_expected_cash_tells_the_mode_and_next_cut(monkeypatch):
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    out = asyncio.run(svc.my_expected_cash(HOTEL, ME))
    assert out["closed_days_only"] is True
    assert out["next_cut_at"] == local(19, 0, 5).isoformat()
    # Variantlar o'zgarmagan — topshiriladigani oxirgisi
    assert [c["due_cash"] for c in out["cuts"]] == [1_300_000.0, 2_150_000.0, 2_550_000.0]
    assert out["expected_cash"] == 2_800_000.0


def test_expected_cash_in_free_mode_is_marked_free(monkeypatch):
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES, closed_days=False)
    out = asyncio.run(svc.my_expected_cash(HOTEL, ME))
    assert out["closed_days_only"] is False
    assert out["next_cut_at"] == local(19, 0, 5).isoformat()

    # Sozlamada maydon umuman bo'lmasa ham (eski yozuv) — erkin rejim
    svc, _ = make_free_service(monkeypatch, make_session(opening=100_000), MOVES)
    assert asyncio.run(svc.my_expected_cash(HOTEL, ME))["closed_days_only"] is False


# ----------------------------------------------------------- kassani topshirish

def test_all_closed_days_are_handed_over_together(monkeypatch):
    """Uch yopilgan kun birga topshiriladi, bugungi 250 000 kassada qoladi."""
    s = make_session(opening=100_000, continue_after_end=True)
    svc, _ = make_service(monkeypatch, s, MOVES)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 2_550_000.0, cut_at=CUT_18))

    assert s.status == "CLOSED"
    assert s.ended_at == CUT_18 and s.handed_at == NOW
    assert s.expected_cash == 2_550_000 and s.cash_diff == 0
    (new_s,) = svc.session.added
    assert new_s.status == "ACTIVE"
    assert new_s.started_at == CUT_18 + GAP
    assert new_s.opening_cash == 0
    assert asyncio.run(svc.cash_breakdown(new_s))["expected_cash"] == 250_000
    assert report["new_session"]["started_at"] == (CUT_18 + GAP).isoformat()


def test_todays_cash_cannot_be_handed_over(monkeypatch):
    """Kesimsiz so'rov — "hamma pul": bugungi tushum ham ketardi. Rad etiladi."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)

    with pytest.raises(ValidationException) as error:
        asyncio.run(svc.close_cash(HOTEL, ME, 2_800_000.0))

    assert error.value.error_code == "CLOSED_DAYS_ONLY"
    # Sessiyaga tegilmagan
    assert s.status == "ACTIVE" and s.ended_at is None and s.counted_cash is None
    assert svc.session.added == []


@pytest.mark.parametrize("older", [CUT_16, CUT_17])
def test_closed_days_cannot_be_split(monkeypatch, older):
    """Eski kunlarning bir qismi emas — yopilgan kunlarning hammasi birga."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)

    with pytest.raises(ValidationException) as error:
        asyncio.run(svc.close_cash(HOTEL, ME, 1_300_000.0, cut_at=older))

    assert error.value.error_code == "CLOSED_DAYS_ONLY"
    assert s.status == "ACTIVE" and s.ended_at is None


def test_nothing_to_hand_over_before_the_first_cut(monkeypatch):
    """Kassa oxirgi kesimdan keyin ochilgan — faqat bugungi pul bor."""
    s = make_session(started=local(18, 1))
    svc, _ = make_service(monkeypatch, s, [pay(250_000, local(18, 7))])

    with pytest.raises(ValidationException) as error:
        asyncio.run(svc.close_cash(HOTEL, ME, 250_000.0))

    assert error.value.error_code == "NO_CLOSED_DAY"
    assert "00:05" in error.value.detail
    assert s.status == "ACTIVE" and s.ended_at is None

    # Kesim yuborilsa ham — bunday kesim yo'q
    with pytest.raises(ValidationException) as error:
        asyncio.run(svc.close_cash(HOTEL, ME, 250_000.0, cut_at=CUT_18))
    assert error.value.error_code == "NO_CLOSED_DAY"


def test_difference_is_recorded_on_the_employee(monkeypatch):
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    asyncio.run(svc.close_cash(HOTEL, ME, 2_500_000.0, cut_at=CUT_18))
    assert s.expected_cash == 2_550_000
    assert s.cash_diff == -50_000


def test_cut_without_timezone_is_accepted(monkeypatch):
    """Klient vaqtni zonasiz yoki soniyagacha yaxlitlab yuborishi mumkin."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    asyncio.run(svc.close_cash(HOTEL, ME, 2_550_000.0, cut_at=CUT_18.replace(tzinfo=None)))
    assert s.ended_at == CUT_18


def test_closed_days_handover_waits_for_approval(monkeypatch):
    s = make_session(started=local(17, 9), continue_after_end=True)
    moves = [pay(1_000_000, local(17, 15)), spend(50_000, local(18, 7))]
    svc, approvals = make_service(monkeypatch, s, moves, approval=True)

    report = asyncio.run(svc.close_cash(HOTEL, ME, 950_000.0, cut_at=CUT_18))

    assert s.status == "PENDING_HANDOVER" and s.pending_approval == "close_cash"
    assert s.ended_at == CUT_18
    assert svc.session.added == []
    assert "new_session" not in report
    assert len(approvals) == 1

    asyncio.run(svc.approve_handover(HOTEL, ADMIN, s.id))
    assert s.status == "CLOSED" and s.accepted_by == ADMIN_ID
    (new_s,) = svc.session.added
    assert new_s.started_at == CUT_18 + GAP
    assert new_s.opening_cash == 50_000


def test_free_mode_still_allows_everything(monkeypatch):
    """Sozlama o'chiq — hamma pul ham, eski kun ham avvalgidek topshiriladi."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES, closed_days=False)
    asyncio.run(svc.close_cash(HOTEL, ME, 2_800_000.0))
    assert s.status == "CLOSED" and s.ended_at == NOW and s.expected_cash == 2_800_000

    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES, closed_days=False)
    asyncio.run(svc.close_cash(HOTEL, ME, 1_300_000.0, cut_at=CUT_16))
    assert s.status == "CLOSED" and s.ended_at == CUT_16 and s.expected_cash == 1_300_000


# ------------------------------------------------------------ smenani tugallash

def test_end_shift_closes_the_days_at_the_cut(monkeypatch):
    """Smena kunlik kesimda yopiladi: yopilgan kunlar alohida sessiya,
    kesimdan keyingi qism kassa bilan birga keyingi xodimga o'tadi."""
    s = make_session(opening=100_000, continue_after_end=True)
    svc, approvals = make_service(monkeypatch, s, MOVES)

    report = asyncio.run(svc.end_shift(HOTEL, ME, 2_800_000.0, "Azizga topshirdim"))

    # Yopilgan kunlar: kesim momentida yopildi; pul sanalmagan — kassada qoldi
    assert s.status == "CLOSED"
    assert s.ended_at == CUT_18 and s.handed_at == NOW
    assert s.expected_cash is None and s.counted_cash is None and s.cash_diff is None
    assert s.carried_cash == 2_550_000
    assert kept_in_drawer(s) is True
    assert s.closed_by == DILNOZA
    assert s.accepted_by is None and s.pending_approval is None
    assert "kunlik kesim bo'yicha yopildi" in s.notes

    # Kesimdan keyingi qism: kassadagi HAMMA pul bilan qabul kutadi
    (rest,) = svc.session.added
    assert rest.user_id == DILNOZA
    assert rest.started_at == CUT_18 + GAP
    assert rest.opening_cash == 2_550_000
    assert rest.status == "PENDING_HANDOVER"
    assert rest.ended_at == NOW and rest.handed_at is None
    assert rest.expected_cash == 2_800_000
    assert rest.counted_cash == 2_800_000 and rest.cash_diff == 0
    assert rest.notes == "Azizga topshirdim"
    assert rest.pending_approval is None
    assert rest.continue_after_end is True

    # Javob — qabul kutadigan sessiya; yopilgan kunlar alohida ko'rsatiladi
    assert report["status"] == "PENDING_HANDOVER"
    assert report["started_at"] == (CUT_18 + GAP).isoformat()
    assert report["expected_cash"] == 2_800_000.0
    assert report["closed_days"]["id"] == str(s.id)
    assert report["closed_days"]["ended_at"] == CUT_18.isoformat()
    assert report["closed_days"]["carried_cash"] == 2_550_000.0
    assert report["closed_days"]["kept_in_drawer"] is True
    assert report["closed_days"]["counted_cash"] is None
    assert report["kept_in_drawer"] is False
    # Smena tugallash tasdiq so'ramaydi
    assert approvals == []


def test_end_shift_parts_add_up_to_the_whole_drawer(monkeypatch):
    """Hech bir harakat yo'qolmaydi va ikki marta sanalmaydi."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    whole = asyncio.run(svc.cash_breakdown(s))

    asyncio.run(svc.end_shift(HOTEL, ME, 2_800_000.0))

    (rest,) = svc.session.added
    days = asyncio.run(svc.cash_breakdown(s))
    today = asyncio.run(svc.cash_breakdown(rest))
    assert days["expected_cash"] == 2_550_000
    assert today["expected_cash"] == whole["expected_cash"] == 2_800_000
    for field in ("payments_cash", "shop_cash", "expenses_cash"):
        assert days[field] + today[field] == whole[field]
    assert days["non_cash_income"] + today["non_cash_income"] == whole["non_cash_income"]


def test_end_shift_difference_lands_on_the_counted_session(monkeypatch):
    """Pul bir marta — smena tugallanganda sanaladi; farq o'sha sessiyada."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)

    report = asyncio.run(svc.end_shift(HOTEL, ME, 2_750_000.0))

    assert s.cash_diff is None
    (rest,) = svc.session.added
    assert rest.cash_diff == -50_000
    assert report["cash_diff"] == -50_000.0


def test_end_shift_keeps_money_spent_after_the_cut_consistent(monkeypatch):
    """Kesimdan keyin naqd xarajat: kesimdagi kassa 1 000 000, bugun −50 000."""
    s = make_session(started=local(17, 9))
    moves = [pay(1_000_000, local(17, 15)), spend(50_000, local(18, 7))]
    svc, _ = make_service(monkeypatch, s, moves)

    asyncio.run(svc.end_shift(HOTEL, ME, 950_000.0))

    assert s.carried_cash == 1_000_000
    (rest,) = svc.session.added
    assert rest.opening_cash == 1_000_000
    assert rest.expected_cash == 950_000 and rest.cash_diff == 0


def test_end_shift_before_the_first_cut_is_plain(monkeypatch):
    """Sessiyaga kesim tushmagan — bo'ladigan narsa yo'q, avvalgidek yopiladi."""
    s = make_session(started=local(18, 1))
    svc, _ = make_service(monkeypatch, s, [pay(250_000, local(18, 7))])

    report = asyncio.run(svc.end_shift(HOTEL, ME, 250_000.0))

    assert s.status == "PENDING_HANDOVER"
    assert s.ended_at == NOW and s.handed_at is None and s.carried_cash is None
    assert s.expected_cash == 250_000
    assert svc.session.added == []
    assert "closed_days" not in report


def test_end_shift_after_handing_over_closed_days(monkeypatch):
    """Odatiy tartib: avval yopilgan kunlar topshiriladi (pul kassadan
    chiqadi), keyin smena tugallanadi — bugungi tushum keyingi xodimga o'tadi."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    asyncio.run(svc.close_cash(HOTEL, ME, 2_550_000.0, cut_at=CUT_18))
    (today,) = svc.session.added
    svc.session.sessions = [today]
    svc.session.added.clear()

    report = asyncio.run(svc.end_shift(HOTEL, ME, 250_000.0))

    assert today.status == "PENDING_HANDOVER"
    assert today.started_at == CUT_18 + GAP and today.ended_at == NOW
    assert today.expected_cash == 250_000 and today.cash_diff == 0
    assert svc.session.added == []
    assert "closed_days" not in report


def test_end_shift_in_free_mode_is_unchanged(monkeypatch):
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES, closed_days=False)

    report = asyncio.run(svc.end_shift(HOTEL, ME, 2_800_000.0))

    assert s.status == "PENDING_HANDOVER"
    assert s.ended_at == NOW and s.handed_at is None
    assert s.expected_cash == Decimal("2800000")
    assert svc.session.added == []
    assert "closed_days" not in report


# ------------------------------------------------- kassada qolgan qism belgisi

def test_cut_handover_is_not_marked_as_kept_in_drawer(monkeypatch):
    """Kesim bo'yicha kassa TOPSHIRILGANDA pul sanaladi — bu boshqa holat,
    hatto 0 so'm topshirilgan bo'lsa ham."""
    s = make_session(started=local(17, 9))
    moves = [pay(100_000, local(17, 15)), spend(100_000, local(18, 7))]
    svc, _ = make_service(monkeypatch, s, moves)
    asyncio.run(svc.close_cash(HOTEL, ME, 0.0, cut_at=CUT_18))
    assert s.status == "CLOSED" and s.counted_cash == 0 and s.carried_cash == 100_000
    assert kept_in_drawer(s) is False


def test_uncounted_part_cannot_be_corrected(monkeypatch):
    """Kesimda yopilgan qismda sanalgan summa yo'q — tuzatiladigan narsa ham."""
    s = make_session(opening=100_000)
    svc, _ = make_service(monkeypatch, s, MOVES)
    asyncio.run(svc.end_shift(HOTEL, ME, 2_800_000.0))

    with pytest.raises(ConflictException) as error:
        asyncio.run(svc.correct_session(HOTEL, ADMIN, s.id, 5.0, "sinov"))

    assert error.value.error_code == "SHIFT_NOT_COUNTED"
    assert s.counted_cash is None and s.corrections is None
