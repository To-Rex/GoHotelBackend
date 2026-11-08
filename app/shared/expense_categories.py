"""Xarajat toifalari: standart ro'yxat, nom qoidalari va birinchi to'ldirish.

Xarajat (`expenses.category`) toifani NOM sifatida saqlaydi — hisobotlar
(moliya yig'indisi, mobil ilova) shu nom bo'yicha guruhlaydi va eski
yozuvlar o'zgarmasdan ishlayveradi. Toifalar jadvali (`expense_categories`)
esa mehmonxonaning tanlash ro'yxati: nom, belgi, rang, tartib.

Bu modul ikki joydan ishlatiladi:

* Alembic migratsiyasi — mavjud mehmonxonalarga standart toifalar va
  ilgari qo'lda yozilgan toifa nomlarini bir marta kiritadi
  (`seed_expense_categories`). SQLAlchemy Core bilan yozilgan, dialektga
  bog'liq emas — SQLite'da sinaladi, PostgreSQL'da ishlaydi.
* Servis — yangi mehmonxona ro'yxatni birinchi ochganda standart toifalar
  shu yerdan olinadi (`DEFAULT_CATEGORIES`).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.engine import Connection

#: `expenses.category` ustuni 50 belgi — toifa nomi ham shundan oshmaydi
MAX_NAME_LENGTH = 50
#: Toifasiz xarajatlar hisobotda shu nomga yig'iladi (finance_summary_service)
OTHER_NAME = "Boshqa"

#: Standart toifalar: (nom, belgi, rang). Tartib — ro'yxatdagi tartib.
#: Nomlar o'zbekcha saqlanadi (mijoz ekranda tarjima qiladi).
DEFAULT_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("Ish haqi", "wallet", "emerald"),
    ("Elektr energiyasi", "zap", "amber"),
    ("Gaz", "flame", "orange"),
    ("Suv", "droplets", "sky"),
    ("Internet va aloqa", "wifi", "indigo"),
    ("Oziq-ovqat", "utensils", "rose"),
    ("Xo'jalik buyumlari", "shopping-basket", "lime"),
    ("Kir yuvish", "washing-machine", "teal"),
    ("Ta'mirlash", "wrench", "slate"),
    ("Transport", "car", "violet"),
    ("Soliqlar", "landmark", "red"),
    ("Reklama", "megaphone", "fuchsia"),
    (OTHER_NAME, "ellipsis", "gray"),
)

#: Mijoz biladigan belgilar (lucide nomlari). Boshqasi qabul qilinmaydi —
#: aks holda ekranda bo'sh joy chiqardi.
ICONS: tuple[str, ...] = (
    "wallet", "zap", "flame", "droplets", "wifi", "utensils", "shopping-basket",
    "washing-machine", "wrench", "car", "landmark", "megaphone", "ellipsis",
    "tag", "hammer", "paintbrush", "truck", "phone", "shield-check", "users",
    "gift", "package", "bed", "sparkles", "receipt", "banknote", "building-2",
    "fuel", "lightbulb", "thermometer", "printer", "monitor", "scissors",
    "leaf", "bath", "coins", "percent", "heart-handshake", "cable",
    "briefcase-business", "stethoscope", "bus", "shirt",
)

#: Ranglar — mijozdagi palitra kalitlari
COLORS: tuple[str, ...] = (
    "gray", "slate", "red", "orange", "amber", "lime", "emerald", "teal",
    "sky", "indigo", "violet", "fuchsia", "rose",
)

#: Qo'lda yozilgan (ro'yxatda bo'lmagan) toifa uchun belgi va rang
IMPORTED_ICON = "tag"
IMPORTED_COLOR = "gray"

_SPACES = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """Chetki va ketma-ket bo'shliqlarni yig'ishtiradi: "  Ish   haqi " → "Ish haqi"."""
    return _SPACES.sub(" ", (name or "")).strip()


def name_key(name: str | None) -> str:
    """Taqqoslash kaliti: bo'shliqlar yig'ishtirilgan, harf kattaligi
    e'tiborga olinmaydi ("ish haqi" va "Ish haqi" — bitta toifa)."""
    return normalize_name(name).casefold()


# --- Core jadvallar (migratsiya uchun, ORM'siz) ------------------------------

# `sa.Uuid` — PostgreSQL'da uuid, SQLite'da 32 belgili matn: ikkala
# dialektda ham Python UUID qiymati to'g'ri bog'lanadi
HOTELS = sa.table("hotels", sa.column("id", sa.Uuid))

EXPENSES = sa.table(
    "expenses",
    sa.column("hotel_id", sa.Uuid),
    sa.column("category", sa.String),
)

EXPENSE_CATEGORIES = sa.table(
    "expense_categories",
    sa.column("id", sa.Uuid),
    sa.column("hotel_id", sa.Uuid),
    sa.column("name", sa.String),
    sa.column("icon", sa.String),
    sa.column("color", sa.String),
    sa.column("sort_order", sa.Integer),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def default_rows(hotel_id, start: int = 0) -> list[dict]:
    """Bir mehmonxona uchun standart toifa qatorlari (id'siz)."""
    return [
        {
            "hotel_id": hotel_id,
            "name": name,
            "icon": icon,
            "color": color,
            "sort_order": start + position,
            "is_active": True,
        }
        for position, (name, icon, color) in enumerate(DEFAULT_CATEGORIES)
    ]


def seed_expense_categories(conn: Connection) -> dict[str, int]:
    """Mavjud mehmonxonalarga toifalarni bir marta kiritadi.

    * Toifasi hali YO'Q mehmonxona standart ro'yxatni oladi.
    * Xarajatlarda ilgari qo'lda yozilgan har bir toifa nomi ham ro'yxatga
      kiradi (bo'shlari va standart nomga teng kelganlari emas) — hech bir
      eski xarajat "noma'lum toifa" bo'lib qolmaydi.
    * Toifasi BOR mehmonxonaga standart ro'yxat qayta qo'shilmaydi —
      administrator o'chirgan toifa qaytib kelmasin; faqat ro'yxatda yo'q
      ishlatilgan nomlar qo'shiladi. Shu sabab qayta ishga tushsa hech
      narsa o'zgarmaydi.

    Qaytaradi: {"hotels": toifa olgan mehmonxonalar, "defaults": standart
    qatorlar soni, "imported": xarajatlardan olingan nomlar soni}.
    """
    hotel_ids = [row[0] for row in conn.execute(sa.select(HOTELS.c.id)).all()]

    existing: dict = {}
    max_order: dict = {}
    for hotel_id, name, order in conn.execute(
        sa.select(
            EXPENSE_CATEGORIES.c.hotel_id,
            EXPENSE_CATEGORIES.c.name,
            EXPENSE_CATEGORIES.c.sort_order,
        )
    ).all():
        existing.setdefault(hotel_id, set()).add(name_key(name))
        max_order[hotel_id] = max(max_order.get(hotel_id, -1), int(order or 0))

    used: dict = {}
    for hotel_id, name in conn.execute(
        sa.select(EXPENSES.c.hotel_id, EXPENSES.c.category)
        .where(EXPENSES.c.category.is_not(None))
        .distinct()
    ).all():
        clean = normalize_name(name)
        if not clean:
            continue
        # Bir xil nomning har xil yozilishi ("gaz", "Gaz ") — bittasi,
        # birinchi uchragani saqlanadi
        used.setdefault(hotel_id, {}).setdefault(name_key(clean), clean[:MAX_NAME_LENGTH])

    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    touched: set = set()
    defaults_added = imported = 0

    for hotel_id in hotel_ids:
        keys = set(existing.get(hotel_id, set()))
        order = max_order.get(hotel_id, -1) + 1
        if not keys:
            for row in default_rows(hotel_id, start=order):
                rows.append(row)
                keys.add(name_key(row["name"]))
            order += len(DEFAULT_CATEGORIES)
            defaults_added += len(DEFAULT_CATEGORIES)
            touched.add(hotel_id)
        for key, clean in sorted(used.get(hotel_id, {}).items(), key=lambda kv: kv[1]):
            if key in keys:
                continue
            rows.append(
                {
                    "hotel_id": hotel_id,
                    "name": clean,
                    "icon": IMPORTED_ICON,
                    "color": IMPORTED_COLOR,
                    "sort_order": order,
                    "is_active": True,
                }
            )
            keys.add(key)
            order += 1
            imported += 1
            touched.add(hotel_id)

    for row in rows:
        row["id"] = uuid.uuid4()
        row["created_at"] = now
        row["updated_at"] = now

    for start in range(0, len(rows), 500):
        conn.execute(sa.insert(EXPENSE_CATEGORIES), rows[start : start + 500])

    return {"hotels": len(touched), "defaults": defaults_added, "imported": imported}
