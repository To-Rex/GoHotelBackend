"""Mehmonxonani BUTUNLAY o'chirish — qaytarib bo'lmaydi.

Panel egasi so'raganda mehmonxonaga tegishli HAMMA ma'lumot o'chiriladi:
xodimlar, filiallar, xonalar, bronlar, to'lovlar, do'kon, kassa, vazifalar,
kameralar, bildirishnomalar, fayllar (MinIO ham) — va mehmonxona yozuvining
o'zi. Boshqa mehmonxonalarning ma'lumotiga TEGILMAYDI.

Qanday qilib:

* **Sxema bazaning o'zidan o'qiladi** (pg_catalog): qaysi jadvalda
  `hotel_id` bor va qaysi tashqi kalit qayerga qaraydi. Kelajakda yangi
  jadval qo'shilsa ham u avtomatik qamraladi — ro'yxatni qo'lda yuritish
  bir kun unutilib, "yetim" ma'lumot qoldirardi.
* **Egalik**: `hotel_id` = shu mehmonxona bo'lgan qatorlar; `hotel_id`
  ustuni yo'q jadvallarda — shu mehmonxona qatoriga qaraydigan qatorlar
  (bron xizmatlari, savdo qatorlari, sessiyalar...).
* **Umumiy mehmonlar.** Mehmonlar bazasi global: boshqa mehmonxona shu
  mehmonxonada ro'yxatga olingan mehmonni o'z bronida ishlatgan bo'lishi
  mumkin. Bunday mehmon O'CHIRILMAYDI — o'sha mehmonxonaga o'tkaziladi,
  aks holda boshqa mehmonxonaning broni buzilardi.
* **Tizim akkauntlari** (SUPER_ADMIN, CONFIGURATOR) shu mehmonxonaga
  yozilgan bo'lsa ham o'chirilmaydi — mehmonxonadan ajratiladi.
* **To'siqlar.** Boshqa mehmonxona qatori shu mehmonxona qatoriga
  RESTRICT/CASCADE bilan qarasa — o'chirish to'xtatiladi (avval nima
  to'sayotgani ko'rsatiladi). SET NULL bog'lanish shunchaki bo'shatiladi.
* Hammasi BITTA tranzaksiyada: xato bo'lsa hech narsa o'chmaydi. MinIO
  fayllari tranzaksiya muvaffaqiyatli yakunlangach o'chiriladi.

Xavfsizlik (`router.py`): faqat panel egasi, mehmonxona avval to'xtatilgan
(INACTIVE) bo'lishi, kodini qo'lda yozish va panel parolini qayta kiritish.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictException, NotFoundException, ValidationException

logger = logging.getLogger(__name__)

HOTELS = "hotels"
GUESTS = "guests"
USERS = "users"
SHARED_TEMP = "purge_shared_guests"
#: Tizim darajasidagi akkauntlar — mehmonxona bilan birga o'chirilmaydi
SYSTEM_USER_TYPES = ("SUPER_ADMIN", "CONFIGURATOR")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

#: FK `ON DELETE` kodlari (pg_constraint.confdeltype)
BLOCKING_RULES = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE"}
NULLING_RULES = {"n": "SET NULL", "d": "SET DEFAULT"}


def _rule(value) -> str:
    """`confdeltype` — bitta harf. Drayver uni bayt qaytarishi mumkin
    (asyncpg "char" turini `b'r'` qiladi): bunday holda to'siq tanilmay,
    CASCADE boshqa mehmonxona qatorlarini o'chirib yuborardi."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    value = str(value or "").strip()
    if len(value) != 1:
        raise ValidationException(f"Noma'lum ON DELETE qoidasi: {value!r}", "PURGE_SCHEMA")
    return value


def q(name: str) -> str:
    """Jadval/ustun nomi — faqat katalogdan keladi, baribir tekshiriladi."""
    if not _IDENT.match(name):
        raise ValidationException(f"Kutilmagan nom: {name!r}", "PURGE_SCHEMA")
    return f'"{name}"'


@dataclass(frozen=True)
class ForeignKey:
    child: str
    child_col: str
    parent: str
    parent_col: str
    rule: str


@dataclass
class Schema:
    tables: set[str]
    hotel_tables: set[str]
    fks: list[ForeignKey]
    by_child: dict[str, list[ForeignKey]] = field(default_factory=dict)
    by_parent: dict[str, list[ForeignKey]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for fk in self.fks:
            self.by_child.setdefault(fk.child, []).append(fk)
            self.by_parent.setdefault(fk.parent, []).append(fk)


def owned_predicates(schema: Schema) -> dict[str, str]:
    """Har jadval uchun "shu mehmonxonaga tegishli qator" sharti (`:h`).

    `hotel_id` bor jadval — o'sha ustun; yo'q jadval — egalik qilinadigan
    jadvalga qaraydigan har qanday tashqi kalit orqali. Hech qayerga
    bog'lanmagan jadval (global katalog) bu ro'yxatga kirmaydi.
    """
    memo: dict[str, str | None] = {}
    visiting: set[str] = set()

    def pred(table: str) -> str | None:
        if table in memo:
            return memo[table]
        if table == HOTELS:
            memo[table] = "id = :h"
            return memo[table]
        if table in schema.hotel_tables:
            memo[table] = f"{q('hotel_id')} = :h"
            return memo[table]
        if table in visiting:  # aylana — bu yo'ldan egalik yo'q
            return None
        visiting.add(table)
        parts = []
        for fk in schema.by_child.get(table, []):
            if fk.parent == table:
                continue
            parent_pred = pred(fk.parent)
            if parent_pred:
                parts.append(
                    f"{q(fk.child_col)} IN (SELECT {q(fk.parent_col)} FROM {q(fk.parent)} "
                    f"WHERE {parent_pred})"
                )
        visiting.discard(table)
        memo[table] = " OR ".join(f"({p})" for p in parts) if parts else None
        return memo[table]

    out = {}
    for table in sorted(schema.tables):
        p = pred(table)
        if p:
            out[table] = p
    return out


def deletion_order(schema: Schema, owned: dict[str, str]) -> list[str]:
    """Bolalar oldin, ota-onalar keyin (`hotels` — eng oxirida, alohida)."""
    tables = [t for t in owned if t != HOTELS]
    parents_of = {
        t: {fk.parent for fk in schema.by_child.get(t, []) if fk.parent in owned and fk.parent != t}
        for t in tables
    }
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(table: str) -> None:
        # Ota-onalar oldin qo'yiladi; oxirida ro'yxat teskari aylantiriladi
        if state.get(table) == 2:
            return
        if state.get(table) == 1:
            raise ValidationException(
                f"Jadvallar orasida aylana bog'lanish: {table}", "PURGE_CYCLE"
            )
        state[table] = 1
        for parent in sorted(parents_of.get(table, ())):
            if parent != HOTELS:
                visit(parent)
        state[table] = 2
        order.append(table)

    for table in sorted(tables):
        visit(table)
    return list(reversed(order))


class HotelPurgeService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------ sxema --
    async def schema(self) -> Schema:
        rows = (
            await self.session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
        ).scalars().all()
        tables = set(rows) - {"alembic_version"}
        hotel_tables = set(
            (
                await self.session.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name = 'hotel_id'"
                    )
                )
            ).scalars().all()
        ) & tables
        composite = (
            await self.session.execute(
                text(
                    "SELECT count(*) FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    "WHERE c.contype = 'f' AND n.nspname = 'public' "
                    "AND array_length(c.conkey, 1) > 1"
                )
            )
        ).scalar()
        if composite:
            # Bunday kalitni to'g'ri kuzatib bo'lmaydi — taxmin qilib o'chirmaymiz
            raise ValidationException(
                "Bazada ko'p ustunli tashqi kalit bor — avtomatik o'chirish to'xtatildi",
                "PURGE_SCHEMA",
            )
        fk_rows = (
            await self.session.execute(
                text(
                    "SELECT child.relname, ca.attname, parent.relname, pa.attname, c.confdeltype::text "
                    "FROM pg_constraint c "
                    "JOIN pg_class child ON child.oid = c.conrelid "
                    "JOIN pg_class parent ON parent.oid = c.confrelid "
                    "JOIN pg_namespace n ON n.oid = child.relnamespace "
                    "JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = c.conkey[1] "
                    "JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = c.confkey[1] "
                    "WHERE c.contype = 'f' AND n.nspname = 'public'"
                )
            )
        ).all()
        fks = [
            ForeignKey(child=r[0], child_col=r[1], parent=r[2], parent_col=r[3], rule=_rule(r[4]))
            for r in fk_rows
            if r[0] in tables and r[2] in tables
        ]
        return Schema(tables=tables, hotel_tables=hotel_tables, fks=fks)

    # ------------------------------------------------------------- reja --
    async def _hotel(self, hotel_id: UUID, lock: bool = False) -> dict:
        sql = "SELECT id, name, code, status FROM hotels WHERE id = :h"
        if lock:
            sql += " FOR UPDATE"
        row = (await self.session.execute(text(sql), {"h": hotel_id})).first()
        if row is None:
            raise NotFoundException("Mehmonxona topilmadi", "HOTEL_NOT_FOUND")
        return {"id": str(row[0]), "name": row[1], "code": row[2], "status": row[3]}

    async def _share_guests(self, schema: Schema, hotel_id: UUID) -> list[dict]:
        """Boshqa mehmonxona ishlatgan mehmonlar — o'sha mehmonxonaga o'tadi.

        Natija vaqtinchalik jadvalda (tranzaksiya oxirida o'chadi).
        """
        await self.session.execute(text(f"DROP TABLE IF EXISTS {SHARED_TEMP}"))
        await self.session.execute(
            text(
                f"CREATE TEMP TABLE {SHARED_TEMP} (guest_id uuid PRIMARY KEY, "
                "new_hotel_id uuid NOT NULL, new_branch_id uuid) ON COMMIT DROP"
            )
        )
        refs = [
            fk for fk in schema.by_parent.get(GUESTS, [])
            if fk.child in schema.hotel_tables and fk.child != GUESTS
        ]
        if not refs:
            return []
        branch_tables = await self._tables_with_column("branch_id")
        union = " UNION ALL ".join(
            f"SELECT {q(fk.child_col)} AS guest_id, {q('hotel_id')} AS hotel_id, "
            + (f"{q('branch_id')}" if fk.child in branch_tables else "NULL::uuid")
            + f" AS branch_id FROM {q(fk.child)} "
            f"WHERE {q(fk.child_col)} IS NOT NULL AND {q('hotel_id')} <> :h"
            for fk in refs
        )
        # Eng ko'p ishlatgan mehmonxona va FILIALiga (teng bo'lsa — barqaror
        # tartibda): filiallar ajratilgan, mehmon o'sha filial yozuviga aylanadi
        await self.session.execute(
            text(
                f"INSERT INTO {SHARED_TEMP} (guest_id, new_hotel_id, new_branch_id) "
                "SELECT DISTINCT ON (r.guest_id) r.guest_id, r.hotel_id, r.branch_id "
                f"FROM ({union}) r JOIN {q(GUESTS)} g ON g.id = r.guest_id "
                "WHERE g.hotel_id = :h "
                "GROUP BY r.guest_id, r.hotel_id, r.branch_id "
                "ORDER BY r.guest_id, count(*) DESC, r.hotel_id, r.branch_id NULLS LAST"
            ),
            {"h": hotel_id},
        )
        rows = (
            await self.session.execute(
                text(
                    f"SELECT s.new_hotel_id, h.name, count(*) FROM {SHARED_TEMP} s "
                    "JOIN hotels h ON h.id = s.new_hotel_id GROUP BY s.new_hotel_id, h.name "
                    "ORDER BY count(*) DESC"
                )
            )
        ).all()
        return [{"hotel_id": str(r[0]), "hotel_name": r[1], "guests": int(r[2])} for r in rows]

    async def _tables_with_column(self, column: str) -> set[str]:
        return set(
            (
                await self.session.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name = :c"
                    ),
                    {"c": column},
                )
            ).scalars().all()
        )

    async def _rehome_guests(self) -> int:
        if GUESTS in await self._tables_with_column("branch_id"):
            # Filial: mehmonni ishlatgan yozuvlar filiali, bo'lmasa yangi
            # mehmonxonaning asosiy filiali
            sql = (
                f"UPDATE {q(GUESTS)} g SET hotel_id = s.new_hotel_id, branch_id = COALESCE("
                "s.new_branch_id, (SELECT b.id FROM branches b WHERE b.hotel_id = s.new_hotel_id "
                "ORDER BY b.is_main_branch DESC NULLS LAST, b.created_at, b.id LIMIT 1)) "
                f"FROM {SHARED_TEMP} s WHERE g.id = s.guest_id"
            )
        else:
            sql = (
                f"UPDATE {q(GUESTS)} g SET hotel_id = s.new_hotel_id "
                f"FROM {SHARED_TEMP} s WHERE g.id = s.guest_id"
            )
        result = await self.session.execute(text(sql))
        return int(result.rowcount or 0)

    async def _detach_system_users(self, schema: Schema, hotel_id: UUID) -> list[dict]:
        """SUPER_ADMIN / CONFIGURATOR akkaunti shu mehmonxonaga yozilgan
        bo'lsa — o'chirilmaydi, faqat mehmonxonadan ajratiladi.

        Bu tizim darajasidagi akkauntlar (hotel_id siz ham ishlaydi);
        o'chsa egasi asosiy tizimga kira olmay qolishi mumkin edi.
        """
        if USERS not in schema.hotel_tables:
            return []
        cols = set(
            (
                await self.session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :t"
                    ),
                    {"t": USERS},
                )
            ).scalars().all()
        )
        if "user_type" not in cols:
            return []
        sets = [f"{q('hotel_id')} = NULL"]
        if "branch_id" in cols and "branches" in schema.hotel_tables:
            # Faqat shu mehmonxona filiali bo'lsa bo'shatiladi
            sets.append(
                f"{q('branch_id')} = CASE WHEN {q('branch_id')} IN "
                f"(SELECT id FROM {q('branches')} WHERE {q('hotel_id')} = :h) "
                f"THEN NULL ELSE {q('branch_id')} END"
            )
        username = q("username") if "username" in cols else "NULL"
        rows = (
            await self.session.execute(
                text(
                    f"UPDATE {q(USERS)} SET {', '.join(sets)} "
                    f"WHERE {q('hotel_id')} = :h AND {q('user_type')} IN :types "
                    f"RETURNING id, {username}, {q('user_type')}"
                ).bindparams(bindparam("types", expanding=True)),
                {"h": hotel_id, "types": list(SYSTEM_USER_TYPES)},
            )
        ).all()
        return [{"id": str(r[0]), "username": r[1], "user_type": r[2]} for r in rows]

    async def _counts(self, owned: dict[str, str], hotel_id: UUID) -> dict[str, int]:
        counts = {}
        for table, pred in owned.items():
            n = (
                await self.session.execute(
                    text(f"SELECT count(*) FROM {q(table)} WHERE {pred}"), {"h": hotel_id}
                )
            ).scalar()
            if n:
                counts[table] = int(n)
        return counts

    async def _conflicts(self, schema: Schema, owned: dict[str, str], hotel_id: UUID) -> list[dict]:
        """Boshqa (yoki global) qatorlar shu mehmonxona qatoriga qaraydimi."""
        out = []
        for fk in schema.fks:
            parent_pred = owned.get(fk.parent)
            if not parent_pred:
                continue
            child_pred = owned.get(fk.child)
            not_owned = f" AND NOT ({child_pred})" if child_pred else ""
            n = (
                await self.session.execute(
                    text(
                        f"SELECT count(*) FROM {q(fk.child)} WHERE {q(fk.child_col)} IN "
                        f"(SELECT {q(fk.parent_col)} FROM {q(fk.parent)} WHERE {parent_pred})"
                        f"{not_owned}"
                    ),
                    {"h": hotel_id},
                )
            ).scalar()
            if not n:
                continue
            blocking = fk.rule in BLOCKING_RULES
            out.append(
                {
                    "table": fk.child,
                    "column": fk.child_col,
                    "references": fk.parent,
                    "rows": int(n),
                    "on_delete": BLOCKING_RULES.get(fk.rule) or NULLING_RULES.get(fk.rule, fk.rule),
                    "blocking": blocking,
                }
            )
        return out

    async def _files(self, schema: Schema, hotel_id: UUID) -> list[tuple[str, str]]:
        if "file_attachments" not in schema.tables:
            return []
        rows = (
            await self.session.execute(
                text(
                    "SELECT DISTINCT minio_bucket, minio_path FROM file_attachments "
                    "WHERE hotel_id = :h"
                ),
                {"h": hotel_id},
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    async def _plan(self, hotel_id: UUID, *, lock: bool) -> dict:
        hotel = await self._hotel(hotel_id, lock=lock)
        schema = await self.schema()
        owned = owned_predicates(schema)
        shared = await self._share_guests(schema, hotel_id)
        # Umumiy mehmonlar o'tkazilgach ular endi "bu mehmonxonaniki" emas —
        # sanash va to'siqlar shundan keyin (ko'rib chiqishda tranzaksiya
        # baribir bekor qilinadi)
        rehomed = await self._rehome_guests()
        system_users = await self._detach_system_users(schema, hotel_id)
        counts = await self._counts(owned, hotel_id)
        conflicts = await self._conflicts(schema, owned, hotel_id)
        return {
            "hotel": hotel,
            "schema": schema,
            "owned": owned,
            "shared": shared,
            "rehomed": rehomed,
            "system_users": system_users,
            "counts": counts,
            "conflicts": conflicts,
        }

    @staticmethod
    def _report(plan: dict, files: int) -> dict:
        counts = plan["counts"]
        blocking = [c for c in plan["conflicts"] if c["blocking"]]
        reasons = []
        if plan["hotel"]["status"] != "INACTIVE":
            reasons.append("HOTEL_ACTIVE")
        if blocking:
            reasons.append("CROSS_HOTEL_REFERENCES")
        return {
            "hotel": plan["hotel"],
            "tables": [
                {"table": t, "rows": n}
                for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "total_rows": sum(counts.values()),
            "shared_guests": {
                "count": plan["rehomed"],
                "hotels": plan["shared"],
            },
            "system_users": plan["system_users"],
            "conflicts": plan["conflicts"],
            "files": files,
            "can_purge": not reasons,
            "blocked_by": reasons,
        }

    # ------------------------------------------------------ tashqi API --
    async def preview(self, hotel_id: UUID) -> dict:
        """Nima o'chishini ko'rsatadi — hech narsa o'zgarmaydi."""
        try:
            plan = await self._plan(hotel_id, lock=False)
            files = len(await self._files(plan["schema"], hotel_id))
            return self._report(plan, files)
        finally:
            # Umumiy mehmonlarni o'tkazish ham faqat hisob uchun edi
            await self.session.rollback()

    async def purge(self, hotel_id: UUID, *, actor: str | None = None) -> dict:
        """Hammasini o'chiradi. Xato bo'lsa — hech narsa o'chmaydi."""
        started = time.monotonic()
        await self.session.execute(text("SET LOCAL lock_timeout = '15s'"))
        await self.session.execute(text("SET LOCAL statement_timeout = '600s'"))
        plan = await self._plan(hotel_id, lock=True)
        if plan["hotel"]["status"] != "INACTIVE":
            raise ConflictException(
                "Avval mehmonxonani to'xtating (INACTIVE) — faol mehmonxona o'chirilmaydi",
                "HOTEL_ACTIVE",
            )
        blocking = [c for c in plan["conflicts"] if c["blocking"]]
        if blocking:
            where = ", ".join(
                f"{c['table']}.{c['column']} → {c['references']} ({c['rows']})" for c in blocking
            )
            raise ConflictException(
                f"Boshqa mehmonxona ma'lumotlari bu mehmonxonaga bog'langan: {where}",
                "CROSS_HOTEL_REFERENCES",
            )

        files = await self._files(plan["schema"], hotel_id)
        deleted: dict[str, int] = {}
        for table in deletion_order(plan["schema"], plan["owned"]):
            result = await self.session.execute(
                text(f"DELETE FROM {q(table)} WHERE {plan['owned'][table]}"), {"h": hotel_id}
            )
            if result.rowcount:
                deleted[table] = int(result.rowcount)
        result = await self.session.execute(text("DELETE FROM hotels WHERE id = :h"), {"h": hotel_id})
        deleted[HOTELS] = int(result.rowcount or 0)
        await self.session.commit()

        removed, failed = await self._remove_files(hotel_id, files)
        report = {
            "hotel": plan["hotel"],
            "deleted": dict(sorted(deleted.items(), key=lambda kv: (-kv[1], kv[0]))),
            "total_rows": sum(deleted.values()),
            "guests_moved": plan["rehomed"],
            "guests_moved_to": plan["shared"],
            "system_users_kept": plan["system_users"],
            "set_null": [c for c in plan["conflicts"] if not c["blocking"]],
            "files_removed": removed,
            "files_failed": failed,
            "seconds": round(time.monotonic() - started, 2),
        }
        logger.warning(
            "MEHMONXONA BUTUNLAY O'CHIRILDI: %s (%s) — %s qator, %s fayl, %s mehmon "
            "boshqa mehmonxonaga o'tdi; bajaruvchi: %s",
            plan["hotel"]["name"], plan["hotel"]["code"], report["total_rows"],
            removed, plan["rehomed"], actor or "?",
        )
        return report

    # ---------------------------------------------------------- MinIO --
    async def _remove_files(self, hotel_id: UUID, files: list[tuple[str, str]]) -> tuple[int, int]:
        """Mehmonxona fayllari: yozuvlardagilar va `{hotel_id}/` ostidagi
        yetimlar. Boshqa yozuv hali qarayotgan obyektga tegilmaydi."""
        try:
            from app.infrastructure.storage import minio as storage

            client = await asyncio.wait_for(asyncio.to_thread(storage.get_minio_client), timeout=20)
        except Exception as exc:
            logger.warning("MinIO mavjud emas — fayllar o'chirilmadi: %s", exc)
            return 0, len(files)

        targets = set(files)
        prefix = f"{hotel_id}/"
        for bucket in {settings.MINIO_BUCKET_DOCUMENTS, settings.MINIO_BUCKET_GUESTS}:
            try:
                objects = await asyncio.to_thread(
                    lambda b=bucket: [o.object_name for o in client.list_objects(b, prefix=prefix, recursive=True)]
                )
                targets |= {(bucket, name) for name in objects}
            except Exception as exc:
                logger.warning("MinIO ro'yxatini olib bo'lmadi (%s): %s", bucket, exc)

        removed = failed = 0
        for bucket, path in sorted(targets):
            still_used = (
                await self.session.execute(
                    text(
                        "SELECT 1 FROM file_attachments WHERE minio_bucket = :b "
                        "AND minio_path = :p LIMIT 1"
                    ),
                    {"b": bucket, "p": path},
                )
            ).first()
            if still_used:
                continue
            try:
                await asyncio.to_thread(client.remove_object, bucket, path)
                removed += 1
            except Exception as exc:
                failed += 1
                logger.warning("Faylni o'chirib bo'lmadi %s/%s: %s", bucket, path, exc)
        return removed, failed
