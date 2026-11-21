"""Do'kon foydasi va zarari — Moliya → Do'kon bo'limining statistikasi.

Davr — SOTUV sanasi (chek yozilgan mahalliy kun): mahsulot qaysi kuni
sotilgan bo'lsa, foydasi o'sha kunga tegishli. Bronga yozilgan (hali
to'lanmagan) sotuv ham kiradi — mahsulot ombordan chiqib ketgan; uning
to'lanmagan qismi alohida (`sales.unpaid`) ko'rsatiladi. Do'kon TUSHUMI
(`/finance/summary`) esa pul olingan kun bo'yicha — ikkalasi farq qilishi
tabiiy, ekranda shunday yoziladi.

Hisob:

* Sotuv summasi — chek qatorlari (`ShopSaleItem.total_price`).
* Tannarx — har qatorning O'Z partiyasi narxi (FIFO bilan qaysi partiyadan
  olingan bo'lsa): `quantity × batch.cost_price`. Partiyaning tannarxi keyin
  kiritilsa (`PUT /shop/batches/{id}`) hisobot ham o'zgaradi.
  Partiyada tannarx yo'q bo'lsa — shu mahsulotning tannarxi ma'lum
  partiyalari o'rtachasi (TAXMINIY, `cost.estimated`); umuman yo'q bo'lsa —
  bu sotuv foydaga KIRMAYDI va alohida ko'rsatiladi (`uncovered`), aks holda
  tannarxsiz sotuv 100% foyda bo'lib ko'rinardi.
* Yalpi foyda = tannarxi ma'lum sotuv − tannarx; marja — shu sotuvga nisbatan.
* Zarar: spisaniye (WRITEOFF) va inventarizatsiyada chiqqan kamomad —
  tannarxda. Inventarizatsiyada ortiqcha topilgani — foyda (qoldiqqa
  qo'shilgan tannarx). Yangi yozuvlarda qaysi partiyadan qancha chiqqani
  `shop_writeoffs.batches` da; eski yozuvlarda — mahsulot o'rtacha
  tannarxi (taxminiy).
* Tannarxidan arzon sotilgan qatorlar — `below_cost` (zarar bilan sotuv).
* Sof foyda = yalpi foyda − spisaniye − kamomad + ortiqcha.

Ombordagi qoldiq qiymati (`stock`) — JORIY holat, davrga bog'liq emas.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.shop import (
    ShopBatch,
    ShopProduct,
    ShopSale,
    ShopSaleItem,
    ShopWriteoff,
)
from app.shared.utils import local_day_bounds

#: Tannarxi yo'q partiyalar ro'yxati — ko'pi bilan shuncha (eng yangisi avval)
MAX_MISSING_BATCHES = 300


def _num(v) -> float:
    return float(v or 0)


def _r(v: float) -> float:
    return round(float(v or 0), 2)


def _product_row(pid: str) -> dict:
    return {
        "product_id": pid,
        "name": None,
        "emoji": None,
        "is_active": True,
        "sold_qty": 0,
        "revenue": 0.0,
        "revenue_covered": 0.0,
        "cost": 0.0,
        "cost_estimated": 0.0,
        "uncovered_qty": 0,
        "uncovered_revenue": 0.0,
        "estimated_qty": 0,
        "below_cost_qty": 0,
        "below_cost_loss": 0.0,
        "writeoff_qty": 0,
        "writeoff_cost": 0.0,
        "shortage_qty": 0,
        "shortage_cost": 0.0,
        "surplus_qty": 0,
        "surplus_cost": 0.0,
        "loss_unknown_qty": 0,
    }


class ShopProfitService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _avg_costs(self, hotel_id: UUID) -> dict[str, tuple[float | None, float | None]]:
        """Mahsulot → (o'rtacha tannarx, o'rtacha sotuv narxi) — tannarxi
        ma'lum partiyalar bo'yicha, miqdor bilan tortilgan."""
        rows = (
            await self.session.execute(
                select(
                    ShopBatch.product_id,
                    ShopBatch.quantity,
                    ShopBatch.cost_price,
                    ShopBatch.sale_price,
                ).where(ShopBatch.hotel_id == hotel_id)
            )
        ).all()
        acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        for pid, qty, cost, sale in rows:
            a = acc[str(pid)]
            q = float(qty or 0)
            a[2] += q * _num(sale)
            a[3] += q
            if cost is not None:
                a[0] += q * _num(cost)
                a[1] += q
        out: dict[str, tuple[float | None, float | None]] = {}
        for pid, (cost_sum, cost_qty, sale_sum, sale_qty) in acc.items():
            out[pid] = (
                cost_sum / cost_qty if cost_qty else None,
                sale_sum / sale_qty if sale_qty else None,
            )
        return out

    async def build(
        self,
        hotel_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        *,
        can_edit_cost: bool = False,
    ) -> dict:
        start, end = local_day_bounds(date_from, date_to)
        avg = await self._avg_costs(hotel_id)
        products: dict[str, dict] = {}

        def prow(pid) -> dict:
            key = str(pid)
            if key not in products:
                products[key] = _product_row(key)
            return products[key]

        # ------------------------------------------------ sotuvlar --
        q = (
            select(
                ShopSaleItem.product_id,
                ShopSaleItem.product_name,
                ShopSaleItem.quantity,
                ShopSaleItem.unit_price,
                ShopSaleItem.total_price,
                ShopSaleItem.batch_id,
                ShopBatch.cost_price,
                ShopSale.id,
                ShopSale.total_amount,
                ShopSale.paid_amount,
                ShopSale.status,
            )
            .join(ShopSale, ShopSale.id == ShopSaleItem.sale_id)
            .outerjoin(ShopBatch, ShopBatch.id == ShopSaleItem.batch_id)
            .where(ShopSale.hotel_id == hotel_id)
        )
        if start:
            q = q.where(ShopSale.created_at >= start)
        if end:
            q = q.where(ShopSale.created_at < end)

        sales: dict[str, float] = {}
        sold_batches: dict[str, int] = defaultdict(int)
        items_qty = 0
        below_products: set[str] = set()
        for (pid, pname, qty, unit, total, batch_id, cost, sale_id, sale_total,
             paid, status) in (await self.session.execute(q)).all():
            row = prow(pid)
            row["name"] = row["name"] or pname
            qty = int(qty or 0)
            total = _num(total)
            items_qty += qty
            row["sold_qty"] += qty
            row["revenue"] += total
            if batch_id is not None:
                sold_batches[str(batch_id)] += qty
            # Savdo bo'yicha to'lanmagan qoldiq (bir marta)
            if str(sale_id) not in sales:
                paid_v = _num(sale_total) if paid is None and status == "PAID" else _num(paid)
                sales[str(sale_id)] = max(_num(sale_total) - paid_v, 0.0)

            unit_cost = _num(cost) if cost is not None else None
            estimated = False
            if unit_cost is None:
                fallback = avg.get(str(pid), (None, None))[0]
                if fallback is not None:
                    unit_cost, estimated = fallback, True
            if unit_cost is None:
                row["uncovered_qty"] += qty
                row["uncovered_revenue"] += total
                continue
            line_cost = qty * unit_cost
            row["revenue_covered"] += total
            row["cost"] += line_cost
            if estimated:
                row["cost_estimated"] += line_cost
                row["estimated_qty"] += qty
            elif _num(unit) < unit_cost:
                row["below_cost_qty"] += qty
                row["below_cost_loss"] += line_cost - total
                below_products.add(str(pid))

        # ------------------------------- spisaniye va inventarizatsiya --
        wq = select(ShopWriteoff).where(ShopWriteoff.hotel_id == hotel_id)
        if start:
            wq = wq.where(ShopWriteoff.created_at >= start)
        if end:
            wq = wq.where(ShopWriteoff.created_at < end)
        writeoffs = (await self.session.execute(wq)).scalars().all()

        # Yozuvdagi partiyalarning JORIY tannarxi (keyin kiritilgan bo'lsa ham)
        batch_ids = {
            str(b.get("batch_id"))
            for w in writeoffs
            for b in (w.batches or [])
            if isinstance(b, dict) and b.get("batch_id")
        }
        live_cost: dict[str, float | None] = {}
        live_sale: dict[str, float | None] = {}
        if batch_ids:
            for bid, cost, sale in (
                await self.session.execute(
                    select(ShopBatch.id, ShopBatch.cost_price, ShopBatch.sale_price).where(
                        ShopBatch.id.in_([UUID(b) for b in batch_ids])
                    )
                )
            ).all():
                live_cost[str(bid)] = _num(cost) if cost is not None else None
                live_sale[str(bid)] = _num(sale)

        writeoff = {"count": 0, "qty": 0, "cost": 0.0, "sale_value": 0.0, "estimated": False, "unknown_qty": 0}
        shortage = {"count": 0, "qty": 0, "cost": 0.0, "sale_value": 0.0, "estimated": False, "unknown_qty": 0}
        surplus = {"count": 0, "qty": 0, "cost": 0.0, "sale_value": 0.0, "estimated": False, "unknown_qty": 0}

        for w in writeoffs:
            row = prow(w.product_id)
            qty = abs(int(w.quantity or 0))
            if w.kind == "WRITEOFF":
                bucket, qty_key, cost_key = writeoff, "writeoff_qty", "writeoff_cost"
            elif int(w.quantity or 0) > 0:
                bucket, qty_key, cost_key = shortage, "shortage_qty", "shortage_cost"
            else:
                bucket, qty_key, cost_key = surplus, "surplus_qty", "surplus_cost"
            bucket["count"] += 1
            bucket["qty"] += qty
            row[qty_key] += qty

            parts = [b for b in (w.batches or []) if isinstance(b, dict)]
            fallback_cost, fallback_sale = avg.get(str(w.product_id), (None, None))
            known_qty = 0
            for part in parts:
                pq = abs(int(part.get("quantity") or 0))
                bid = str(part.get("batch_id") or "")
                cost = live_cost.get(bid) if bid in live_cost else None
                if cost is None and part.get("cost_price") is not None:
                    cost = _num(part.get("cost_price"))
                sale = live_sale.get(bid) if bid in live_sale else part.get("sale_price")
                if cost is None and fallback_cost is not None:
                    cost = fallback_cost
                    bucket["estimated"] = True
                if cost is None:
                    bucket["unknown_qty"] += pq
                    row["loss_unknown_qty"] += pq
                else:
                    bucket["cost"] += pq * cost
                    row[cost_key] += pq * cost
                if sale is not None:
                    bucket["sale_value"] += pq * _num(sale)
                known_qty += pq
            rest = qty - known_qty
            if rest > 0:
                # Eski yozuv (partiyalari saqlanmagan) — o'rtacha tannarx
                if fallback_cost is not None:
                    bucket["cost"] += rest * fallback_cost
                    row[cost_key] += rest * fallback_cost
                    bucket["estimated"] = True
                else:
                    bucket["unknown_qty"] += rest
                    row["loss_unknown_qty"] += rest
                if fallback_sale is not None:
                    bucket["sale_value"] += rest * fallback_sale

        # ---------------------------------------------- mahsulot nomlari --
        if products:
            for p in (
                await self.session.execute(
                    select(ShopProduct).where(
                        ShopProduct.id.in_([UUID(k) for k in products])
                    )
                )
            ).scalars():
                row = products.get(str(p.id))
                if row is not None:
                    row["name"] = p.name
                    row["emoji"] = p.emoji
                    row["is_active"] = bool(p.is_active)

        # ------------------------------------------ ombordagi qoldiq --
        stock = {"qty": 0, "cost_value": 0.0, "sale_value": 0.0, "covered_sale_value": 0.0, "unknown_cost_qty": 0}
        missing: list[dict] = []
        # Qoldig'i bor partiyalar va shu davrda sotilganlari (tannarxi yo'qlari
        # ro'yxati uchun — qoldig'i tugagan bo'lsa ham foydaga ta'sir qiladi)
        relevant = ShopBatch.remaining > 0
        if sold_batches:
            relevant = or_(relevant, ShopBatch.id.in_([UUID(b) for b in sold_batches]))
        batch_rows = (
            await self.session.execute(
                select(ShopBatch, ShopProduct.name, ShopProduct.emoji)
                .join(ShopProduct, ShopProduct.id == ShopBatch.product_id)
                .where(ShopBatch.hotel_id == hotel_id, relevant)
                .order_by(ShopBatch.created_at.desc())
            )
        ).all()
        for b, pname, emoji in batch_rows:
            remaining = int(b.remaining or 0)
            if remaining > 0:
                stock["qty"] += remaining
                stock["sale_value"] += remaining * _num(b.sale_price)
                if b.cost_price is not None:
                    stock["cost_value"] += remaining * _num(b.cost_price)
                    stock["covered_sale_value"] += remaining * _num(b.sale_price)
                else:
                    stock["unknown_cost_qty"] += remaining
            if b.cost_price is None and len(missing) < MAX_MISSING_BATCHES:
                missing.append(
                    {
                        "batch_id": str(b.id),
                        "product_id": str(b.product_id),
                        "product_name": pname,
                        "emoji": emoji,
                        "quantity": int(b.quantity or 0),
                        "remaining": remaining,
                        "sold_in_period": sold_batches.get(str(b.id), 0),
                        "sale_price": _r(_num(b.sale_price)),
                        "created_at": b.created_at.isoformat() if b.created_at else None,
                    }
                )

        # ------------------------------------------------------ jami --
        revenue = sum(r["revenue"] for r in products.values())
        covered = sum(r["revenue_covered"] for r in products.values())
        cost = sum(r["cost"] for r in products.values())
        cost_est = sum(r["cost_estimated"] for r in products.values())
        gross = covered - cost
        net = gross - writeoff["cost"] - shortage["cost"] + surplus["cost"]

        rows_out = []
        for r in products.values():
            profit = r["revenue_covered"] - r["cost"]
            losses = r["writeoff_cost"] + r["shortage_cost"] - r["surplus_cost"]
            if r["sold_qty"] == 0:
                status = "none"
            elif r["uncovered_qty"] == r["sold_qty"]:
                status = "unknown"
            elif r["uncovered_qty"] > 0:
                status = "partial"
            elif r["estimated_qty"] > 0:
                status = "estimated"
            else:
                status = "exact"
            rows_out.append(
                {
                    **{k: (_r(v) if isinstance(v, float) else v) for k, v in r.items()},
                    "name": r["name"] or "—",
                    "profit": _r(profit),
                    "margin": _r(profit / r["revenue_covered"] * 100) if r["revenue_covered"] > 0 else None,
                    "losses": _r(losses),
                    "net": _r(profit - losses),
                    "cost_status": status,
                }
            )
        rows_out.sort(key=lambda r: (-r["net"], r["name"]))

        def _bucket(b: dict) -> dict:
            return {**b, "cost": _r(b["cost"]), "sale_value": _r(b["sale_value"])}

        return {
            "date_from": str(date_from) if date_from else None,
            "date_to": str(date_to) if date_to else None,
            "sales": {
                "count": len(sales),
                "items_qty": items_qty,
                "revenue": _r(revenue),
                "unpaid": _r(sum(sales.values())),
            },
            "revenue_covered": _r(covered),
            "cost": {"total": _r(cost), "estimated": _r(cost_est)},
            "gross_profit": _r(gross),
            "margin": _r(gross / covered * 100) if covered > 0 else None,
            "uncovered": {
                "revenue": _r(revenue - covered),
                "qty": sum(r["uncovered_qty"] for r in products.values()),
                "products": sum(1 for r in products.values() if r["uncovered_qty"] > 0),
            },
            "below_cost": {
                "qty": sum(r["below_cost_qty"] for r in products.values()),
                "loss": _r(sum(r["below_cost_loss"] for r in products.values())),
                "products": len(below_products),
            },
            "writeoff": _bucket(writeoff),
            "shortage": _bucket(shortage),
            "surplus": _bucket(surplus),
            "losses": _r(writeoff["cost"] + shortage["cost"]),
            "net_profit": _r(net),
            "stock": {
                **{k: (_r(v) if isinstance(v, float) else v) for k, v in stock.items()},
                "potential_profit": _r(stock["covered_sale_value"] - stock["cost_value"]),
            },
            "products": rows_out,
            "missing_cost_batches": missing,
            "can_edit_cost": can_edit_cost,
        }


__all__ = ["MAX_MISSING_BATCHES", "ShopProfitService"]
