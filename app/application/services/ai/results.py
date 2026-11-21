"""API natijasini AI uchun ixchamlash va maxfiy maydonlarni yashirish.

Token tejash: model katta ro'yxatni to'liq ko'rmaydi — faqat qatorlar
soni, ustunlar va bir nechta namuna qator. To'liq ma'lumot serverda
`result_id` ostida qoladi; jadval, grafik va Excel ana shu to'liq
ma'lumotdan SERVERDA quriladi (model raqamlarni qayta yozmaydi — xato
ham, token ham kam).

Maxfiylik: parol, token va kalitlar har doim; pasport, PINFL, karta va
tug'ilgan sana — sozlamada yoqilgan bo'lsa (standart) — "***".
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

#: Har doim yashiriladigan maydonlar
SECRET_KEYS = re.compile(r"(password|passwd|token|secret|api_key|apikey|refresh|hash|fcm|otp)", re.I)
#: Sozlamaga qarab yashiriladiganlar (shaxsiy ma'lumot)
PERSONAL_KEYS = re.compile(
    r"(passport|document_number|doc_number|document_series|pinfl|jshshir|birth|card_number|cvv|mrz|"
    r"face|embedding|photo_url|image_url)",
    re.I,
)

MAX_STORED_ROWS = 5000
SAMPLE_ROWS = 12
MAX_COLUMNS_FOR_MODEL = 18
MAX_TEXT = 160
MAX_CHARS = 6000

#: Ustun tartibida birinchi turadiganlar (namuna qatorlar ko'proq ma'noli bo'lsin)
PRIORITY = (
    "id", "number", "reservation_number", "invoice_number", "payment_number", "room_number", "name",
    "title", "guest_name", "first_name", "last_name", "status", "date", "check_in_date",
    "check_out_date", "amount", "total", "total_amount", "paid_amount", "remaining", "created_at",
)


def mask(value: Any, personal: bool = True, _key: str = "") -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            if SECRET_KEYS.search(key) or (personal and PERSONAL_KEYS.search(key)):
                out[k] = "***" if v not in (None, "", [], {}) else v
            else:
                out[k] = mask(v, personal, key)
        return out
    if isinstance(value, list):
        return [mask(v, personal, _key) for v in value]
    return value


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def flatten_row(row: Any) -> dict:
    """Ichma-ich lug'atni bir darajaga: {"guest": {"name": ..}} -> "guest.name".
    Ro'yxatlar — qisqa matn (oddiy qiymatlar) yoki "N ta"."""
    if not isinstance(row, dict):
        return {"value": row}
    out: dict = {}
    for k, v in row.items():
        if _scalar(v):
            out[k] = v
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if _scalar(v2):
                    out[f"{k}.{k2}"] = v2
        elif isinstance(v, list):
            if all(_scalar(x) for x in v):
                out[k] = ", ".join(str(x) for x in v[:20] if x is not None)
            else:
                out[k] = f"{len(v)} ta"
    return out


def extract_rows(data: Any) -> tuple[list | None, dict]:
    """Ro'yxat qismini topadi: [...] yoki {"items": [...], ...}; qolgani — meta."""
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict):
        for key in ("items", "rows", "data", "results", "products", "sales", "tasks", "reservations"):
            if isinstance(data.get(key), list):
                meta = {k: v for k, v in data.items() if k != key}
                return data[key], meta
    return None, {}


def _trim(v: Any) -> Any:
    if isinstance(v, str) and len(v) > MAX_TEXT:
        return v[:MAX_TEXT] + "…"
    if isinstance(v, float):
        return round(v, 2)
    return v


def _columns(rows: list[dict]) -> list[str]:
    seen: list[str] = []
    for r in rows[:60]:
        for k in r:
            if k not in seen:
                seen.append(k)
    pri = [c for c in PRIORITY if c in seen]
    rest = [c for c in seen if c not in pri]
    return pri + rest


def _compact_value(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        return {k: _compact_value(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        items = [_compact_value(v, depth + 1) for v in value[:SAMPLE_ROWS]]
        if len(value) > SAMPLE_ROWS:
            items.append(f"... yana {len(value) - SAMPLE_ROWS} ta")
        return items
    return _trim(value)


@dataclass
class StoredResult:
    id: str
    source: str
    rows: list[dict]
    columns: list[str]
    meta: dict = field(default_factory=dict)


class ResultStore:
    """Bitta suhbat navbati davomidagi natijalar (xotirada)."""

    def __init__(self) -> None:
        self._items: dict[str, StoredResult] = {}
        self._n = 0

    def add(self, source: str, rows: list[dict], meta: dict) -> StoredResult:
        self._n += 1
        rid = f"r{self._n}"
        flat = [flatten_row(r) for r in rows[:MAX_STORED_ROWS]]
        item = StoredResult(rid, source, flat, _columns(flat), meta)
        self._items[rid] = item
        return item

    def get(self, rid: str | None) -> StoredResult | None:
        return self._items.get(rid or "")

    def ids(self) -> list[str]:
        return list(self._items)


def for_model(source: str, data: Any, store: ResultStore, personal_mask: bool) -> str:
    """Natijani modelga beriladigan ixcham JSON matnga aylantiradi."""
    data = mask(data, personal_mask)
    rows, meta = extract_rows(data)
    if rows is not None and (len(rows) > 3 or (rows and isinstance(rows[0], dict))):
        dict_rows = [r if isinstance(r, dict) else {"value": r} for r in rows]
        item = store.add(source, dict_rows, meta)
        cols = item.columns[:MAX_COLUMNS_FOR_MODEL]
        sample = [{c: _trim(r.get(c)) for c in cols if r.get(c) not in (None, "")} for r in item.rows[:SAMPLE_ROWS]]
        payload = {
            "result_id": item.id,
            "total_rows": len(rows),
            "stored_rows": len(item.rows),
            "columns": item.columns[:60],
            "sample_rows": sample,
            "note": "Full rows are stored on the server. Use result_id with show_table / show_chart / "
                    "export_excel instead of copying rows into your answer.",
        }
        if meta:
            payload["meta"] = _compact_value(meta)
    else:
        payload = {"data": _compact_value(data)}
        # Kichik lug'at ham jadval/grafik uchun saqlanadi (masalan summary)
        if isinstance(data, dict):
            item = store.add(source, [data], {})
            payload["result_id"] = item.id
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + ' …"truncated"}'
    return text


def new_block_id() -> str:
    return uuid.uuid4().hex[:12]


__all__ = [
    "ResultStore", "StoredResult", "extract_rows", "flatten_row", "for_model", "mask", "new_block_id",
]
