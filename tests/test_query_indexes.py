"""Katta hajmga tayyorlik: indekslar model va migratsiyada MOS bo'lishi shart.

Indekslar modelda e'lon qilinadi (SQLAlchemy metadata) va migratsiya orqali
bazaga qo'shiladi. Ikkalasi ajralib ketsa (biri yangilanib, ikkinchisi
emas) — yangi muhitda indeks umuman yaratilmaydi yoki kelajakdagi
autogenerate chalkashadi. Shu sababli bu test to'plamlar bir xilligini,
migratsiyaning zanjiri va revision nomini qulflaydi.
"""
import importlib.util
from pathlib import Path

from app.infrastructure.database.models import *  # noqa: F401,F403 — metadata to'lishi uchun
from app.infrastructure.database.models.base import Base

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "d2e3f4a5b6c7_query_indexes.py"
PREVIOUS_HEAD = "d1e2f3a4b5c7"

#: Migratsiyadan tashqari qo'lda qo'shilgan maxsus indekslar (jadval, nom)
CUSTOM_INDEXES = [
    ("reservations", "ix_reservations_companions_gin"),
]


def _load_migration():
    spec = importlib.util.spec_from_file_location("query_indexes_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index_names(table: str) -> set[str]:
    return {ix.name for ix in Base.metadata.tables[table].indexes}


def test_migration_file_matches_revision_name():
    migration = _load_migration()
    assert MIGRATION_PATH.name.startswith(migration.revision)
    assert migration.down_revision == PREVIOUS_HEAD


def test_every_migration_index_exists_in_models_with_same_columns():
    migration = _load_migration()
    missing: list[str] = []
    for table, name, columns in migration.INDEXES:
        index = next(
            (ix for ix in Base.metadata.tables[table].indexes if ix.name == name), None
        )
        if index is None:
            missing.append(f"{table}.{name}")
            continue
        assert [c.name for c in index.columns] == columns, (
            f"{name}: modelda ustunlar boshqacha"
        )
    assert not missing, f"Modelda e'lon qilinmagan indekslar: {missing}"


def test_custom_and_trigram_indexes_are_declared_in_models():
    migration = _load_migration()
    for table, name in CUSTOM_INDEXES:
        assert name in _index_names(table), f"{table}.{name} modelda yo'q"
    for name, _column in migration.TRIGRAM_INDEXES:
        assert name in _index_names("guests"), f"guests.{name} modelda yo'q"


def test_growing_tables_keep_their_hot_path_indexes():
    """Eng muhim indekslar tasodifan o'chib ketmasin.

    Ro'yxat ATAYLAB qisqa: bu yerda faqat katta hajmda birinchi bo'lib
    seziladigan yo'llar qulflanadi (bronlar, to'lovlar, vazifalar, smena
    kassasi). Bazada avvaldan bor `idx_*` indekslar (audit, invoice lines,
    room status history) bu ro'yxatga kirmaydi — ular migratsiyalarda.
    """
    required = {
        "reservations": {
            "ix_reservations_hotel_branch_status",
            "ix_reservations_hotel_branch_created",
            "ix_reservations_room_checkin",
            "ix_reservations_companions_gin",
        },
        "payments": {
            "ix_payments_hotel_payment_date",
            "ix_payments_created_by_created_at",
        },
        "housekeeping_tasks": {
            "ix_housekeeping_tasks_hotel_branch_status",
            "ix_housekeeping_tasks_assigned_status",
            "ix_housekeeping_tasks_reservation",
        },
        "notifications": {
            "ix_notifications_user_created",
            "ix_notifications_hotel_created",
        },
        "shop_sale_payments": {"ix_shop_sale_payments_created_by_paid_at"},
        "expenses": {"ix_expenses_hotel_expense_date", "ix_expenses_created_by_created_at"},
    }
    for table, names in required.items():
        assert names <= _index_names(table), f"{table}: {names - _index_names(table)}"
