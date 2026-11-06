"""To'lov TURINI tahrirlash — huquq, 2 kunlik oyna va audit tarixi.

Qoidalar (foydalanuvchi talabi):
  * faqat super admin, admin va menejer (`shift.force_close`);
  * menejer faqat oxirgi 2 kunlik to'lovlarni, admin/super admin cheklovsiz;
  * faqat to'lov turi o'zgaradi (summa tegilmaydi);
  * har tahrir saqlanadi: nimadan nimaga, kim, qachon, izoh.
Bazasiz: sessiya va audit o'rinbosarlar bilan.
"""
import asyncio
import uuid
from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pydantic
import pytest

from app.application.services.audit_service import AuditService
from app.application.services.finance_service import (
    PAYMENT_EDIT_WINDOW_HOURS,
    FinanceService,
)
from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)

HOTEL = uuid.uuid4()
ADMIN = {"id": str(uuid.uuid4()), "user_type": "ADMIN", "permissions": []}
SUPER = {"id": str(uuid.uuid4()), "user_type": "SUPER_ADMIN", "permissions": []}
MANAGER = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["shift.force_close"]}
CASHIER = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["finance.payment.create"]}


def make_payment(created_hours_ago: float = 1, method="CASH", hotel=HOTEL):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hotel_id=hotel,
        invoice_id=uuid.uuid4(),
        payment_number="PAY-1",
        amount=250000.0,
        payment_method=method,
        payment_date=dt_date(2026, 10, 15),
        reference=None,
        created_by=uuid.uuid4(),
        created_at=datetime.now(timezone.utc) - timedelta(hours=created_hours_ago),
        method_changes=None,
    )


def make_actor(first="Aziz", last="Karimov"):
    return SimpleNamespace(first_name=first, last_name=last)


class FakeSession:
    def __init__(self, payment=None, actor=None):
        self.payment = payment
        self.actor = actor
        self.flushes = 0
        self.added: list = []

    async def get(self, model, key):
        if model.__name__ == "Payment":
            return self.payment
        if model.__name__ == "User":
            return self.actor
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1


def build(monkeypatch, payment=None, actor=None):
    """Servis + audit/valyuta o'rinbosarlari."""
    svc = FinanceService.__new__(FinanceService)
    svc.session = FakeSession(payment, actor)
    audit_calls: list[dict] = []

    async def fake_log(self, **kw):
        audit_calls.append(kw)

    async def fake_hotel_code(self, hotel_id):
        return "TST"

    monkeypatch.setattr(AuditService, "log_action", fake_log)
    monkeypatch.setattr(FinanceService, "_get_hotel_code", fake_hotel_code)
    return svc, audit_calls


def edit(svc, user, payment_id, method, note=None, amount=None):
    return asyncio.run(
        svc.update_payment_method(HOTEL, payment_id, method, user, note, amount)
    )


# ----------------------------------------------------------- huquqlar --

def test_admin_edits_old_payment_and_history_is_saved(monkeypatch):
    payment = make_payment(created_hours_ago=30 * 24)  # 1 oylik
    svc, audit = build(monkeypatch, payment, make_actor())

    out = edit(svc, ADMIN, payment.id, "CARD", note="Karta orqali bo'lgan")

    assert out.payment_method == "CARD"
    assert svc.session.flushes == 1
    assert len(payment.method_changes) == 1
    entry = payment.method_changes[0]
    assert entry["old"] == "CASH" and entry["new"] == "CARD"
    assert entry["by"] == ADMIN["id"]
    assert entry["by_name"] == "Aziz Karimov"
    assert entry["note"] == "Karta orqali bo'lgan"
    assert entry["at"]
    # Audit jurnaliga ham yozildi
    assert len(audit) == 1
    assert audit[0]["entity_type"] == "payment"
    assert audit[0]["old_values"] == {"payment_method": "CASH"}
    assert audit[0]["new_values"]["payment_method"] == "CARD"


def test_super_admin_has_no_window(monkeypatch):
    payment = make_payment(created_hours_ago=365 * 24)
    svc, _ = build(monkeypatch, payment)
    assert edit(svc, SUPER, payment.id, "ONLINE").payment_method == "ONLINE"


def test_cashier_cannot_edit(monkeypatch):
    payment = make_payment()
    svc, _ = build(monkeypatch, payment)
    with pytest.raises(ForbiddenException) as exc:
        edit(svc, CASHIER, payment.id, "CARD")
    assert exc.value.error_code == "FORBIDDEN"
    assert payment.payment_method == "CASH"


# ------------------------------------------------- menejer: 2 kunlik oyna --

def test_manager_can_edit_recent_payment(monkeypatch):
    payment = make_payment(created_hours_ago=2)
    svc, _ = build(monkeypatch, payment)
    assert edit(svc, MANAGER, payment.id, "CARD").payment_method == "CARD"


def test_manager_window_boundary(monkeypatch):
    just_inside = make_payment(created_hours_ago=PAYMENT_EDIT_WINDOW_HOURS - 1)
    svc, _ = build(monkeypatch, just_inside)
    assert edit(svc, MANAGER, just_inside.id, "CARD").payment_method == "CARD"

    too_old = make_payment(created_hours_ago=PAYMENT_EDIT_WINDOW_HOURS + 1)
    svc2, _ = build(monkeypatch, too_old)
    with pytest.raises(ForbiddenException) as exc:
        edit(svc2, MANAGER, too_old.id, "CARD")
    assert exc.value.error_code == "PAYMENT_EDIT_WINDOW"
    assert too_old.payment_method == "CASH"
    assert too_old.method_changes is None


def test_missing_created_at_skips_window_check(monkeypatch):
    payment = make_payment()
    payment.created_at = None
    svc, _ = build(monkeypatch, payment)
    # created_at bazada har doim to'ldiriladi (NOT NULL); eski/noto'liq
    # yozuvda oyna tekshirilmaydi — tahrir mumkin, tarix baribir yoziladi
    assert edit(svc, MANAGER, payment.id, "CARD").payment_method == "CARD"


# -------------------------------------------------- qiymat va xatolar --

def test_same_method_is_rejected(monkeypatch):
    payment = make_payment(method="CASH")
    svc, _ = build(monkeypatch, payment)
    with pytest.raises(ValidationException) as exc:
        edit(svc, ADMIN, payment.id, "cash")
    assert exc.value.error_code == "PAYMENT_METHOD_UNCHANGED"
    assert payment.method_changes is None


def test_legacy_code_can_move_to_canonical(monkeypatch):
    payment = make_payment(method="CREDIT_CARD")
    svc, _ = build(monkeypatch, payment)
    assert edit(svc, ADMIN, payment.id, "CARD").payment_method == "CARD"
    assert payment.method_changes[0]["old"] == "CREDIT_CARD"


def test_payment_not_found(monkeypatch):
    svc, _ = build(monkeypatch, None)
    with pytest.raises(NotFoundException) as exc:
        edit(svc, ADMIN, uuid.uuid4(), "CARD")
    assert exc.value.error_code == "PAYMENT_NOT_FOUND"


def test_other_hotel_payment_is_not_found(monkeypatch):
    payment = make_payment(hotel=uuid.uuid4())
    svc, _ = build(monkeypatch, payment)
    with pytest.raises(NotFoundException) as exc:
        edit(svc, ADMIN, payment.id, "CARD")
    assert exc.value.error_code == "PAYMENT_NOT_FOUND"


def test_history_accumulates_across_edits(monkeypatch):
    payment = make_payment()
    svc, _ = build(monkeypatch, payment, make_actor())
    edit(svc, ADMIN, payment.id, "CARD")
    edit(svc, ADMIN, payment.id, "ONLINE", note="Adashib bosilgan")
    assert [e["new"] for e in payment.method_changes] == ["CARD", "ONLINE"]
    assert payment.method_changes[0]["old"] == "CASH"
    assert payment.method_changes[1]["old"] == "CARD"


def test_request_model_only_allows_known_methods():
    from app.presentation.api.v1.finance import PaymentMethodUpdateRequest

    for method in ("CASH", "CARD", "ONLINE", "BANK_TRANSFER"):
        assert PaymentMethodUpdateRequest(payment_method=method).payment_method == method
    with pytest.raises(pydantic.ValidationError):
        PaymentMethodUpdateRequest(payment_method="CRYPTO")
    with pytest.raises(pydantic.ValidationError):
        PaymentMethodUpdateRequest(payment_method="CREDIT_CARD")
    # Qisman summa ixtiyoriy, lekin musbat bo'lishi shart
    req = PaymentMethodUpdateRequest(payment_method="CARD", amount=50000)
    assert req.amount == 50000
    assert PaymentMethodUpdateRequest(payment_method="CARD").amount is None
    with pytest.raises(pydantic.ValidationError):
        PaymentMethodUpdateRequest(payment_method="CARD", amount=0)


# ------------------------------------------------ qisman (split) o'tkazish --

def test_partial_split_creates_a_second_payment(monkeypatch):
    """100 000 naqd → 50 000 naqd + 50 000 online."""
    payment = make_payment(method="CASH")
    payment.amount = 100000.0
    svc, audit = build(monkeypatch, payment, make_actor())

    out = edit(svc, ADMIN, payment.id, "ONLINE", note="Yarmi online", amount=50000)

    # Asl to'lov: 50 000 qoldi, usuli o'zgarmadi
    assert out.amount == 50000.0
    assert out.payment_method == "CASH"

    # Yangi qator: 50 000 online, pulni qabul qilgan xodim va sana saqlanadi
    assert len(svc.session.added) == 1
    split = svc.session.added[0]
    assert float(split.amount) == 50000.0
    assert split.payment_method == "ONLINE"
    assert split.invoice_id == payment.invoice_id
    assert split.created_by == payment.created_by
    assert split.payment_date == payment.payment_date
    assert split.created_at == payment.created_at
    assert "Qisman o'tkazildi" in (split.notes or "")
    assert split.payment_number

    # Tarix: qisman yozuv + yangi to'lov raqami
    entry = payment.method_changes[0]
    assert entry["kind"] == "partial"
    assert entry["old"] == "CASH" and entry["new"] == "ONLINE"
    assert entry["amount"] == 50000.0
    assert entry["new_payment_number"] == split.payment_number
    assert entry["note"] == "Yarmi online"

    # Audit ham qisman ekanini ko'rsatadi
    assert audit[0]["new_values"]["kind"] == "partial"
    assert audit[0]["new_values"]["amount"] == 50000.0


def test_partial_amount_equal_to_total_is_plain_edit(monkeypatch):
    payment = make_payment(method="CASH")
    payment.amount = 100000.0
    svc, _ = build(monkeypatch, payment)

    out = edit(svc, ADMIN, payment.id, "CARD", amount=100000)

    assert out.payment_method == "CARD"
    assert out.amount == 100000.0
    assert svc.session.added == []
    assert payment.method_changes[0]["kind"] == "full"
    assert payment.method_changes[0]["amount"] is None


def test_partial_amount_above_total_is_rejected(monkeypatch):
    payment = make_payment(method="CASH")
    payment.amount = 100000.0
    svc, _ = build(monkeypatch, payment)

    with pytest.raises(ValidationException) as exc:
        edit(svc, ADMIN, payment.id, "CARD", amount=150000)

    assert exc.value.error_code == "PARTIAL_AMOUNT_RANGE"
    assert payment.payment_method == "CASH"
    assert payment.amount == 100000.0
    assert svc.session.added == []


def test_partial_requires_positive_payment(monkeypatch):
    """Qaytarim (manfiy to'lov) bo'linmaydi — faqat butun tahrir."""
    payment = make_payment(method="CASH")
    payment.amount = -30000.0
    svc, _ = build(monkeypatch, payment)

    with pytest.raises(ValidationException) as exc:
        edit(svc, ADMIN, payment.id, "CARD", amount=10000)

    assert exc.value.error_code == "PARTIAL_INVALID_PAYMENT"
    assert svc.session.added == []


def test_partial_respects_manager_window(monkeypatch):
    payment = make_payment(created_hours_ago=PAYMENT_EDIT_WINDOW_HOURS + 5)
    payment.amount = 100000.0
    svc, _ = build(monkeypatch, payment)

    with pytest.raises(ForbiddenException) as exc:
        edit(svc, MANAGER, payment.id, "CARD", amount=50000)

    assert exc.value.error_code == "PAYMENT_EDIT_WINDOW"
    assert svc.session.added == []


def test_partial_same_method_is_rejected(monkeypatch):
    payment = make_payment(method="CASH")
    payment.amount = 100000.0
    svc, _ = build(monkeypatch, payment)

    with pytest.raises(ValidationException) as exc:
        edit(svc, ADMIN, payment.id, "CASH", amount=50000)

    assert exc.value.error_code == "PAYMENT_METHOD_UNCHANGED"
    assert svc.session.added == []


# ------------------------------- tahrir pul olingan VAQTNI o'zgartirmaydi --

def test_partial_split_of_an_old_payment_stays_at_its_original_moment(monkeypatch):
    """Kechagi to'lov bugun qisman tahrirlandi: ajralgan qism ham KECHAGI
    daqiqada turadi — smena kassasi va shaxsiy hisobot `created_at` oynasi
    bilan ishlaydi, aks holda pul bugungi smenaga o'tib qolardi."""
    payment = make_payment(created_hours_ago=30, method="CASH")
    payment.amount = 100000.0
    paid_at = payment.created_at
    svc, _ = build(monkeypatch, payment, make_actor())

    edit(svc, ADMIN, payment.id, "ONLINE", amount=50000)

    split = svc.session.added[0]
    assert split.created_at == paid_at
    assert datetime.now(timezone.utc) - split.created_at > timedelta(hours=29)
    # Asl to'lov vaqti va sanasi ham o'zgarmadi
    assert payment.created_at == paid_at
    assert payment.payment_date == dt_date(2026, 10, 15)
    assert split.payment_date == dt_date(2026, 10, 15)
    # Tahrir PAYTI yo'qolmadi — u tarixda
    edited_at = datetime.fromisoformat(payment.method_changes[0]["at"])
    assert datetime.now(timezone.utc) - edited_at < timedelta(minutes=1)


def test_split_of_a_split_keeps_the_first_moment(monkeypatch):
    payment = make_payment(created_hours_ago=30, method="CASH")
    payment.amount = 100000.0
    paid_at = payment.created_at
    svc, _ = build(monkeypatch, payment, make_actor())
    edit(svc, ADMIN, payment.id, "ONLINE", amount=50000)
    first_split = svc.session.added[0]

    # Ajralgan qatorning o'zi yana bo'linadi
    svc2, _ = build(monkeypatch, first_split, make_actor())
    edit(svc2, ADMIN, first_split.id, "CARD", amount=20000)

    second_split = svc2.session.added[0]
    assert second_split.created_at == paid_at
    assert float(first_split.amount) == 30000.0
    # Birinchi ajralgan qator tarixi: kelib chiqishi + o'zining qisman tahriri
    assert [e["kind"] for e in first_split.method_changes] == ["split_from", "partial"]


def test_split_without_original_moment_falls_back_to_now(monkeypatch):
    """`created_at` NOT NULL; baribir bo'sh bo'lsa ajralgan qator vaqtsiz
    qolmaydi (insert yiqilmasin)."""
    payment = make_payment(method="CASH")
    payment.amount = 100000.0
    payment.created_at = None
    svc, _ = build(monkeypatch, payment)

    edit(svc, ADMIN, payment.id, "ONLINE", amount=50000)

    assert svc.session.added[0].created_at is not None


# --------------------------- tarix: necha puldan necha pulga, qayerdan --

def test_partial_history_records_amount_before_and_after(monkeypatch):
    payment = make_payment(method="CASH")
    payment.amount = 100000.0
    svc, audit = build(monkeypatch, payment, make_actor())

    edit(svc, ADMIN, payment.id, "ONLINE", note="Yarmi online", amount=30000)

    entry = payment.method_changes[0]
    assert entry["total_before"] == 100000.0
    assert entry["total_after"] == 70000.0
    assert entry["amount"] == 30000.0
    assert float(payment.amount) == 70000.0
    # Audit jurnalida ham
    assert audit[0]["new_values"]["total_before"] == 100000.0
    assert audit[0]["new_values"]["total_after"] == 70000.0
    assert audit[0]["new_values"]["new_payment_number"] == entry["new_payment_number"]


def test_split_row_history_says_where_it_came_from(monkeypatch):
    payment = make_payment(method="CASH")
    payment.amount = 100000.0
    svc, _ = build(monkeypatch, payment, make_actor())

    edit(svc, ADMIN, payment.id, "ONLINE", note="Yarmi online", amount=30000)

    split = svc.session.added[0]
    assert len(split.method_changes) == 1
    origin = split.method_changes[0]
    assert origin["kind"] == "split_from"
    assert origin["from_payment_number"] == "PAY-1"
    assert origin["from_total"] == 100000.0
    assert origin["amount"] == 30000.0
    assert origin["old"] == "CASH" and origin["new"] == "ONLINE"
    assert origin["by"] == ADMIN["id"]
    assert origin["by_name"] == "Aziz Karimov"
    assert origin["note"] == "Yarmi online"
    # Ikkala qatorda tahrir payti bir xil
    assert origin["at"] == payment.method_changes[0]["at"]
    # Asl qator tarixi ajralgan qatorga aralashmaydi
    assert payment.method_changes[0]["kind"] == "partial"
    assert "from_payment_number" not in payment.method_changes[0]


def test_full_edit_records_the_unchanged_total(monkeypatch):
    payment = make_payment(method="CASH")
    svc, audit = build(monkeypatch, payment)

    edit(svc, ADMIN, payment.id, "CARD")

    entry = payment.method_changes[0]
    assert entry["kind"] == "full"
    assert entry["total_before"] == 250000.0
    assert entry["total_after"] == 250000.0
    assert float(payment.amount) == 250000.0
    assert svc.session.added == []
    assert audit[0]["new_values"]["new_payment_number"] is None


def test_partial_amounts_are_rounded_to_tiyin(monkeypatch):
    payment = make_payment(method="CASH")
    payment.amount = 100000.1
    svc, _ = build(monkeypatch, payment)

    edit(svc, ADMIN, payment.id, "ONLINE", amount=33333.33)

    entry = payment.method_changes[0]
    assert entry["total_before"] == 100000.1
    assert entry["total_after"] == 66666.77
    assert float(payment.amount) == 66666.77
