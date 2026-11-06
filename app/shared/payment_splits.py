"""To'lov turini QISMAN tahrirlash: ajralgan qator va uning tarixi.

Qisman tahrirda (masalan 100 000 naqd → 50 000 naqd + 50 000 online)
summaning bir qismi YANGI `payments` qatori bo'ladi. Ilgari bu qatorga
`created_at` sifatida tahrir payti yozilardi. Oqibati: kecha olingan pulning
yarmi BUGUNGA o'tib qolardi — smena kassasi va shaxsiy hisobot (ular
`created_at` oynasi bilan ishlaydi) ajralgan qismni bugungi smenaga
qo'shardi, kechagi smenadan esa ayirardi; To'lovlar jadvalida qator bugungi
vaqt bilan turardi, "qarz to'lovi" belgisi va xona/jarima taqsimoti ham
shu vaqtdan hisoblanardi.

To'g'ri holat: pul QACHON olingan bo'lsa, ajralgan qism ham o'sha paytda
turadi. Tahrirning o'zi (kim, qachon, nimadan nimaga, qancha) vaqt
ustunida emas, `method_changes` tarixida saqlanadi:

    asl qator:      {"kind": "partial", old, new, amount,
                     "total_before", "total_after", "new_payment_number"}
    ajralgan qator: {"kind": "split_from", old, new, amount,
                     "from_payment_number", "from_total"}
    butun tahrir:   {"kind": "full", old, new, "total_before", "total_after"}

`repair_split_payments` — yangilanishgacha bo'lingan eski qatorlarni
tuzatadi (Alembic c1e3f5a7b9d2): vaqtini asl to'lovnikiga qaytaradi va
tarixiga "qaysi to'lovdan ajralgan" yozuvini qo'shadi. Mantiq SQLAlchemy
Core da, dialektga bog'liq emas — testlar SQLite da yuradi.
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

#: Butun summa boshqa turga o'tdi (summa o'zgarmaydi)
KIND_FULL = "full"
#: Summaning bir qismi yangi qatorga o'tdi — ASL qatordagi yozuv
KIND_PARTIAL = "partial"
#: Shu qator boshqa to'lovdan ajralgan — AJRALGAN qatordagi yozuv
KIND_SPLIT_FROM = "split_from"


def split_origin_entry(partial_entry: dict, from_payment_number: str | None) -> dict:
    """Ajralgan qatorning o'z tarixi uchun yozuv.

    Asl qatordagi `partial` yozuvidan tuziladi: kim, qachon, nimadan
    nimaga va qancha — bir xil; qo'shimcha: qaysi to'lovdan ajralgani va
    o'sha to'lov tahrirdan oldin qancha bo'lgani (`from_total`; eski
    yozuvlarda noma'lum — `None`).
    """
    return {
        "kind": KIND_SPLIT_FROM,
        "old": partial_entry.get("old"),
        "new": partial_entry.get("new"),
        "by": partial_entry.get("by"),
        "by_name": partial_entry.get("by_name"),
        "at": partial_entry.get("at"),
        "note": partial_entry.get("note"),
        "amount": partial_entry.get("amount"),
        "from_payment_number": from_payment_number,
        "from_total": partial_entry.get("total_before"),
        "new_payment_number": None,
    }


def is_split_row(method_changes) -> bool:
    """Shu to'lov boshqa to'lovdan ajralgan qatormi (tarixida `split_from`)."""
    return isinstance(method_changes, list) and any(
        isinstance(e, dict) and e.get("kind") == KIND_SPLIT_FROM for e in method_changes
    )


# ---------------------------------------------------------------- tuzatish --

_JSON = JSONB().with_variant(sa.JSON(), "sqlite")

PAYMENTS = sa.table(
    "payments",
    sa.column("id"),
    sa.column("hotel_id"),
    sa.column("payment_number", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("method_changes", _JSON),
)

_COLUMNS = (
    PAYMENTS.c.id,
    PAYMENTS.c.hotel_id,
    PAYMENTS.c.payment_number,
    PAYMENTS.c.created_at,
    PAYMENTS.c.method_changes,
)

#: `IN (...)` ro'yxati bo'laklab yuboriladi (parametrlar soni chegarasi)
_CHUNK = 500


def _as_utc(moment: datetime) -> datetime:
    """SQLite vaqt belgisini zonasiz qaytaradi — u UTC deb qabul qilinadi."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def repair_split_payments(conn: Connection) -> dict[str, int]:
    """Eski ajralgan qatorlarni tuzatadi; natija — nechta qator o'zgargani.

    Ajralgan qator ANIQ topiladi: asl to'lov tarixidagi `partial` yozuvi
    yangi qator raqamini saqlaydi (`new_payment_number`, mehmonxona ichida
    yagona). Har ajralgan qatorga:

    * `created_at` — asl to'lovniki (bo'lingan qator yana bo'lingan
      bo'lsa — zanjir boshidagi to'lovniki). Vaqt faqat ORQAGA suriladi;
    * tarixiga `split_from` yozuvi boshiga qo'shiladi (bo'lmasa).

    Boshqa hech narsa (summa, usul, sana, xodim) tegilmaydi. Idempotent:
    qayta ishga tushsa hech narsa o'zgarmaydi.
    """
    t = PAYMENTS
    parents = (
        conn.execute(sa.select(*_COLUMNS).where(t.c.method_changes.is_not(None)))
        .mappings()
        .all()
    )

    # (mehmonxona, ajralgan qator raqami) → (asl qator, partial yozuvi)
    links: dict[tuple, tuple] = {}
    for row in parents:
        changes = row["method_changes"]
        if not isinstance(changes, list):
            continue
        for entry in changes:
            if (
                isinstance(entry, dict)
                and entry.get("kind") == KIND_PARTIAL
                and entry.get("new_payment_number")
            ):
                links[(row["hotel_id"], str(entry["new_payment_number"]))] = (row, entry)

    counts = {"created_at": 0, "history": 0}
    if not links:
        return counts

    numbers = sorted({number for _, number in links})
    children: dict[tuple, dict] = {}
    for start in range(0, len(numbers), _CHUNK):
        chunk = numbers[start : start + _CHUNK]
        rows = conn.execute(
            sa.select(*_COLUMNS).where(t.c.payment_number.in_(chunk))
        ).mappings()
        for row in rows:
            key = (row["hotel_id"], row["payment_number"])
            if key in links:
                children[key] = row

    def root_time(key: tuple) -> datetime | None:
        # Zanjir: A → B (A dan ajralgan) → C (B dan ajralgan). Hammasi A
        # vaqtida turishi kerak; `seen` — buzuq ma'lumotda aylanib qolmaslik
        seen = {key}
        parent = links[key][0]
        while True:
            parent_key = (parent["hotel_id"], parent["payment_number"])
            if parent_key not in links or parent_key in seen:
                return parent["created_at"]
            seen.add(parent_key)
            parent = links[parent_key][0]

    for key, child in children.items():
        parent, entry = links[key]
        values: dict = {}

        target = root_time(key)
        current = child["created_at"]
        if target is not None and current is not None and _as_utc(current) > _as_utc(target):
            values["created_at"] = target

        changes = child["method_changes"]
        changes = changes if isinstance(changes, list) else []
        if not is_split_row(changes):
            values["method_changes"] = [
                split_origin_entry(entry, parent["payment_number"]),
                *changes,
            ]

        if not values:
            continue
        conn.execute(sa.update(t).where(t.c.id == child["id"]).values(**values))
        counts["created_at"] += 1 if "created_at" in values else 0
        counts["history"] += 1 if "method_changes" in values else 0
    return counts
