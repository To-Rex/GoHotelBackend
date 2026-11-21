"""Do'kon foydasi va zarari (shop_profit_service, GET /shop/profit).

Ishga tushirish: pytest tests/test_shop_profit.py

Nozik joylar:

1. Tannarx — sotuv qatorining O'Z partiyasidan (FIFO). Partiyada tannarx
   yo'q bo'lsa — mahsulotning tannarxi ma'lum partiyalari o'rtachasi
   (TAXMINIY); umuman yo'q bo'lsa sotuv foydaga KIRMAYDI (`uncovered`).
2. Tannarx keyin kiritilsa (PUT /shop/batches/{id}) o'tgan sotuv va
   spisaniye ham aniq hisoblanadi.
3. Zarar: spisaniye va inventarizatsiya kamomadi tannarxda; ortiqcha
   topilgani foyda. Sof foyda = yalpi − spisaniye − kamomad + ortiqcha.
4. Davr — sotuv (chek) sanasi; huquq — administrator, finance.view yoki
   do'kon boshqaruvchisi; tannarxni faqat boshqaruvchi o'zgartiradi.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.application.services.shop_profit_service import ShopProfitService
from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
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


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kw):  # shop_sales.payments uchun
    return "JSON"


HOTEL = uuid.uuid4()
OTHER = uuid.uuid4()
ADMIN_ID = uuid.uuid4()
ADMIN = {"user_type": "ADMIN", "id": ADMIN_ID, "hotel_id": HOTEL, "permissions": []}
SELLER = {"user_type": "EMPLOYEE", "id": ADMIN_ID, "hotel_id": HOTEL, "permissions": ["reservation.create"]}
FINANCE = {"user_type": "EMPLOYEE", "id": ADMIN_ID, "hotel_id": HOTEL, "permissions": ["finance.view"]}
MANAGER = {"user_type": "EMPLOYEE", "id": ADMIN_ID, "hotel_id": HOTEL, "permissions": ["service.manage"]}
T0 = datetime(2026, 11, 1, 8, 0)


def run(body):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [
            Hotel.__table__, Branch.__table__, User.__table__, ShopProduct.__table__, ShopBatch.__table__,
            ShopSale.__table__, ShopSaleItem.__table__, ShopSalePayment.__table__, ShopWriteoff.__table__,
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


async def product(session, name, batches, hotel=HOTEL) -> ShopProduct:
    """batches: [(miqdor, tannarx|None, sotuv narxi)] — eskisidan yangisiga."""
    p = ShopProduct(hotel_id=hotel, name=name, emoji="🥤", created_by=ADMIN_ID)
    session.add(p)
    await session.flush()
    for i, (qty, cost, sale) in enumerate(batches):
        session.add(ShopBatch(hotel_id=hotel, product_id=p.id, quantity=qty, remaining=qty, cost_price=cost,
                              sale_price=sale, created_by=ADMIN_ID, created_at=T0 + timedelta(hours=i)))
    await session.flush()
    return p


async def sell(session, items, user=ADMIN, when: datetime | None = None) -> dict:
    data = api.SaleCreateRequest(
        items=[api.SaleItemRequest(product_id=p.id, quantity=q) for p, q in items], payment_method="CASH"
    )
    out = await api.create_sale(data, hotel_id=None, session=session, current_user=user, _shift=None)
    if when is not None:
        sale = await session.get(ShopSale, uuid.UUID(out["id"]))
        sale.created_at = when
        await session.flush()
    return out


async def profit(session, user=ADMIN, date_from=None, date_to=None) -> dict:
    return await api.shop_profit(date_from=date_from, date_to=date_to, hotel_id=None, session=session,
                                 current_user=user)


def rows_by_name(res) -> dict:
    return {r["name"]: r for r in res["products"]}


# ------------------------------------------------------- sotuv foydasi --


def test_exact_estimated_and_uncovered_cost():
    async def body(session):
        cola = await product(session, "Cola", [(10, 6000, 10000), (10, None, 12000)])
        choy = await product(session, "Choy", [(10, None, 5000)])
        # 12 ta Cola: 10 tasi 1-partiyadan (6000), 2 tasi 2-partiyadan (tannarx yo'q)
        await sell(session, [(cola, 12), (choy, 3)])
        res = await profit(session)

        assert res["sales"] == {"count": 1, "items_qty": 15, "revenue": 139000.0, "unpaid": 0.0}
        # Cola tannarxi: 10×6000 + 2×6000 (taxminiy — o'rtacha) ; Choy — noma'lum
        assert res["cost"] == {"total": 72000.0, "estimated": 12000.0}
        assert res["revenue_covered"] == 124000.0
        assert res["gross_profit"] == 52000.0
        assert res["margin"] == round(52000 / 124000 * 100, 2)
        assert res["uncovered"] == {"revenue": 15000.0, "qty": 3, "products": 1}
        rows = rows_by_name(res)
        assert rows["Cola"]["cost_status"] == "estimated" and rows["Cola"]["profit"] == 52000.0
        assert rows["Choy"]["cost_status"] == "unknown" and rows["Choy"]["profit"] == 0.0
        assert rows["Choy"]["margin"] is None
        # Tannarxi yo'q partiyalar ro'yxati — Cola 2-partiyasi va Choy
        missing = {(m["product_name"], m["sold_in_period"]) for m in res["missing_cost_batches"]}
        assert missing == {("Cola", 2), ("Choy", 3)}
        assert res["net_profit"] == 52000.0

    run(body)


def test_cost_entered_later_makes_past_sales_exact():
    async def body(session):
        cola = await product(session, "Cola", [(10, 6000, 10000), (10, None, 12000)])
        await sell(session, [(cola, 12)])
        batch2 = (await session.execute(
            select(ShopBatch).where(ShopBatch.cost_price.is_(None))
        )).scalar_one()

        with pytest.raises(ForbiddenException):
            await api.update_batch_cost(batch2.id, api.BatchCostRequest(cost_price=7000), None, session, FINANCE)
        with pytest.raises(NotFoundException):
            await api.update_batch_cost(uuid.uuid4(), api.BatchCostRequest(cost_price=7000), None, session, ADMIN)
        out = await api.update_batch_cost(batch2.id, api.BatchCostRequest(cost_price=7000), None, session, MANAGER)
        assert out["cost_price"] == 7000.0 and out["sale_price"] == 12000.0 and out["remaining"] == 8

        res = await profit(session)
        assert res["cost"] == {"total": 74000.0, "estimated": 0.0}
        assert res["gross_profit"] == 124000.0 - 74000.0
        assert rows_by_name(res)["Cola"]["cost_status"] == "exact"
        assert res["missing_cost_batches"] == []

    run(body)


def test_below_cost_sale_is_flagged():
    async def body(session):
        suv = await product(session, "Suv", [(10, 4000, 3000)])
        await sell(session, [(suv, 2)])
        res = await profit(session)
        assert res["gross_profit"] == -2000.0
        assert res["below_cost"] == {"qty": 2, "loss": 2000.0, "products": 1}
        assert rows_by_name(res)["Suv"]["margin"] == round(-2000 / 6000 * 100, 2)

    run(body)


# ---------------------------------------------------------- zararlar --


def test_writeoff_shortage_and_surplus():
    async def body(session):
        cola = await product(session, "Cola", [(5, 6000, 10000), (5, 7000, 11000)])
        suv = await product(session, "Suv", [(10, 4000, 6000)])
        await sell(session, [(cola, 4)])  # 4×6000 tannarx, 40 000 sotuv
        # Har so'rov o'z sessiyasida — endpointlar mahsulotni partiyalari bilan
        # qaytadan yuklasin (test sessiyasidagi eski nusxa ishlatilmasin)
        session.expunge_all()

        # Spisaniye 2 ta Cola: 1 tasi 1-partiyadan (6000), 1 tasi 2-dan (7000)
        await api.writeoff_product(cola.id, api.WriteoffRequest(quantity=2, reason="singan"), None, session, ADMIN)
        # Inventarizatsiya: Suv 10 o'rniga 7 — kamomad 3×4000
        await api.inventory_product(suv.id, api.InventoryRequest(counted=7), None, session, ADMIN)
        # Inventarizatsiya: Cola 4 o'rniga 6 — ortiqcha 2 ta (oxirgi partiya narxida, 7000)
        await api.inventory_product(cola.id, api.InventoryRequest(counted=6), None, session, ADMIN)

        rows = (await session.execute(select(ShopWriteoff))).scalars().all()
        by_kind = {(w.kind, w.quantity > 0, w.product_id): w for w in rows}
        spis = by_kind[("WRITEOFF", True, cola.id)]
        assert [(p["quantity"], p["cost_price"]) for p in spis.batches] == [(1, 6000.0), (1, 7000.0)]
        assert sum(p["quantity"] for p in by_kind[("INVENTORY", True, suv.id)].batches) == 3
        # Ortiqcha — yangi qo'shilgan partiya yoziladi
        extra = by_kind[("INVENTORY", False, cola.id)].batches
        assert len(extra) == 1 and extra[0]["quantity"] == 2 and extra[0]["cost_price"] == 7000.0

        res = await profit(session)
        assert res["gross_profit"] == 40000.0 - 24000.0
        assert res["writeoff"]["qty"] == 2 and res["writeoff"]["cost"] == 13000.0
        assert res["writeoff"]["sale_value"] == 21000.0 and res["writeoff"]["estimated"] is False
        assert res["shortage"]["qty"] == 3 and res["shortage"]["cost"] == 12000.0
        assert res["surplus"]["qty"] == 2 and res["surplus"]["cost"] == 14000.0
        assert res["losses"] == 25000.0
        assert res["net_profit"] == 16000.0 - 13000.0 - 12000.0 + 14000.0
        r = rows_by_name(res)
        assert r["Suv"]["sold_qty"] == 0 and r["Suv"]["cost_status"] == "none"
        assert r["Suv"]["shortage_cost"] == 12000.0 and r["Suv"]["net"] == -12000.0
        assert r["Cola"]["losses"] == 13000.0 - 14000.0

    run(body)


def test_legacy_writeoff_uses_average_cost():
    async def body(session):
        cola = await product(session, "Cola", [(5, 6000, 10000), (5, 8000, 10000)])
        # Eski yozuv — partiyalari saqlanmagan
        session.add(ShopWriteoff(hotel_id=HOTEL, product_id=cola.id, kind="WRITEOFF", quantity=2, reason="eski",
                                 created_by=ADMIN_ID, batches=None))
        choy = await product(session, "Choy", [(5, None, 5000)])
        session.add(ShopWriteoff(hotel_id=HOTEL, product_id=choy.id, kind="WRITEOFF", quantity=1, reason="eski",
                                 created_by=ADMIN_ID, batches=None))
        await session.flush()
        res = await profit(session)
        assert res["writeoff"]["cost"] == 14000.0  # 2 × o'rtacha 7000
        assert res["writeoff"]["estimated"] is True and res["writeoff"]["unknown_qty"] == 1
        assert rows_by_name(res)["Choy"]["loss_unknown_qty"] == 1

    run(body)


# ------------------------------------------------------ davr, qoldiq --


def test_period_unpaid_and_stock_value():
    async def body(session):
        cola = await product(session, "Cola", [(10, 6000, 10000)])
        await product(session, "Choy", [(10, None, 5000)])
        await sell(session, [(cola, 1)], when=datetime(2026, 11, 10, 6, 0))   # 11:00 mahalliy
        await sell(session, [(cola, 2)], when=datetime(2026, 11, 11, 20, 0))  # 12-noyabr 01:00 mahalliy
        res = await profit(session, date_from=date(2026, 11, 10), date_to=date(2026, 11, 11))
        assert res["sales"]["count"] == 1 and res["sales"]["revenue"] == 10000.0
        res = await profit(session, date_from=date(2026, 11, 12), date_to=date(2026, 11, 12))
        assert res["sales"]["revenue"] == 20000.0

        # Bronga yozilgan (to'lanmagan) savdo — foydada bor, qoldig'i "unpaid"
        sale = ShopSale(hotel_id=HOTEL, total_amount=10000, status="PENDING", paid_amount=4000,
                        created_by=ADMIN_ID, created_at=datetime(2026, 11, 12, 5, 0))
        session.add(sale)
        await session.flush()
        batch = (await session.execute(select(ShopBatch).where(ShopBatch.product_id == cola.id))).scalar_one()
        session.add(ShopSaleItem(sale_id=sale.id, product_id=cola.id, batch_id=batch.id, product_name="Cola",
                                 quantity=1, unit_price=10000, total_price=10000))
        batch.remaining -= 1
        await session.flush()
        res = await profit(session, date_from=date(2026, 11, 12), date_to=date(2026, 11, 12))
        assert res["sales"]["count"] == 2 and res["sales"]["unpaid"] == 6000.0

        # Ombor: Cola 6 ta qoldi (tannarx 36 000, sotuv 60 000), Choy 10 ta tannarxsiz
        assert res["stock"]["qty"] == 16
        assert res["stock"]["cost_value"] == 36000.0 and res["stock"]["sale_value"] == 110000.0
        assert res["stock"]["unknown_cost_qty"] == 10 and res["stock"]["potential_profit"] == 24000.0

        with pytest.raises(ValidationException):
            await profit(session, date_from=date(2026, 11, 12), date_to=date(2026, 11, 1))

    run(body)


def test_other_hotel_is_not_mixed_in():
    async def body(session):
        mine = await product(session, "Cola", [(10, 6000, 10000)])
        await product(session, "Begona", [(10, None, 1)], hotel=OTHER)
        await sell(session, [(mine, 1)])
        res = await profit(session)
        assert [r["name"] for r in res["products"]] == ["Cola"]
        assert all(m["product_name"] != "Begona" for m in res["missing_cost_batches"])
        assert res["stock"]["qty"] == 9

    run(body)


# ------------------------------------------------------------ huquq --


def test_who_can_see_profit():
    async def body(session):
        await product(session, "Cola", [(10, 6000, 10000)])
        with pytest.raises(ForbiddenException) as e:
            await profit(session, user=SELLER)
        assert e.value.error_code == "SHOP_PROFIT_FORBIDDEN"
        assert (await profit(session, user=FINANCE))["can_edit_cost"] is False
        assert (await profit(session, user=MANAGER))["can_edit_cost"] is True
        assert (await profit(session, user=ADMIN))["can_edit_cost"] is True

    run(body)


def test_empty_period():
    async def body(session):
        res = await ShopProfitService(session).build(HOTEL, date(2026, 1, 1), date(2026, 1, 2))
        assert res["sales"]["revenue"] == 0 and res["margin"] is None and res["products"] == []
        assert res["net_profit"] == 0 and res["missing_cost_batches"] == []

    run(body)


def test_routes_registered():
    paths = {(tuple(sorted(r.methods)), r.path) for r in api.router.routes}
    assert (("GET",), "/profit") in paths
    assert (("PUT",), "/batches/{batch_id}") in paths
