"""Do'kon: partiya sotuv va olish narxini o'zgartirish.

Ishga tushirish: pytest tests/test_shop_prices.py

Nozik joylar:

1. Faqat YUBORILGAN narx o'zgaradi: sotuv narxi yuborilsa olish narxi
   saqlanadi (va aksincha) — eski mijoz faqat `cost_price` yuboradi.
2. Sotuv narxi KEYINGI sotuvga ta'sir qiladi; o'tgan chek qatori o'z
   narxini saqlaydi. Olish narxi foyda hisobini o'tgan sotuvlar uchun ham
   o'zgartiradi.
3. Bir nechta partiya bitta tranzaksiyada; boshqa mahsulot partiyasi —
   404, takror — 422; huquq — faqat do'kon boshqaruvchisi.
4. Har o'zgarish audit jurnalida (kim, nimadan nimaga); o'zgarmagan
   qiymat yozilmaydi.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.audit_log import AuditLog
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.shop import (
    ShopBatch,
    ShopProduct,
    ShopSale,
    ShopSaleItem,
    ShopSalePayment,
    ShopWriteoff,
)
from app.infrastructure.database.models.user import User
from app.presentation.api.v1 import shop as api
from tests.test_shop_profit import (
    ADMIN,
    ADMIN_ID,
    FINANCE,
    MANAGER,
    product,
    profit,
    sell,
)


def run(body):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [
            Hotel.__table__, Branch.__table__, User.__table__, ShopProduct.__table__, ShopBatch.__table__,
            ShopSale.__table__, ShopSaleItem.__table__, ShopSalePayment.__table__, ShopWriteoff.__table__,
            AuditLog.__table__,
        ]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as session:
                session.add(User(id=ADMIN_ID, first_name="Aziz", last_name="Admin", user_type="ADMIN",
                                 username="admin", password_hash="x"))
                await session.flush()
                await body(session)
        finally:
            await engine.dispose()

    asyncio.run(main())


async def batches_of(session, p) -> list[ShopBatch]:
    return list(
        (await session.execute(
            select(ShopBatch).where(ShopBatch.product_id == p.id).order_by(ShopBatch.created_at)
        )).scalars()
    )


def item(batch, **prices):
    return api.BatchPriceItem(batch_id=batch.id, **prices)


async def bulk(session, p, items, user=ADMIN, note=None):
    session.expunge_all()
    return await api.update_batch_prices(p.id, api.BatchPricesRequest(items=items, note=note), None, session, user)


def test_single_batch_changes_only_what_was_sent():
    async def body(session):
        cola = await product(session, "Cola", [(10, 6000, 10000)])
        [b] = await batches_of(session, cola)
        # Faqat sotuv narxi — olish narxi saqlanadi
        out = await api.update_batch_cost(b.id, api.BatchCostRequest(sale_price=12000), None, session, MANAGER)
        assert out["sale_price"] == 12000.0 and out["cost_price"] == 6000.0
        # Eski mijoz: faqat olish narxi — sotuv narxi saqlanadi
        out = await api.update_batch_cost(b.id, api.BatchCostRequest(cost_price=6500), None, session, ADMIN)
        assert out["sale_price"] == 12000.0 and out["cost_price"] == 6500.0
        # Olish narxini olib tashlash (null)
        out = await api.update_batch_cost(b.id, api.BatchCostRequest(cost_price=None), None, session, ADMIN)
        assert out["cost_price"] is None and out["sale_price"] == 12000.0
        with pytest.raises(ValidationException) as e:
            await api.update_batch_cost(b.id, api.BatchCostRequest(sale_price=None), None, session, ADMIN)
        assert e.value.error_code == "SHOP_SALE_PRICE_REQUIRED"
        with pytest.raises(ValueError):
            api.BatchCostRequest(sale_price=0)
        with pytest.raises(ForbiddenException):
            await api.update_batch_cost(b.id, api.BatchCostRequest(sale_price=1), None, session, FINANCE)
        # Audit: 3 ta o'zgarish yozildi
        logs = (await session.execute(select(AuditLog))).scalars().all()
        assert len(logs) == 3 and all(log.entity_type == "shop_batch_prices" for log in logs)
        first = logs[0].new_values["batches"][0]
        assert first["sale_price"] == {"old": 10000.0, "new": 12000.0} and "cost_price" not in first

    run(body)


def test_bulk_update_next_sale_uses_new_price_old_receipt_keeps_its_own():
    async def body(session):
        cola = await product(session, "Cola", [(5, 6000, 10000), (5, None, 11000)])
        old_sale = await sell(session, [(cola, 2)])
        b1, b2 = await batches_of(session, cola)

        out = await bulk(session, cola, [
            item(b1, sale_price=12000),
            item(b2, sale_price=12000, cost_price=7000),
        ], note="Yangi narx")
        assert out["changed"] == 2
        assert out["current_price"] == 12000.0
        assert [(b["sale_price"], b["cost_price"]) for b in out["batches"]] == [(12000.0, 6000.0), (12000.0, 7000.0)]
        assert [b["remaining"] for b in out["batches"]] == [3, 5]  # qoldiq o'zgarmadi

        # O'tgan chek o'z narxida
        old_items = (await session.execute(
            select(ShopSaleItem).where(ShopSaleItem.sale_id == uuid.UUID(old_sale["id"]))
        )).scalars().all()
        assert {float(i.unit_price) for i in old_items} == {10000.0}
        # Keyingi sotuv — yangi narxda
        new_sale = await sell(session, [(cola, 4)])
        assert new_sale["total_amount"] == 48000.0

        log = (await session.execute(select(AuditLog))).scalar_one()
        assert log.new_values["note"] == "Yangi narx" and len(log.new_values["batches"]) == 2
        assert log.old_values == {"product": "Cola"}

        # Foyda: ikkinchi partiyadan sotilgan 1 dona endi aniq tannarxda (7000)
        res = await profit(session)
        row = {r["name"]: r for r in res["products"]}["Cola"]
        assert row["cost_status"] == "exact"
        assert row["cost"] == 5 * 6000.0 + 1 * 7000.0

    run(body)


def test_bulk_validation_and_noop():
    async def body(session):
        cola = await product(session, "Cola", [(5, 6000, 10000)])
        suv = await product(session, "Suv", [(5, 3000, 5000)])
        [b] = await batches_of(session, cola)
        [other] = await batches_of(session, suv)
        with pytest.raises(NotFoundException):
            await bulk(session, cola, [item(other, sale_price=1)])
        with pytest.raises(ValidationException) as e:
            await bulk(session, cola, [item(b, sale_price=11000), item(b, cost_price=1)])
        assert e.value.error_code == "SHOP_BATCH_DUPLICATE"
        with pytest.raises(ForbiddenException):
            await bulk(session, cola, [item(b, sale_price=1)], user=FINANCE)
        with pytest.raises(NotFoundException):
            await api.update_batch_prices(uuid.uuid4(), api.BatchPricesRequest(items=[item(b, sale_price=1)]),
                                          None, session, ADMIN)
        # Xato bo'lsa hech narsa o'zgarmagan
        assert float((await batches_of(session, cola))[0].sale_price) == 10000.0

        # Bir xil qiymat — o'zgarish yo'q, audit yozilmaydi
        out = await bulk(session, cola, [item(b, sale_price=10000, cost_price=6000)])
        assert out["changed"] == 0
        assert (await session.execute(select(AuditLog))).scalars().all() == []
        with pytest.raises(ValueError):
            api.BatchPricesRequest(items=[])

    run(body)


def test_route_registered():
    paths = {(tuple(sorted(r.methods)), r.path) for r in api.router.routes}
    assert (("PUT",), "/products/{product_id}/batches") in paths
    assert (("POST",), "/products/{product_id}/batches") in paths
    assert (("PUT",), "/batches/{batch_id}") in paths
