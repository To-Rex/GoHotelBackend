"""Ko'rsatiladigan bloklar va serverdagi tahlil.

Model katta ma'lumotni o'zi qayta yozmaydi: `result_id` beradi, server
esa saqlangan TO'LIQ qatorlardan hisoblaydi (guruhlash, yig'indi, o'rtacha,
kun/hafta/oy bo'yicha) va jadval/grafik blokini quradi. Raqamlar aniq,
token esa kam.

Blok turlari (frontend: src/features/assistant/blocks):
  table  — {columns:[{key,label,kind}], rows, total, title}
  chart  — {chart: bar|line|area|pie, title, unit, x:{key,label}, series:[{key,label}], data}
  stats  — {items:[{label, value, unit, hint, tone}]}
  file   — {file_id, name, size, rows}
  nav    — {path, label, note}
  replies— {options:[...]}
  action — {action:{...}} (actions_service.serialize)
  plan   — {plan:{...}}
"""
from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.application.services.ai.results import StoredResult, new_block_id

MAX_TABLE_ROWS = 200
MAX_CHART_POINTS = 60
MAX_PIE_SLICES = 7
MAX_SERIES = 6

COMMON_LABELS = {
    "id": "ID", "number": "Raqam", "reservation_number": "Bron raqami", "invoice_number": "Faktura",
    "payment_number": "To'lov raqami", "status": "Holat", "amount": "Summa", "total": "Jami",
    "total_amount": "Jami summa", "paid_amount": "To'langan", "remaining": "Qoldiq",
    "remaining_amount": "Qoldiq", "balance": "Balans", "created_at": "Yaratilgan", "updated_at": "Yangilangan",
    "check_in_date": "Kelish", "check_out_date": "Ketish", "guest_name": "Mehmon", "room_number": "Xona",
    "name": "Nomi", "title": "Nomi", "date": "Sana", "day": "Kun", "month": "Oy", "method": "Usul",
    "payment_method": "To'lov usuli", "category": "Toifa", "description": "Izoh", "note": "Izoh",
    "quantity": "Soni", "price": "Narx", "phone": "Telefon", "first_name": "Ism", "last_name": "Familiya",
    "count": "Soni", "sum": "Jami", "nights": "Tunlar", "room_type": "Xona turi", "floor": "Qavat",
    "created_by_name": "Kim yaratgan", "staff_name": "Xodim", "user_name": "Xodim", "revenue": "Tushum",
    "profit": "Foyda", "cost": "Tannarx", "expense": "Xarajat", "expenses": "Xarajatlar",
    "expense_date": "Sana", "payment_date": "To'lov sanasi", "sale_date": "Sana", "service_date": "Sana",
    "scheduled_date": "Rejalangan sana", "penalty_date": "Sana", "due_date": "Muddat", "issue_date": "Sana",
    "unit_price": "Narx", "total_price": "Jami", "payment_status": "To'lov holati", "task_type": "Vazifa turi",
    "priority": "Muhimlik", "current_status": "Holat", "base_price": "Narx", "capacity": "Sig'im",
}

#: Bazadagi kodlar — hisobotda (Excel) tushunarli nom bilan
VALUE_LABELS = {
    "AVAILABLE": "Bo'sh", "OCCUPIED": "Band", "RESERVED": "Bron qilingan", "CLEANING": "Tozalanmoqda",
    "MAINTENANCE": "Ta'mirda", "OUT_OF_ORDER": "Ishlamaydi", "CONFIRMED": "Tasdiqlangan", "PENDING": "Kutilmoqda",
    "CHECKED_IN": "Joylashgan", "CHECKED_OUT": "Chiqib ketgan", "CANCELLED": "Bekor qilingan", "NO_SHOW": "Kelmadi",
    "PAID": "To'langan", "PARTIAL": "Qisman to'langan", "PARTIALLY_PAID": "Qisman to'langan", "UNPAID": "To'lanmagan",
    "REFUNDED": "Qaytarilgan", "VOID": "Bekor qilingan", "OPEN": "Ochiq", "IN_PROGRESS": "Jarayonda",
    "COMPLETED": "Bajarildi", "RESOLVED": "Hal qilindi", "CASH": "Naqd", "CARD": "Karta", "ONLINE": "Onlayn",
    "BANK_TRANSFER": "Bank o'tkazmasi", "TRANSFER": "O'tkazma", "DAILY": "Kunlik", "HOURLY": "Soatlik",
    "ACTIVE": "Faol", "INACTIVE": "Nofaol",
}


def value_label(v):
    return VALUE_LABELS.get(v, v) if isinstance(v, str) else v


_MONEY_HINT = re.compile(
    r"(amount|price|total|sum|paid|remaining|balance|cost|revenue|debt|profit|income|expense|cash|summa|narx)",
    re.I,
)
_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def label_for(key: str, labels: dict | None = None) -> str:
    if labels and labels.get(key):
        return str(labels[key])[:60]
    if key in COMMON_LABELS:
        return COMMON_LABELS[key]
    tail = key.rsplit(".", 1)[-1]
    if tail in COMMON_LABELS and "." in key:
        return f"{key.split('.', 1)[0].replace('_', ' ').capitalize()}: {COMMON_LABELS[tail].lower()}"
    return key.replace(".", " ").replace("_", " ").strip().capitalize()


def to_number(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(" ", "").replace(" ", "")
        if not s or len(s) > 30:
            return None
        try:
            return float(Decimal(s))
        except (InvalidOperation, ValueError):
            return None
    return None


def _tz_offset() -> timedelta:
    from app.core.config import settings

    return timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)


def to_local_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        moment = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return (moment.astimezone(timezone.utc) + _tz_offset()).date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and _DATE_RX.match(v):
        s = v.strip()
        if len(s) == 10:
            try:
                return date.fromisoformat(s)
            except ValueError:
                return None
        try:
            moment = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(s[:10])
            except ValueError:
                return None
        if moment.tzinfo is None:
            # Zonasiz vaqt belgisi — API UTC qaytaradi
            moment = moment.replace(tzinfo=timezone.utc)
        return (moment.astimezone(timezone.utc) + _tz_offset()).date()
    return None


def _local_hour(v: Any) -> int | None:
    if isinstance(v, str) and len(v) > 10 and _DATE_RX.match(v):
        try:
            moment = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (moment.astimezone(timezone.utc) + _tz_offset()).hour
    return None


_WEEKDAYS = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")


def _bucket(value: Any, unit: str) -> Any:
    if unit == "hour":
        h = _local_hour(value)
        return f"{h:02d}:00" if h is not None else None
    d = to_local_date(value)
    if d is None:
        return None
    if unit == "day":
        return d.isoformat()
    if unit == "week":
        start = d - timedelta(days=d.weekday())
        return start.isoformat()
    if unit == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if unit == "year":
        return f"{d.year:04d}"
    if unit == "weekday":
        return f"{d.weekday() + 1}-{_WEEKDAYS[d.weekday()]}"
    return d.isoformat()


def column_kind(rows: list[dict], key: str) -> str:
    seen = nums = dates = 0
    for r in rows[:80]:
        v = r.get(key)
        if v in (None, ""):
            continue
        seen += 1
        if to_number(v) is not None and not (isinstance(v, str) and _DATE_RX.match(v)):
            nums += 1
        elif isinstance(v, str) and _DATE_RX.match(v):
            dates += 1
    if not seen:
        return "text"
    if nums / seen >= 0.8:
        if key.endswith("id") or key in ("number", "room_number", "phone"):
            return "text"
        return "money" if _MONEY_HINT.search(key) else "number"
    if dates / seen >= 0.8:
        return "datetime" if any(
            len(str(r.get(key) or "")) > 10 for r in rows[:20]
        ) else "date"
    return "text"


# ------------------------------------------------------------- filtr --

_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "empty", "not_empty")


def _match(row: dict, flt: dict) -> bool:
    field = str(flt.get("field") or "")
    op = str(flt.get("op") or "eq")
    target = flt.get("value")
    v = row.get(field)
    if op == "empty":
        return v in (None, "")
    if op == "not_empty":
        return v not in (None, "")
    if op == "contains":
        return str(target or "").lower() in str(v or "").lower()
    if op == "in":
        options = target if isinstance(target, list) else [target]
        return str(v) in {str(o) for o in options}
    if op in ("gt", "gte", "lt", "lte"):
        a, b = to_number(v), to_number(target)
        if a is None or b is None:
            # Sana solishtirish (ISO matn tartibi to'g'ri)
            if isinstance(v, str) and isinstance(target, str):
                a, b = v[: len(target)], target  # type: ignore[assignment]
            else:
                return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "ne":
        return str(v).lower() != str(target).lower()
    return str(v).lower() == str(target).lower() if not isinstance(target, (int, float)) else to_number(v) == float(target)


def apply_filters(rows: list[dict], filters: list[dict] | None) -> list[dict]:
    if not filters:
        return rows
    return [r for r in rows if all(_match(r, f) for f in filters if isinstance(f, dict))]


# ------------------------------------------------------------- tahlil --


class AnalyzeError(ValueError):
    pass


def analyze(
    item: StoredResult,
    *,
    group_by: list[str] | None = None,
    metrics: list[dict] | None = None,
    filters: list[dict] | None = None,
    sort: str | None = None,
    desc: bool = True,
    limit: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Guruhlash va yig'indi. group_by: "field" yoki "field:day|week|month|year|weekday|hour"."""
    rows = apply_filters(item.rows, filters)
    cols = set(item.columns)
    groups: list[tuple[str, str | None, str]] = []
    for g in group_by or []:
        field, _, unit = str(g).partition(":")
        if field not in cols:
            raise AnalyzeError(f"Unknown column '{field}'. Columns: {', '.join(item.columns[:40])}")
        alias = field if not unit else f"{field.rsplit('.', 1)[-1]}_{unit}"
        groups.append((field, unit or None, alias))
    mets: list[tuple[str | None, str, str]] = []
    for m in metrics or [{"op": "count"}]:
        op = str(m.get("op") or "count").lower()
        field = m.get("field")
        if op not in ("sum", "count", "avg", "min", "max", "count_distinct"):
            raise AnalyzeError(f"Unknown op '{op}'")
        if op != "count" and (not field or field not in cols):
            raise AnalyzeError(f"Metric '{op}' needs an existing column. Columns: {', '.join(item.columns[:40])}")
        alias = str(m.get("as") or (f"{op}_{field.rsplit('.', 1)[-1]}" if field else "count"))
        mets.append((field, op, alias))

    buckets: "OrderedDict[tuple, dict]" = OrderedDict()
    for r in rows:
        key_parts = []
        for field, unit, _alias in groups:
            v = r.get(field)
            key_parts.append(_bucket(v, unit) if unit else (v if v not in ("",) else None))
        key = tuple(key_parts)
        acc = buckets.get(key)
        if acc is None:
            acc = {"n": 0, "vals": {alias: [] for _f, _o, alias in mets}}
            buckets[key] = acc
        acc["n"] += 1
        for field, op, alias in mets:
            if op == "count":
                continue
            v = r.get(field)
            if op == "count_distinct":
                acc["vals"][alias].append(v)
            else:
                num = to_number(v)
                if num is not None:
                    acc["vals"][alias].append(num)

    out: list[dict] = []
    for key, acc in buckets.items():
        row: dict = {}
        for (_field, _unit, alias), part in zip(groups, key, strict=True):
            row[alias] = part if part is not None else "—"
        for _field, op, alias in mets:
            vals = acc["vals"][alias]
            if op == "count":
                row[alias] = acc["n"]
            elif op == "count_distinct":
                row[alias] = len({str(v) for v in vals if v not in (None, "")})
            elif not vals:
                row[alias] = 0 if op == "sum" else None
            elif op == "sum":
                row[alias] = round(sum(vals), 2)
            elif op == "avg":
                row[alias] = round(sum(vals) / len(vals), 2)
            elif op == "min":
                row[alias] = min(vals)
            else:
                row[alias] = max(vals)
        out.append(row)

    columns = [g[2] for g in groups] + [m[2] for m in mets]
    if sort and sort in columns:
        def _key(r):
            v = r.get(sort)
            n = to_number(v)
            return (0, n) if n is not None else (1, str(v))
        out.sort(key=_key, reverse=bool(desc))
    elif groups and any(g[1] for g in groups):
        # Vaqt bo'yicha guruh — xronologik tartib
        out.sort(key=lambda r: str(r.get(groups[0][2])))
    elif mets:
        first = mets[0][2]
        out.sort(key=lambda r: to_number(r.get(first)) or 0, reverse=True)
    if limit:
        out = out[: max(1, min(int(limit), 1000))]
    return out, columns


# ------------------------------------------------------------- bloklar --


def table_block(
    item: StoredResult,
    *,
    title: str | None = None,
    columns: list[str] | None = None,
    labels: dict | None = None,
    filters: list[dict] | None = None,
    sort: str | None = None,
    desc: bool = False,
    limit: int | None = None,
) -> dict:
    rows = apply_filters(item.rows, filters)
    cols = [c for c in (columns or []) if c in item.columns] or item.columns[:8]
    if sort and sort in item.columns:
        def _key(r):
            v = r.get(sort)
            n = to_number(v)
            return (0, n, "") if n is not None else (1, 0, str(v or ""))
        rows = sorted(rows, key=_key, reverse=bool(desc))
    cap = max(1, min(int(limit or MAX_TABLE_ROWS), MAX_TABLE_ROWS))
    shown = [{c: r.get(c) for c in cols} for r in rows[:cap]]
    return {
        "type": "table",
        "id": new_block_id(),
        "title": (title or "")[:120],
        "columns": [{"key": c, "label": label_for(c, labels), "kind": column_kind(rows, c)} for c in cols],
        "rows": shown,
        "total": len(rows),
        "result_id": item.id,
    }


def _short_label(v: Any) -> str:
    if v is None or v == "":
        return "—"
    s = str(v)
    d = to_local_date(s) if _DATE_RX.match(s) and len(s) > 10 else None
    if d is not None:
        return d.isoformat()
    return s[:40]


def chart_block(
    data_rows: list[dict],
    *,
    chart: str,
    x: str,
    y: list[str],
    title: str | None = None,
    unit: str | None = None,
    labels: dict | None = None,
    source: str | None = None,
) -> dict:
    chart = chart if chart in ("bar", "line", "area", "pie") else "bar"
    series = [k for k in y if k][:MAX_SERIES] or []
    if not series:
        raise AnalyzeError("y must name at least one numeric column")
    if chart == "pie":
        series = series[:1]
    points = []
    for r in data_rows:
        point = {"x": _short_label(r.get(x))}
        for k in series:
            n = to_number(r.get(k))
            point[k] = round(n, 2) if n is not None else 0
        points.append(point)
    if chart == "pie" and len(points) > MAX_PIE_SLICES:
        points.sort(key=lambda p: p[series[0]], reverse=True)
        head, tail = points[: MAX_PIE_SLICES - 1], points[MAX_PIE_SLICES - 1:]
        head.append({"x": "Boshqa", series[0]: round(sum(p[series[0]] for p in tail), 2)})
        points = head
    truncated = len(points) > MAX_CHART_POINTS
    if truncated:
        points = points[-MAX_CHART_POINTS:] if chart in ("line", "area") else points[:MAX_CHART_POINTS]
    return {
        "type": "chart",
        "id": new_block_id(),
        "chart": chart,
        "title": (title or "")[:120],
        "unit": (unit or "")[:20] or None,
        "x": {"key": "x", "label": label_for(x, labels)},
        "series": [{"key": k, "label": label_for(k, labels)} for k in series],
        "data": points,
        "truncated": truncated,
        "source": source,
    }


def stats_block(items: list[dict]) -> dict:
    clean = []
    for it in items[:8]:
        if not isinstance(it, dict) or not it.get("label"):
            continue
        tone = it.get("tone") if it.get("tone") in ("good", "bad", "warn", "neutral") else None
        clean.append(
            {
                "label": str(it.get("label"))[:60],
                "value": it.get("value") if isinstance(it.get("value"), (int, float)) else str(it.get("value") or "—")[:40],
                "unit": (str(it.get("unit")) if it.get("unit") else None),
                "hint": (str(it.get("hint"))[:80] if it.get("hint") else None),
                "tone": tone,
            }
        )
    return {"type": "stats", "id": new_block_id(), "items": clean}


def nav_block(path: str, label: str | None, note: str | None = None) -> dict:
    return {
        "type": "nav", "id": new_block_id(), "path": path,
        "label": (label or path)[:60], "note": (note or None) and str(note)[:160],
    }


def replies_block(options: list[str]) -> dict:
    opts = list(dict.fromkeys(str(o).strip()[:80] for o in options if str(o).strip()))[:4]
    return {"type": "replies", "id": new_block_id(), "options": opts}


def file_block(file_id: str, name: str, size: int, rows: int) -> dict:
    return {"type": "file", "id": new_block_id(), "file_id": file_id, "name": name, "size": size, "rows": rows}


__all__ = [
    "AnalyzeError", "analyze", "apply_filters", "chart_block", "column_kind", "file_block",
    "label_for", "nav_block", "replies_block", "stats_block", "table_block", "to_local_date", "to_number",
]
