"""Panelning to'liq boshqaruv imkoniyatlari — haqiqiy PostgreSQL'da.

* Mehmonxonaga kirish (EnterService): yashirin sozlovchi hisobi ochiladi,
  token tanlangan mehmonxona/filial bilan, profil to'g'ri; yashirin hisob
  sozlovchilar ro'yxatida ko'rinmaydi; panel foydalanuvchisi to'xtatilsa
  hisob va sessiyalari yopiladi; boshqa mehmonxona filiali — rad.
* E'lon (BroadcastService): auditoriya (administratorlar / filial xodimlari),
  mehmonxona va filial bo'yicha cheklash, bildirishnoma filialga yoziladi,
  administrator takror olmaydi.
* Xodimni filialga o'tkazish, xodimlar ro'yxatida filial nomi.
* Tizim holati — baza bo'limi haqiqiy raqamlar bilan, MinIO yo'qligi
  sahifani yiqitmaydi.

Faqat lokal test bazasida: GOHOTEL_TEST_PG_URL.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from tests.test_branch_isolation_api import URL, seed
from tests.test_hotel_purge import run, wipe

pytestmark = pytest.mark.skipif(
    not URL or "test" not in URL.rsplit("/", 1)[-1] or not any(h in URL for h in ("127.0.0.1", "localhost")),
    reason="GOHOTEL_TEST_PG_URL (lokal test bazasi) berilmagan",
)


def panel_user(label="Tizim egasi"):
    return SimpleNamespace(id=uuid.uuid4(), label=label, is_root=True, is_active=True)


@pytest.fixture(scope="module")
def world():
    async def body(s):
        await wipe(s)
        return await seed(s)

    return run(body)


def test_enter_opens_hotel_as_hidden_configurator(world):
    async def body(s):
        from sqlalchemy import text

        from app.infrastructure.auth.jwt import decode_token
        from app.superadmin.enter_service import EnterService, hidden_username
        from app.superadmin.estate_service import EstateService

        actor = panel_user("Boss")
        svc = EnterService(s)
        result = await svc.enter(actor, world["H"], world["A2"], ip_address="1.2.3.4", user_agent="test")
        await s.commit()

        claims = decode_token(result["access_token"])
        assert claims["user_type"] == "CONFIGURATOR"
        assert claims["hotel_id"] == str(world["H"]) and claims["branch_id"] == str(world["A2"])
        me = result["user"]
        assert me["hotel_id"] == str(world["H"]) and me["branch_id"] == str(world["A2"])
        assert me["branch_name"] == "Chilonzor" and me["user_type"] == "CONFIGURATOR"
        assert result["hotel"]["id"] == str(world["H"])

        # Yashirin hisob: parol bilan kirib bo'lmaydi, ro'yxatda yo'q
        row = (await s.execute(text("SELECT id, status, hotel_id FROM users WHERE username=:u"),
                               {"u": hidden_username(actor.id)})).first()
        assert row is not None and row[1] == "ACTIVE" and row[2] is None
        names = {c["username"] for c in await EstateService(s).list_configurators()}
        assert hidden_username(actor.id) not in names
        assert str(world["configurator"]) in {c["id"] for c in await EstateService(s).list_configurators()}

        # Ikkinchi kirish — yangi hisob ochilmaydi, filial berilmasa asosiysi
        again = await svc.enter(actor, world["H"], None)
        await s.commit()
        assert decode_token(again["access_token"])["branch_id"] == str(world["A1"])
        count = (await s.execute(text("SELECT count(*) FROM users WHERE username LIKE 'panel__%'"))).scalar()
        assert count == 1
        live = (await s.execute(text("SELECT count(*) FROM user_sessions WHERE user_id=:u AND revoked_at IS NULL"),
                                {"u": row[0]})).scalar()
        assert live == 2

        # Panel foydalanuvchisi to'xtatildi — hisob va sessiyalar yopiladi
        await svc.disable_for(actor.id)
        await s.commit()
        status, live = (await s.execute(text(
            "SELECT u.status, (SELECT count(*) FROM user_sessions x WHERE x.user_id=u.id AND x.revoked_at IS NULL) "
            "FROM users u WHERE u.id=:u"), {"u": row[0]})).first()
        assert status == "INACTIVE" and live == 0

        # Boshqa mehmonxonaning filiali — rad
        from app.core.exceptions import ValidationException

        b_branch = (await s.execute(text("SELECT id FROM branches WHERE hotel_id=:h"), {"h": world["B"]})).scalar()
        with pytest.raises(ValidationException) as err:
            await svc.enter(actor, world["H"], b_branch)
        assert err.value.error_code == "BRANCH_NOT_IN_HOTEL"
        await s.rollback()
    run(body)


def test_broadcast_targets_and_scopes(world, monkeypatch):
    async def body(s):
        from sqlalchemy import text

        from app.infrastructure.push import firebase
        from app.superadmin.broadcast_service import BroadcastService

        pushed: list = []

        async def fake_push(token, title, body_, **kw):
            pushed.append(token)
            return 1

        monkeypatch.setattr(firebase, "send_push", fake_push)
        # Hamma xodimda token bo'lsin — push soni bilan tekshiramiz
        await s.execute(text("UPDATE users SET fcm_token = 'tok-' || id::text WHERE user_type IN ('ADMIN','EMPLOYEE')"))
        await s.commit()
        before = (await s.execute(text("SELECT count(*) FROM notifications"))).scalar()

        svc = BroadcastService(s)
        # 1) Hamma mehmonxonalar, faqat administratorlar: H da 1 admin, B da 0 → 1 ta
        r = await svc.send(title="Texnik ishlar", body="Bugun 23:00", audience="admins", actor="test")
        await s.commit()
        assert r["recipients"] == 1 and r["hotels"] == 2
        # 2) Bitta filial, barcha xodimlar: A2 xodimi + administrator
        r = await svc.send(title="A2 uchun", body=None, audience="staff",
                           hotel_id=world["H"], branch_id=world["A2"], send_push=False)
        await s.commit()
        assert r["recipients"] == 2 and r["hotels"] == 1
        rows = (await s.execute(text(
            "SELECT user_id, branch_id FROM notifications WHERE title='A2 uchun'"))).all()
        assert {str(u) for u, _ in rows} == {str(world["e2"]), str(world["admin"])}
        assert {b for _, b in rows} == {world["A2"]}  # xabar filialga yozildi
        # 3) Bitta mehmonxona, hamma xodimlar: e1, e2, admin (admin takrorlanmaydi)
        r = await svc.send(title="H uchun", body=None, audience="staff", hotel_id=world["H"], send_push=False)
        await s.commit()
        assert r["recipients"] == 3
        after = (await s.execute(text("SELECT count(*) FROM notifications"))).scalar()
        assert after - before == 1 + 2 + 3
        assert len(pushed) == 1  # faqat birinchi e'londa push yoqilgan edi

        from app.core.exceptions import NotFoundException, ValidationException

        with pytest.raises(ValidationException):
            await svc.send(title="  ", body=None, audience="admins")
        with pytest.raises(ValidationException):
            await svc.send(title="x", body=None, audience="everyone")
        with pytest.raises(ValidationException):
            await svc.send(title="x", body=None, audience="staff", branch_id=world["A2"])
        with pytest.raises(NotFoundException):
            await svc.send(title="x", body=None, audience="staff", hotel_id=uuid.uuid4())
        await s.rollback()
    run(body)


def test_move_staff_between_branches_and_list_shows_branch(world):
    async def body(s):
        from app.core.exceptions import ValidationException
        from app.superadmin.estate_service import EstateService
        from sqlalchemy import text

        estate = EstateService(s)
        staff = {u["id"]: u for u in await estate.list_users(world["H"])}
        assert staff[str(world["e1"])]["branch_name"] == "Markaz"
        assert staff[str(world["e2"])]["branch_id"] == str(world["A2"])

        moved = await estate.move_staff_to_branch(world["e1"], world["A2"])
        await s.commit()
        assert moved["branch_name"] == "Chilonzor"
        assert (await s.execute(text("SELECT branch_id FROM users WHERE id=:u"), {"u": world["e1"]})).scalar() == world["A2"]
        # qaytarib qo'yamiz
        await estate.move_staff_to_branch(world["e1"], world["A1"])
        await s.commit()

        b_branch = (await s.execute(text("SELECT id FROM branches WHERE hotel_id=:h"), {"h": world["B"]})).scalar()
        with pytest.raises(ValidationException) as err:
            await estate.move_staff_to_branch(world["e1"], b_branch)
        assert err.value.error_code == "BRANCH_NOT_IN_HOTEL"
        await s.rollback()
    run(body)


def test_system_snapshot_is_robust(world):
    async def body(s):
        from app.superadmin.system_service import SystemService

        snap = await SystemService(s).snapshot()
        assert set(snap) == {"app", "database", "storage", "push", "scheduler"}
        db = snap["database"]
        assert db["ok"] is True and db["counts"]["hotels"] == 2 and db["counts"]["branches"] == 3
        assert db["migration"] and db["version"]
        assert "ok" in snap["storage"] and "ok" in snap["push"]  # MinIO yo'q bo'lsa ham javob bor
        assert isinstance(snap["scheduler"]["auto_checkout_enabled"], bool)
        assert snap["app"]["uptime_seconds"] >= 0
    run(body)
