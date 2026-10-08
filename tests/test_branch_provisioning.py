"""Yangi filial, filialni o'chirish va filial bo'yicha tozalash — PostgreSQL'da.

* Yangi filial asosiy filialning sozlama va kataloglari (xona turlari,
  yoqilgan turlar, qulayliklar, xizmat narxlari, cheklistlar, hisob rejasi,
  do'kon mahsulotlari) NUSXASI bilan boshlanadi — zaxirasiz.
* Ish ma'lumoti yo'q filial kataloglari bilan birga o'chadi; mehmon, bron
  yoki xodimi bor filial o'chirilmaydi.
* "Ma'lumotlarni tozalash" faqat tanlangan filialni tozalaydi.

Faqat lokal test bazasida: GOHOTEL_TEST_PG_URL.
"""
from __future__ import annotations

import pytest

from tests.test_branch_isolation_api import URL, seed
from tests.test_hotel_purge import run, wipe

pytestmark = pytest.mark.skipif(
    not URL or "test" not in URL.rsplit("/", 1)[-1] or not any(h in URL for h in ("127.0.0.1", "localhost")),
    reason="GOHOTEL_TEST_PG_URL (lokal test bazasi) berilmagan",
)

CATALOGS = ("room_types", "hotel_room_types", "hotel_amenities", "hotel_services",
            "checklist_templates", "ledgers", "shop_products")


async def count(s, table, branch_id) -> int:
    from sqlalchemy import text

    return (await s.execute(text(f'SELECT count(*) FROM "{table}" WHERE branch_id = :b'), {"b": branch_id})).scalar()


def test_new_branch_starts_with_main_branch_settings_and_catalogs():
    async def body(s):
        from sqlalchemy import text

        from app.application.services.branch_service import BranchService

        await wipe(s)
        w = await seed(s)
        await s.execute(
            text("UPDATE branches SET settings = :st WHERE id = :b"),
            {"st": '{"shift": {"mode": "cash"}}', "b": w["A1"]},
        )
        branch = await BranchService(s).create_branch(w["H"], {"name": "Yunusobod", "code": "YUN"})
        await s.commit()
        assert branch.settings == {"shift": {"mode": "cash"}}
        for table in CATALOGS:
            assert await count(s, table, branch.id) == await count(s, table, w["A1"]) > 0, table
        # Zaxira (partiyalar) nusxalanmaydi
        assert await count(s, "shop_batches", branch.id) == 0
        # Yoqilgan xona turi o'z nusxasiga qaraydi (global tur — o'zi)
        bad = (await s.execute(text(
            "SELECT count(*) FROM hotel_room_types h JOIN room_types t ON t.id = h.room_type_id "
            "WHERE h.branch_id = :b AND t.branch_id IS NOT NULL AND t.branch_id <> :b"), {"b": branch.id})).scalar()
        assert bad == 0
        # Hisob rejasi: ota schyot ham shu filialniki
        bad = (await s.execute(text(
            "SELECT count(*) FROM ledgers l JOIN ledgers p ON p.id = l.parent_id "
            "WHERE l.branch_id = :b AND p.branch_id <> :b"), {"b": branch.id})).scalar()
        assert bad == 0
    run(body)


def test_branch_with_only_catalogs_can_be_deleted_but_not_one_with_data():
    async def body(s):
        from app.core.exceptions import ConflictException
        from app.superadmin.estate_service import EstateService

        await wipe(s)
        w = await seed(s)
        estate = EstateService(s)
        fresh = await estate.create_branch(w["H"], {"name": "Bo'sh", "code": "BOSH"})
        await s.commit()
        await estate.delete_branch(fresh["id"])
        await s.commit()
        for table in CATALOGS:
            assert await count(s, table, fresh["id"]) == 0
        with pytest.raises(ConflictException) as err:
            await estate.delete_branch(w["A2"])
        assert err.value.error_code == "BRANCH_NOT_EMPTY"
        await s.rollback()
    run(body)


def test_reset_data_touches_only_the_chosen_branch():
    async def body(s):
        from sqlalchemy import text

        from app.application.services.maintenance_service import MaintenanceService

        await wipe(s)
        w = await seed(s)
        before = {t: await count(s, t, w["A1"]) for t in ("reservations", "payments", "shop_sales", "expenses")}
        await MaintenanceService(s).reset_data(w["H"], include_employees=True, branch_id=w["A2"])
        await s.commit()
        for table in ("reservations", "payments", "shop_sales", "expenses"):
            assert await count(s, table, w["A2"]) == 0, table
            assert await count(s, table, w["A1"]) == before[table] > 0, table
        users = dict((await s.execute(text("SELECT id, true FROM users WHERE id IN (:a, :b)"),
                                      {"a": w["e1"], "b": w["e2"]})).all())
        assert w["e1"] in users and w["e2"] not in users
    run(body)
