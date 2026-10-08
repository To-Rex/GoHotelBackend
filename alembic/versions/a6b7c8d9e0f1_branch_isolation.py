"""branch isolation: every hotel row belongs to one branch

Filiallar to'liq ajratiladi: har bir mehmonxona ma'lumoti bitta filialga
tegishli bo'ladi (xodimlar, mehmonlar, bronlar, moliya, do'kon, kataloglar,
sozlamalar...). Bu migratsiya MAVJUD ma'lumotni yo'qotmasdan filialga
taqsimlaydi:

* Filiali yo'q mehmonxonaga "Asosiy filial" ochiladi.
* Sozlamalar (`hotels.settings`) har bir filialga nusxalanadi
  (`branches.settings`) — keyin har filial o'zinikini o'zgartiradi.
* Filialni aniq ko'rsatadigan bog'lanish bo'lsa (bron, xona, xodim...) —
  o'sha filial; bo'lmasa — asosiy filial.
* Bir necha filialda bron qilgan mehmon har filial uchun alohida yozuvga
  ajratiladi (asosiysi — eng ko'p bron qilgan filialda), o'sha filial
  bronlari, hisoblari va hamroh ro'yxatlari yangi yozuvga o'tkaziladi.
* Kataloglar (mehmonxonaning xona turlari, xizmat narxlari, qulayliklar,
  cheklist shablonlari, hisob rejasi, do'kon mahsulotlari) har bir
  filialga nusxalanadi va filial yozuvlari o'z nusxasiga ulanadi —
  avval hamma filial bitta katalogni ishlatardi, endi har biri o'zinikini.
* Do'kon zaxirasi (partiyalar) asosiy filialda qoladi; boshqa filialda
  bo'lgan eski savdolar uchun partiya nusxasi (qoldig'i 0) ochiladi —
  tannarx tarixi saqlanadi, zaxira ikki marta sanalmaydi.

Revision ID: a6b7c8d9e0f1
Revises: f5c6d7e8f9a0
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a6b7c8d9e0f1'
down_revision: Union[str, None] = 'f5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Yangi `branch_id` ustuni qo'shiladigan jadvallar
NEW_BRANCH_TABLES = (
    "audit_logs",
    "buildings",
    "checklist_templates",
    "document_scans",
    "guest_face_profiles",
    "guests",
    "hotel_amenities",
    "hotel_room_types",
    "hotel_services",
    "incoming_calls",
    "invoice_line_items",
    "invoices",
    "journal_entries",
    "journal_entry_lines",
    "ledgers",
    "notifications",
    "payments",
    "reports",
    "reservation_penalties",
    "reservation_services",
    "room_status_history",
    "room_types",
    "shop_batches",
    "shop_products",
    "shop_sale_payments",
    "shop_sales",
    "shop_writeoffs",
    "staff_messages",
    "trusted_devices",
)

#: Filial ustuni avvaldan bor, lekin indeksi yo'q jadvallar
MISSING_BRANCH_INDEXES = (
    "expenses",
    "guest_feedback",
    "housekeeping_tasks",
    "problems",
    "reservations",
    "users",
    "vision_devices",
)

UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def _x(sql: str, **params) -> None:
    op.get_bind().execute(sa.text(sql), params)


def _cols(table: str, exclude: tuple = ()) -> list[str]:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :t "
            "ORDER BY ordinal_position"
        ),
        {"t": table},
    ).scalars().all()
    return [c for c in rows if c not in exclude]


def _drop_unique(table: str, columns: list[str]) -> None:
    """(columns) bo'yicha unikal cheklov yoki indeksni nomidan qat'i nazar
    olib tashlaydi (prod va lokal bazalarda nomlar farq qilishi mumkin)."""
    bind = op.get_bind()
    wanted = list(columns)
    constraints = bind.execute(
        sa.text(
            "SELECT c.conname, c.contype, "
            "ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, o) "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n ORDER BY k.o) "
            "FROM pg_constraint c WHERE c.conrelid = (:t)::regclass AND c.contype IN ('u', 'p')"
        ),
        {"t": table},
    ).all()
    for name, _kind, cols in constraints:
        if list(cols) == wanted:
            bind.execute(sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"'))
    indexes = bind.execute(
        sa.text(
            "SELECT i.relname, "
            "ARRAY(SELECT a.attname FROM unnest(x.indkey::int2[]) WITH ORDINALITY k(n, o) "
            "JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.n ORDER BY k.o) "
            "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
            "WHERE x.indrelid = (:t)::regclass AND x.indisunique"
        ),
        {"t": table},
    ).all()
    for name, cols in indexes:
        if list(cols) == wanted:
            bind.execute(sa.text(f'DROP INDEX IF EXISTS "{name}"'))


def _fill_main(table: str) -> None:
    """Qolgan bo'sh `branch_id` — mehmonxonaning asosiy filiali."""
    _x(
        f'UPDATE "{table}" t SET branch_id = m.branch_id FROM _bi_main m '
        "WHERE t.branch_id IS NULL AND t.hotel_id = m.hotel_id"
    )


def _fill_from_user(table: str, user_col: str) -> None:
    """Yozuvni yaratgan xodimning filiali (shu mehmonxonada bo'lsa)."""
    _x(
        f'UPDATE "{table}" t SET branch_id = u.branch_id FROM users u, branches b '
        f"WHERE t.branch_id IS NULL AND t.{user_col} = u.id AND u.branch_id = b.id "
        "AND b.hotel_id = t.hotel_id"
    )


def _clone_rows(table: str, map_table: str, overrides: dict | None = None) -> None:
    """`map_table(old_id, branch_id, new_id)` bo'yicha qatorlarni nusxalaydi."""
    overrides = overrides or {}
    cols = _cols(table, exclude=("id", "branch_id"))
    select_cols = ", ".join(overrides.get(c, f't."{c}"') for c in cols)
    insert_cols = ", ".join(f'"{c}"' for c in cols)
    _x(
        f'INSERT INTO "{table}" (id, branch_id, {insert_cols}) '
        f"SELECT m.new_id, m.branch_id, {select_cols} "
        f'FROM {map_table} m JOIN "{table}" t ON t.id = m.old_id'
    )


def _catalog_map(table: str, map_table: str) -> None:
    """Asosiy filialdagi katalog qatorining har bir boshqa filial uchun nusxa xaritasi."""
    _x(
        f"CREATE TEMP TABLE {map_table} AS "
        "SELECT t.id AS old_id, e.branch_id, gen_random_uuid() AS new_id "
        f'FROM "{table}" t JOIN _bi_extra e ON e.hotel_id = t.hotel_id'
    )
    _x(f"CREATE INDEX ON {map_table} (old_id, branch_id)")


def upgrade() -> None:
    # ------------------------------------------------ asosiy filiallar --
    # Filiali umuman yo'q mehmonxona — bitta asosiy filial ochiladi
    _x(
        "INSERT INTO branches (id, hotel_id, name, code, is_main_branch, status, created_at, updated_at) "
        "SELECT gen_random_uuid(), h.id, 'Asosiy filial', 'MAIN', true, 'ACTIVE', now(), now() "
        "FROM hotels h WHERE NOT EXISTS (SELECT 1 FROM branches b WHERE b.hotel_id = h.id)"
    )
    _x(
        "CREATE TEMP TABLE _bi_main AS "
        "SELECT DISTINCT ON (hotel_id) hotel_id, id AS branch_id FROM branches "
        "ORDER BY hotel_id, is_main_branch DESC NULLS LAST, created_at NULLS LAST, id"
    )
    _x(
        "CREATE TEMP TABLE _bi_extra AS "
        "SELECT b.hotel_id, b.id AS branch_id FROM branches b "
        "JOIN _bi_main m ON m.hotel_id = b.hotel_id WHERE b.id <> m.branch_id"
    )

    # ------------------------------------------------------ sozlamalar --
    op.add_column("branches", sa.Column("settings", postgresql.JSONB(), nullable=True))
    _x(
        "UPDATE branches b SET settings = COALESCE(h.settings, '{}'::jsonb) "
        "FROM hotels h WHERE h.id = b.hotel_id"
    )

    # ----------------------------------------------------- yangi ustunlar --
    for table in NEW_BRANCH_TABLES:
        op.add_column(
            table,
            sa.Column(
                "branch_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("branches.id", name=f"fk_{table}_branch_id"),
                nullable=True,
            ),
        )

    # ------------------------------------------------------- xodimlar --
    _x(
        "UPDATE users u SET branch_id = m.branch_id FROM _bi_main m "
        "WHERE u.hotel_id = m.hotel_id AND (u.branch_id IS NULL OR NOT EXISTS ("
        "SELECT 1 FROM branches b WHERE b.id = u.branch_id AND b.hotel_id = u.hotel_id))"
    )

    # ------------------------ filial ustuni bor, lekin bo'sh qolganlar --
    _x(
        "UPDATE problems p SET branch_id = r.branch_id FROM rooms r "
        "WHERE p.branch_id IS NULL AND p.room_id = r.id"
    )
    _x(
        "UPDATE problems p SET branch_id = t.branch_id FROM housekeeping_tasks t "
        "WHERE p.branch_id IS NULL AND p.task_id = t.id"
    )
    _fill_from_user("problems", "reported_by")
    _fill_main("problems")
    _fill_from_user("expenses", "created_by")
    _fill_main("expenses")
    _fill_from_user("shift_sessions", "user_id")
    _fill_main("shift_sessions")
    _x(
        "UPDATE guest_feedback f SET branch_id = r.branch_id FROM reservations r "
        "WHERE f.branch_id IS NULL AND f.reservation_id = r.id"
    )
    _x(
        "UPDATE guest_feedback f SET branch_id = r.branch_id FROM rooms r "
        "WHERE f.branch_id IS NULL AND f.room_id = r.id"
    )
    _x(
        "UPDATE face_sightings s SET branch_id = r.branch_id FROM reservations r "
        "WHERE s.branch_id IS NULL AND s.reservation_id = r.id"
    )
    # vision_devices / vision_cameras: bo'sh filial — "umumiy qurilma" va
    # "biriktirilmagan kamera" ma'nosida, o'zgartirilmaydi

    _x(
        "UPDATE room_status_history h SET branch_id = r.branch_id FROM rooms r "
        "WHERE h.room_id = r.id"
    )

    # -------------------------------------------------------- mehmonlar --
    # Mehmon qaysi filiallarda ishlatilgan: bronlar, hamrohlar, kamera,
    # taklif/shikoyatlar
    _x(
        "CREATE TEMP TABLE _bi_gref AS "
        "SELECT x.guest_id, x.branch_id, count(*) AS n FROM ("
        "  SELECT guest_id, branch_id FROM reservations"
        "  UNION ALL"
        "  SELECT (e.value->>'guest_id')::uuid, r.branch_id FROM reservations r, "
        "  jsonb_array_elements(CASE WHEN jsonb_typeof(r.companions) = 'array' "
        "    THEN r.companions ELSE '[]'::jsonb END) e "
        "  WHERE jsonb_typeof(e.value) = 'object' AND (e.value->>'guest_id') ~ :uuid_re"
        "  UNION ALL"
        "  SELECT guest_id, branch_id FROM face_sightings WHERE guest_id IS NOT NULL AND branch_id IS NOT NULL"
        "  UNION ALL"
        "  SELECT guest_id, branch_id FROM guest_feedback WHERE guest_id IS NOT NULL AND branch_id IS NOT NULL"
        ") x JOIN guests g ON g.id = x.guest_id "
        "JOIN branches b ON b.id = x.branch_id AND b.hotel_id = g.hotel_id "
        "GROUP BY x.guest_id, x.branch_id",
        uuid_re=UUID_RE,
    )
    # Asosiy yozuv — eng ko'p ishlatilgan filialda (teng bo'lsa asosiy filial)
    _x(
        "UPDATE guests g SET branch_id = p.branch_id FROM ("
        "  SELECT DISTINCT ON (r.guest_id) r.guest_id, r.branch_id FROM _bi_gref r "
        "  JOIN guests g2 ON g2.id = r.guest_id JOIN _bi_main m ON m.hotel_id = g2.hotel_id "
        "  ORDER BY r.guest_id, r.n DESC, (r.branch_id = m.branch_id) DESC, r.branch_id"
        ") p WHERE g.id = p.guest_id"
    )
    _fill_main("guests")
    _x(
        "CREATE TEMP TABLE _bi_gclone AS "
        "SELECT r.guest_id AS old_id, r.branch_id, gen_random_uuid() AS new_id "
        "FROM _bi_gref r JOIN guests g ON g.id = r.guest_id WHERE r.branch_id <> g.branch_id"
    )
    _x("CREATE INDEX ON _bi_gclone (old_id, branch_id)")
    _clone_rows("guests", "_bi_gclone")
    # O'sha filial yozuvlari yangi mehmon yozuviga
    _x(
        "UPDATE reservations r SET guest_id = c.new_id FROM _bi_gclone c "
        "WHERE r.guest_id = c.old_id AND r.branch_id = c.branch_id"
    )
    _x(
        "UPDATE reservations r SET companions = ("
        "  SELECT jsonb_agg(CASE WHEN c.new_id IS NOT NULL "
        "    THEN jsonb_set(e.value, '{guest_id}', to_jsonb(c.new_id::text)) ELSE e.value END "
        "    ORDER BY e.ordinality) "
        "  FROM jsonb_array_elements(r.companions) WITH ORDINALITY e "
        "  LEFT JOIN _bi_gclone c ON jsonb_typeof(e.value) = 'object' "
        "    AND c.old_id::text = lower(e.value->>'guest_id') AND c.branch_id = r.branch_id"
        ") WHERE jsonb_typeof(r.companions) = 'array' AND jsonb_array_length(r.companions) > 0 "
        "AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.companions) e2 "
        "  JOIN _bi_gclone c2 ON jsonb_typeof(e2.value) = 'object' "
        "  AND c2.old_id::text = lower(e2.value->>'guest_id') AND c2.branch_id = r.branch_id)"
    )
    _x(
        "UPDATE invoices i SET guest_id = c.new_id FROM reservations r, _bi_gclone c "
        "WHERE i.reservation_id = r.id AND i.guest_id = c.old_id AND c.branch_id = r.branch_id"
    )
    for table in ("face_sightings", "guest_feedback"):
        _x(
            f"UPDATE {table} t SET guest_id = c.new_id FROM _bi_gclone c "
            "WHERE t.guest_id = c.old_id AND t.branch_id = c.branch_id"
        )
    _x(
        "UPDATE incoming_calls ic SET guest_id = c.new_id FROM reservations r, _bi_gclone c "
        "WHERE ic.reservation_id = r.id AND ic.guest_id = c.old_id AND c.branch_id = r.branch_id"
    )
    # Yuz profili — har bir mehmon yozuvi uchun (tanish har filialda ishlasin)
    _x(
        "CREATE TEMP TABLE _bi_fpclone AS "
        "SELECT p.id AS old_id, c.branch_id, gen_random_uuid() AS new_id, c.new_id AS guest_id "
        "FROM _bi_gclone c JOIN guest_face_profiles p ON p.guest_id = c.old_id"
    )
    _clone_rows("guest_face_profiles", "_bi_fpclone", {"guest_id": "m.guest_id"})
    _x(
        "UPDATE guest_face_profiles p SET branch_id = g.branch_id FROM guests g "
        "WHERE p.branch_id IS NULL AND p.guest_id = g.id"
    )
    _fill_main("guest_face_profiles")

    # ----------------------------------------------------- bron bo'yicha --
    for table in ("reservation_services", "reservation_penalties", "invoices"):
        _x(
            f"UPDATE {table} t SET branch_id = r.branch_id FROM reservations r "
            "WHERE t.reservation_id = r.id"
        )
        _fill_main(table)
    for table in ("invoice_line_items", "payments"):
        _x(
            f"UPDATE {table} t SET branch_id = i.branch_id FROM invoices i "
            "WHERE t.invoice_id = i.id"
        )
        _fill_main(table)
    _x(
        "UPDATE incoming_calls ic SET branch_id = r.branch_id FROM reservations r "
        "WHERE ic.reservation_id = r.id"
    )
    _x(
        "UPDATE incoming_calls ic SET branch_id = g.branch_id FROM guests g "
        "WHERE ic.branch_id IS NULL AND ic.guest_id = g.id"
    )
    _fill_from_user("incoming_calls", "reported_by")
    _fill_main("incoming_calls")
    _x(
        "UPDATE document_scans d SET branch_id = g.branch_id FROM guests g "
        "WHERE d.guest_id = g.id"
    )
    _fill_from_user("document_scans", "scanned_by")
    _fill_main("document_scans")
    _x(
        "UPDATE face_sightings s SET branch_id = g.branch_id FROM guests g "
        "WHERE s.branch_id IS NULL AND s.guest_id = g.id"
    )
    _fill_main("face_sightings")
    _fill_from_user("guest_feedback", "created_by")
    _fill_main("guest_feedback")

    # ---------------------------------------------------------- do'kon --
    _x(
        "UPDATE shop_sales s SET branch_id = r.branch_id FROM reservations r "
        "WHERE s.reservation_id = r.id"
    )
    _fill_from_user("shop_sales", "created_by")
    _fill_main("shop_sales")
    _x(
        "UPDATE shop_sale_payments p SET branch_id = s.branch_id FROM shop_sales s "
        "WHERE p.sale_id = s.id"
    )
    _fill_main("shop_sale_payments")
    _drop_unique("shop_products", ["hotel_id", "name"])
    _fill_main("shop_products")
    _x(
        "UPDATE shop_batches b SET branch_id = p.branch_id FROM shop_products p "
        "WHERE b.product_id = p.id"
    )
    _x(
        "UPDATE shop_writeoffs w SET branch_id = p.branch_id FROM shop_products p "
        "WHERE w.product_id = p.id"
    )
    _catalog_map("shop_products", "_bi_pclone")
    _clone_rows("shop_products", "_bi_pclone")
    _x(
        "UPDATE shop_sale_items si SET product_id = c.new_id "
        "FROM shop_sales s, _bi_pclone c "
        "WHERE si.sale_id = s.id AND si.product_id = c.old_id AND c.branch_id = s.branch_id"
    )
    # Boshqa filial savdosi asosiy filial partiyasidan bo'lgan: o'sha filialda
    # sotilgan miqdor o'sha filialga "o'tkazilgan" partiya bo'ladi (qoldig'i 0),
    # asosiy partiya miqdori shunchaga kamayadi — har filial harakatida
    # kirim - chiqim = qoldiq to'g'ri chiqadi, zaxira ikki marta sanalmaydi
    _x(
        "CREATE TEMP TABLE _bi_bmove AS "
        "SELECT si.batch_id AS old_id, s.branch_id, sum(GREATEST(si.quantity, 0)) AS qty "
        "FROM shop_sale_items si JOIN shop_sales s ON s.id = si.sale_id "
        "JOIN shop_batches b ON b.id = si.batch_id WHERE b.branch_id <> s.branch_id "
        "GROUP BY si.batch_id, s.branch_id"
    )
    # Partiya butunlay boshqa filial(lar)da sotilgan — eng ko'p sotgan
    # filialga o'zi o'tadi (miqdor 0 bo'la olmaydi)
    _x(
        "CREATE TEMP TABLE _bi_bwhole AS "
        "SELECT DISTINCT ON (mv.old_id) mv.old_id, mv.branch_id, mv.qty "
        "FROM _bi_bmove mv JOIN shop_batches b ON b.id = mv.old_id "
        "WHERE b.quantity - (SELECT sum(x.qty) FROM _bi_bmove x WHERE x.old_id = mv.old_id) <= 0 "
        "ORDER BY mv.old_id, mv.qty DESC, mv.branch_id"
    )
    _x(
        "CREATE TEMP TABLE _bi_bclone AS "
        "SELECT mv.old_id, mv.branch_id, mv.qty, gen_random_uuid() AS new_id FROM _bi_bmove mv "
        "WHERE NOT EXISTS (SELECT 1 FROM _bi_bwhole w WHERE w.old_id = mv.old_id AND w.branch_id = mv.branch_id)"
    )
    _x(
        "UPDATE shop_batches b SET quantity = b.quantity - x.total "
        "FROM (SELECT old_id, sum(qty) AS total FROM _bi_bmove GROUP BY old_id) x "
        "WHERE b.id = x.old_id AND NOT EXISTS (SELECT 1 FROM _bi_bwhole w WHERE w.old_id = b.id)"
    )
    _clone_rows(
        "shop_batches",
        "_bi_bclone",
        {
            "quantity": "GREATEST(m.qty, 1)",
            "remaining": "0",
            "product_id": (
                "COALESCE((SELECT pc.new_id FROM _bi_pclone pc WHERE pc.old_id = t.product_id "
                "AND pc.branch_id = m.branch_id), t.product_id)"
            ),
        },
    )
    _x(
        "UPDATE shop_sale_items si SET batch_id = c.new_id FROM shop_sales s, _bi_bclone c "
        "WHERE si.sale_id = s.id AND si.batch_id = c.old_id AND c.branch_id = s.branch_id"
    )
    _x(
        "UPDATE shop_batches b SET branch_id = w.branch_id, quantity = GREATEST(w.qty, 1), "
        "product_id = COALESCE((SELECT pc.new_id FROM _bi_pclone pc WHERE pc.old_id = b.product_id "
        "AND pc.branch_id = w.branch_id), b.product_id) "
        "FROM _bi_bwhole w WHERE b.id = w.old_id"
    )
    _fill_main("shop_batches")
    _fill_main("shop_writeoffs")

    # ------------------------------------------------------- kataloglar --
    # Mehmonxonaning o'z xona turlari (global turlar — hotel_id bo'sh — umumiy qoladi)
    _drop_unique("room_types", ["hotel_id", "name"])
    _x(
        "UPDATE room_types t SET branch_id = m.branch_id FROM _bi_main m "
        "WHERE t.hotel_id = m.hotel_id"
    )
    _catalog_map("room_types", "_bi_rtclone")
    _x("DELETE FROM _bi_rtclone c USING room_types t WHERE t.id = c.old_id AND t.hotel_id IS NULL")
    _clone_rows("room_types", "_bi_rtclone")
    _x(
        "UPDATE rooms r SET room_type_id = c.new_id FROM _bi_rtclone c "
        "WHERE r.room_type_id = c.old_id AND r.branch_id = c.branch_id"
    )

    # Mehmonxonaga yoqilgan xona turlari (kalit: hotel_id + room_type_id)
    _drop_unique("hotel_room_types", ["hotel_id", "room_type_id"])
    _x(
        "UPDATE hotel_room_types t SET branch_id = m.branch_id FROM _bi_main m "
        "WHERE t.hotel_id = m.hotel_id"
    )
    _x(
        "INSERT INTO hotel_room_types (hotel_id, branch_id, room_type_id) "
        "SELECT t.hotel_id, e.branch_id, COALESCE(c.new_id, t.room_type_id) "
        "FROM hotel_room_types t JOIN _bi_extra e ON e.hotel_id = t.hotel_id "
        "LEFT JOIN _bi_rtclone c ON c.old_id = t.room_type_id AND c.branch_id = e.branch_id "
        "WHERE t.branch_id IS NOT NULL AND t.branch_id <> e.branch_id"
    )

    _drop_unique("hotel_amenities", ["hotel_id", "amenity_id"])
    _x(
        "UPDATE hotel_amenities t SET branch_id = m.branch_id FROM _bi_main m "
        "WHERE t.hotel_id = m.hotel_id"
    )
    _x(
        "INSERT INTO hotel_amenities (hotel_id, branch_id, amenity_id) "
        "SELECT t.hotel_id, e.branch_id, t.amenity_id "
        "FROM hotel_amenities t JOIN _bi_extra e ON e.hotel_id = t.hotel_id "
        "WHERE t.branch_id IS NOT NULL AND t.branch_id <> e.branch_id"
    )

    _drop_unique("hotel_services", ["hotel_id", "service_id"])
    _fill_main("hotel_services")
    _catalog_map("hotel_services", "_bi_hsclone")
    _clone_rows("hotel_services", "_bi_hsclone")
    _x(
        "UPDATE reservation_services rs SET hotel_service_id = c.new_id FROM _bi_hsclone c "
        "WHERE rs.hotel_service_id = c.old_id AND rs.branch_id = c.branch_id"
    )

    _fill_main("checklist_templates")
    _catalog_map("checklist_templates", "_bi_ctclone")
    _clone_rows("checklist_templates", "_bi_ctclone")

    # Hisob rejasi — har filialga (ota schyot ham o'z nusxasiga)
    _drop_unique("ledgers", ["hotel_id", "code"])
    _fill_main("ledgers")
    _catalog_map("ledgers", "_bi_lclone")
    _clone_rows(
        "ledgers",
        "_bi_lclone",
        {
            "parent_id": (
                "(SELECT pc.new_id FROM _bi_lclone pc WHERE pc.old_id = t.parent_id "
                "AND pc.branch_id = m.branch_id)"
            ),
        },
    )
    _fill_main("journal_entries")
    _x(
        "UPDATE journal_entry_lines l SET branch_id = j.branch_id FROM journal_entries j "
        "WHERE l.journal_entry_id = j.id"
    )
    _fill_main("journal_entry_lines")
    _fill_main("buildings")

    # ---------------------------------------------------------- boshqalar --
    _x(
        "UPDATE staff_messages s SET branch_id = r.branch_id FROM rooms r "
        "WHERE s.room_id = r.id"
    )
    _fill_from_user("staff_messages", "created_by")
    _fill_main("staff_messages")
    _fill_from_user("notifications", "user_id")
    _fill_main("notifications")
    _fill_from_user("audit_logs", "user_id")
    _fill_main("audit_logs")
    _fill_from_user("reports", "generated_by")
    _fill_main("reports")
    _fill_from_user("trusted_devices", "last_user_id")
    _fill_main("trusted_devices")

    # --------------------------------------------- kalitlar va indekslar --
    op.alter_column("hotel_room_types", "branch_id", nullable=False)
    op.alter_column("hotel_amenities", "branch_id", nullable=False)
    op.create_primary_key("pk_hotel_room_types", "hotel_room_types", ["branch_id", "room_type_id"])
    op.create_primary_key("pk_hotel_amenities", "hotel_amenities", ["branch_id", "amenity_id"])
    op.create_index("uq_room_types_branch_name", "room_types", ["hotel_id", "branch_id", "name"], unique=True)
    op.create_index("uq_hotel_services_branch_service", "hotel_services", ["branch_id", "service_id"], unique=True)
    op.create_index("uq_shop_products_branch_name", "shop_products", ["branch_id", "name"], unique=True)
    op.create_index("uq_ledgers_branch_code", "ledgers", ["branch_id", "code"], unique=True)
    for table in NEW_BRANCH_TABLES:
        if table in ("hotel_room_types", "hotel_amenities"):
            continue
        op.create_index(f"ix_{table}_branch_id", table, ["branch_id"])
    for table in MISSING_BRANCH_INDEXES:
        op.create_index(f"ix_{table}_branch_id", table, ["branch_id"])

    for temp in (
        "_bi_main", "_bi_extra", "_bi_gref", "_bi_gclone", "_bi_fpclone", "_bi_pclone",
        "_bi_bclone", "_bi_bmove", "_bi_bwhole", "_bi_rtclone", "_bi_hsclone", "_bi_ctclone", "_bi_lclone",
    ):
        _x(f"DROP TABLE IF EXISTS {temp}")


def downgrade() -> None:
    # Nusxalangan katalog/mehmon yozuvlari qaytarib birlashtirilmaydi —
    # bunday holatda eski unikal kalitlar tiklanmaydi va xato chiqadi
    for table in MISSING_BRANCH_INDEXES:
        op.drop_index(f"ix_{table}_branch_id", table_name=table)
    op.drop_index("uq_ledgers_branch_code", table_name="ledgers")
    op.drop_index("uq_shop_products_branch_name", table_name="shop_products")
    op.drop_index("uq_hotel_services_branch_service", table_name="hotel_services")
    op.drop_index("uq_room_types_branch_name", table_name="room_types")
    op.drop_constraint("pk_hotel_amenities", "hotel_amenities", type_="primary")
    op.drop_constraint("pk_hotel_room_types", "hotel_room_types", type_="primary")
    for table in NEW_BRANCH_TABLES:
        if table not in ("hotel_room_types", "hotel_amenities"):
            op.drop_index(f"ix_{table}_branch_id", table_name=table)
        op.drop_constraint(f"fk_{table}_branch_id", table, type_="foreignkey")
        op.drop_column(table, "branch_id")
    op.create_primary_key("uq_hotel_room_types", "hotel_room_types", ["hotel_id", "room_type_id"])
    op.create_primary_key("uq_hotel_amenities", "hotel_amenities", ["hotel_id", "amenity_id"])
    op.create_index("uq_room_types_hotel_name", "room_types", ["hotel_id", "name"], unique=True)
    op.create_unique_constraint("hotel_services_hotel_id_service_id_key", "hotel_services", ["hotel_id", "service_id"])
    op.create_index("uq_shop_products_hotel_name", "shop_products", ["hotel_id", "name"], unique=True)
    op.create_unique_constraint("ledgers_hotel_id_code_key", "ledgers", ["hotel_id", "code"])
    op.drop_column("branches", "settings")
