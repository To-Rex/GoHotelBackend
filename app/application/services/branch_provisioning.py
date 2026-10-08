"""Yangi filial — asosiy filialdan sozlama va kataloglar bilan boshlanadi.

Filiallar to'liq ajratilgan: har filialning o'z sozlamalari, xona turlari,
xizmat narxlari, qulayliklari, cheklist shablonlari, hisob rejasi va
do'kon mahsulotlari bor. Avval yangi filial bularning hammasini mehmonxona
bilan bo'lishardi — darhol bron qilish mumkin edi. Shu imkoniyat saqlanishi
uchun yangi filialga asosiy filialdagilar NUSXALANADI (zaxirasiz, faqat
ta'riflar); keyin har filial o'zinikini mustaqil o'zgartiradi.

Filialni o'chirish (`deletable_branch_data` / `drop_branch_catalogs`):
faqat shu nusxalar bo'lsa — ular bilan birga o'chadi; filialda ish
ma'lumoti (bron, mehmon, to'lov, xodim...) bo'lsa — o'chirilmaydi.
"""
from __future__ import annotations

import json
import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Nusxalanadigan kataloglar — tartib muhim (ota jadval avval)
CATALOG_TABLES = (
    "room_types",
    "hotel_room_types",
    "hotel_amenities",
    "hotel_services",
    "checklist_templates",
    "ledgers",
    "shop_products",
)


async def _columns(session: AsyncSession, table: str) -> list[str]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :t "
                    "ORDER BY ordinal_position"
                ),
                {"t": table},
            )
        ).scalars()
    )


async def _copy(
    session: AsyncSession,
    table: str,
    source: UUID,
    target: UUID,
    remap: dict[str, dict] | None = None,
    reset: dict | None = None,
) -> dict:
    """`table` ning `source` filialdagi qatorlarini `target` ga nusxalaydi.

    Qaytadi: eski id -> yangi id (id ustuni bo'lsa).
    """
    cols = await _columns(session, table)
    rows = (
        await session.execute(
            text(f'SELECT * FROM "{table}" WHERE branch_id = :b'), {"b": source}
        )
    ).mappings().all()
    has_id = "id" in cols
    mapping: dict = {}
    if has_id:
        mapping = {row["id"]: uuid.uuid4() for row in rows}
    remap = remap or {}
    reset = reset or {}
    payload = []
    for row in rows:
        values = dict(row)
        values["branch_id"] = target
        if has_id:
            values["id"] = mapping[row["id"]]
        for column, ids in remap.items():
            if values.get(column) in ids:
                values[column] = ids[values[column]]
        values.update(reset)
        # JSON ustunlar (masalan, xona turi qulayliklari) matn sifatida
        for key, value in values.items():
            if isinstance(value, (dict, list)):
                values[key] = json.dumps(value, ensure_ascii=False)
        payload.append(values)
    if payload:
        names = ", ".join(f'"{c}"' for c in cols)
        params = ", ".join(f":{c}" for c in cols)
        await session.execute(text(f'INSERT INTO "{table}" ({names}) VALUES ({params})'), payload)
    return mapping


async def provision_branch(session: AsyncSession, branch) -> None:
    """Yangi filialga asosiy filialning sozlamalari va kataloglari.

    Mehmonxonaning birinchi filiali bo'lsa — faqat mehmonxona sozlamalari.
    """
    from app.infrastructure.database.models.hotel import Hotel

    await session.flush()
    source = (
        await session.execute(
            text(
                "SELECT id, settings FROM branches WHERE hotel_id = :h AND id <> :b "
                "ORDER BY is_main_branch DESC NULLS LAST, created_at, id LIMIT 1"
            ),
            {"h": branch.hotel_id, "b": branch.id},
        )
    ).first()
    if branch.settings is None:
        if source is not None and source[1] is not None:
            branch.settings = dict(source[1])
        else:
            hotel = await session.get(Hotel, branch.hotel_id)
            branch.settings = dict((hotel.settings if hotel else None) or {})
    if source is None:
        return
    src = source[0]
    room_types = await _copy(session, "room_types", src, branch.id)
    await _copy(session, "hotel_room_types", src, branch.id, remap={"room_type_id": room_types})
    await _copy(session, "hotel_amenities", src, branch.id)
    await _copy(session, "hotel_services", src, branch.id)
    await _copy(session, "checklist_templates", src, branch.id)
    # Hisob rejasi: avval ota schyotlar bo'sh, keyin bog'lanish tiklanadi
    ledgers = await _copy(session, "ledgers", src, branch.id, reset={"parent_id": None})
    parents = (
        await session.execute(
            text("SELECT id, parent_id FROM ledgers WHERE branch_id = :b AND parent_id IS NOT NULL"),
            {"b": src},
        )
    ).all()
    for old_id, old_parent in parents:
        if old_id in ledgers and old_parent in ledgers:
            await session.execute(
                text("UPDATE ledgers SET parent_id = :p WHERE id = :i"),
                {"p": ledgers[old_parent], "i": ledgers[old_id]},
            )
    await _copy(session, "shop_products", src, branch.id)


async def branch_usage(session: AsyncSession, branch_id: UUID) -> dict[str, int]:
    """Filialdagi ish ma'lumoti (kataloglardan tashqari) — jadval: qatorlar.

    Xodimlar ham hisoblanadi: ularni avval boshqa filialga o'tkazish kerak.
    """
    tables = (
        await session.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND column_name = 'branch_id' "
                "AND table_name <> 'branches'"
            )
        )
    ).scalars().all()
    usage: dict[str, int] = {}
    users = (
        await session.execute(
            text("SELECT count(*) FROM users WHERE branch_id = :b AND is_deleted = false"),
            {"b": branch_id},
        )
    ).scalar()
    if users:
        usage["users"] = int(users)
    for table in sorted(tables):
        if table == "users":
            continue
        if table in CATALOG_TABLES:
            continue
        n = (
            await session.execute(
                text(f'SELECT count(*) FROM "{table}" WHERE branch_id = :b'), {"b": branch_id}
            )
        ).scalar()
        if n:
            usage[table] = int(n)
    return usage


async def drop_branch_catalogs(session: AsyncSession, branch_id: UUID) -> None:
    """Ish ma'lumoti yo'q filialning katalog nusxalarini o'chiradi."""
    await session.execute(text("DELETE FROM hotel_room_types WHERE branch_id = :b"), {"b": branch_id})
    await session.execute(text("DELETE FROM hotel_amenities WHERE branch_id = :b"), {"b": branch_id})
    await session.execute(text("DELETE FROM hotel_services WHERE branch_id = :b"), {"b": branch_id})
    await session.execute(text("DELETE FROM checklist_templates WHERE branch_id = :b"), {"b": branch_id})
    await session.execute(text("UPDATE ledgers SET parent_id = NULL WHERE branch_id = :b"), {"b": branch_id})
    await session.execute(text("DELETE FROM ledgers WHERE branch_id = :b"), {"b": branch_id})
    await session.execute(text("DELETE FROM shop_products WHERE branch_id = :b"), {"b": branch_id})
    await session.execute(text("DELETE FROM room_types WHERE branch_id = :b"), {"b": branch_id})
