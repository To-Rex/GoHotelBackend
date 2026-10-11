"""Katta hajmga tayyorlik: yetishmayotgan so'rov indekslari

Bazada avvalgi migratsiyalardan qolgan `idx_*` indekslar bor (masalan
`idx_audit_logs_hotel_id`, `idx_reservations_dates`, `idx_payments_invoice_id`).
Ular bilan TAQQOSLAB, faqat HAQIQIY YETISHMAYOTGAN indekslar qo'shiladi —
dublikat indeks yaratilmaydi (har ortiqcha indeks yozishni sekinlashtiradi).

Nima uchun kerak: `reservations`, `housekeeping_tasks`, `payments` kabi
jadvalarning asosiy so'rov yo'llari (smena kassasi, hisobotlar, doska,
"mening vazifalarim") kompozit indekssiz edi. Mavjud funksionallik
o'zgarmaydi — faqat o'qish tezligi. Ma'lumot o'chirilmaydi.

pg_trgm kengaytmasi serverda mavjud bo'lmasa trigram indekslar o'tkazib
yuboriladi (mehmon qidiruvi ILIKE uchun).

Revision ID: d2e3f4a5b6c7
Revises: d1e2f3a4b5c7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "d1e2f3a4b5c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (jadval, indeks nomi, ustunlar) — mavjudlaridan farqli, yangi indekslar
INDEXES: list[tuple[str, str, list[str]]] = [
    # Bronlar: filial bo'yicha holat va ro'yxat tartibi, mavjudlik, "Bronlarim"
    ("reservations", "ix_reservations_hotel_branch_status", ["hotel_id", "branch_id", "status"]),
    ("reservations", "ix_reservations_hotel_branch_created", ["hotel_id", "branch_id", "created_at"]),
    ("reservations", "ix_reservations_room_checkin", ["room_id", "check_in_date"]),
    ("reservations", "ix_reservations_created_by", ["created_by", "created_at"]),
    # To'lovlar: hisobotlar, smena kassasi
    ("payments", "ix_payments_hotel_payment_date", ["hotel_id", "payment_date"]),
    ("payments", "ix_payments_created_by_created_at", ["created_by", "created_at"]),
    # Xarajatlar: hisobotlar, smena kassasi
    ("expenses", "ix_expenses_hotel_expense_date", ["hotel_id", "expense_date"]),
    ("expenses", "ix_expenses_created_by_created_at", ["created_by", "created_at"]),
    # Do'kon to'lovlari: smena kassasi
    ("shop_sale_payments", "ix_shop_sale_payments_created_by_paid_at", ["created_by", "paid_at"]),
    # Xo'jalik vazifalari: doska, "mening vazifalarim", avtomatik chiqish
    ("housekeeping_tasks", "ix_housekeeping_tasks_hotel_branch_status", ["hotel_id", "branch_id", "status"]),
    ("housekeeping_tasks", "ix_housekeeping_tasks_hotel_branch_created", ["hotel_id", "branch_id", "created_at"]),
    ("housekeeping_tasks", "ix_housekeeping_tasks_assigned_status", ["assigned_to", "status"]),
    ("housekeeping_tasks", "ix_housekeeping_tasks_reservation", ["reservation_id"]),
    # Bildirishnomalar: ro'yxatlar (o'qilmaganlar uchun mavjud idx bor)
    ("notifications", "ix_notifications_user_created", ["user_id", "created_at"]),
    ("notifications", "ix_notifications_hotel_created", ["hotel_id", "created_at"]),
    # Xabarlar taxtasi
    ("staff_messages", "ix_staff_messages_hotel_created", ["hotel_id", "created_at"]),
    # Vazifa cheklisti
    ("checklist_items", "ix_checklist_items_task_id", ["task_id"]),
    # Hisob-faktura qatorlari (invoice_line_items uchun mavjud idx bor)
    ("invoice_items", "ix_invoice_items_invoice_id", ["invoice_id"]),
    # Fayllar: hotel bo'yicha (egasi bo'yicha mavjud idx bor)
    ("file_attachments", "ix_file_attachments_hotel_created", ["hotel_id", "created_at"]),
]

#: pg_trgm kengaytmasi mavjud bo'lsa yaratiladigan trigram indekslari
#: (mehmon qidiruvi ILIKE '%...%' — btree bunday qidiruvga yaramaydi)
TRIGRAM_INDEXES: list[tuple[str, str]] = [
    ("ix_guests_trgm_first_name", "first_name"),
    ("ix_guests_trgm_last_name", "last_name"),
    ("ix_guests_trgm_phone", "phone"),
    ("ix_guests_trgm_email", "email"),
    ("ix_guests_trgm_passport", "passport_number"),
]


def _trigram_available() -> bool:
    """Serverda pg_trgm kengaytmasi o'rnatish uchun mavjudmi.

    Offline rejimda (SQL generatsiya) yoki tekshirib bo'lmasa — False:
    migratsiya hech qachon shu sababdan yiqilmasin.
    """
    try:
        bind = op.get_bind()
        if bind is None:
            return False
        row = bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
        ).first()
        return row is not None
    except Exception:  # noqa: BLE001 — offline/mock rejim: trigram o'tkazib yuboriladi
        return False


def upgrade() -> None:
    for table, name, columns in INDEXES:
        op.create_index(name, table, columns)

    # Bron hamrohlari JSONB ichida qidiriladi (@> containment) — GIN indeks
    op.create_index(
        "ix_reservations_companions_gin",
        "reservations",
        ["companions"],
        postgresql_using="gin",
        postgresql_ops={"companions": "jsonb_path_ops"},
    )

    # Mehmon qidiruvi uchun trigram. Kengaytma bo'lmasa o'tkazib yuboriladi —
    # migratsiya hech qachon yiqilmasin.
    if _trigram_available():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for name, column in TRIGRAM_INDEXES:
            op.execute(
                f"CREATE INDEX IF NOT EXISTS {name} ON guests USING gin ({column} gin_trgm_ops)"
            )


def downgrade() -> None:
    # Trigram indekslar shartli yaratilgan — shartli o'chiriladi
    for name, _ in TRIGRAM_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")

    op.drop_index("ix_reservations_companions_gin", table_name="reservations")

    for table, name, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
