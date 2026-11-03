"""UTC bo'yicha yozilib qolgan hujjat sanalarini mahalliy kunga keltirish.

2026-11-02 dagi yangilanishgacha server hujjat sanalarini `date.today()`
bilan yozardi, server esa UTC da ishlaydi. Toshkent vaqti bilan 00:00–05:00
oralig'ida bu hali KECHAGI sana: tunda olingan to'lov, hisob-faktura,
jarima, xarajat va tozalash vazifasi bir kun orqaga yozilib qolgan.
Oqibati ko'zga tashlanadigan edi: Moliya sahifasi to'lovni sana ustuni
bo'yicha "kecha"ga qo'shadi, smena hisobi esa vaqt belgisi bo'yicha uni
bugungi smenaga kiritadi — ikki sahifa bir-biriga mos kelmasdi (Xorazim
filiali, 2-noyabr: 2 000 000 so'mlik farq aynan shundan).

Tuzatish imzosi aniq: ustun = created_at ning UTC kuni VA ≠ created_at ning
mahalliy kuni. Qo'lda boshqa sana kiritilgan yozuv bu imzoga tushmaydi
(u UTC kuniga teng bo'lmaydi), shuning uchun faqat xato yozuvlar o'zgaradi.
Hosilaviy ustun (hisob-fakturadagi `due_date` = sana + 7 kun) ham faqat
aynan eski sana + siljish bo'lsa birga suriladi.

Alembic migratsiyasi (f3b8d1c7e9a2) shu funksiyalarni ishlatadi. Mantiq
SQLAlchemy Core da, dialektga bog'liq emas — testlar SQLite da yuradi.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class DateRepair:
    table: str
    column: str
    created_column: str = "created_at"
    # Asosiy sanadan hosil bo'lgan ustunlar: (ustun, siljish)
    derived: tuple[tuple[str, timedelta], ...] = ()


# Yangilanishgacha `date.today()` bilan yozilgan barcha hujjat sanalari
REPAIRS: tuple[DateRepair, ...] = (
    DateRepair("payments", "payment_date"),
    DateRepair("invoices", "invoice_date", derived=(("due_date", timedelta(days=7)),)),
    DateRepair("expenses", "expense_date"),
    DateRepair("journal_entries", "entry_date"),
    DateRepair("reservation_penalties", "penalty_date"),
    DateRepair("housekeeping_tasks", "scheduled_date"),
    DateRepair("reservation_services", "service_date"),
)


def _as_utc(moment: datetime) -> datetime:
    """SQLite vaqt belgisini zonasiz qaytaradi — u UTC deb qabul qilinadi."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def repair_utc_dates(
    conn: Connection,
    repair: DateRepair,
    offset_minutes: int,
    before: datetime | None = None,
) -> int:
    """Bitta jadvalni tuzatadi; o'zgartirilgan qatorlar sonini qaytaradi.

    `before` — faqat shu vaqtdan oldin yaratilgan yozuvlar ko'riladi
    (yangilanish deploy qilingan payt): undan keyin imzo faqat ataylab
    kiritilgan sanada uchrashi mumkin.
    """
    columns = [
        sa.column("id"),
        sa.column(repair.created_column, sa.DateTime(timezone=True)),
        sa.column(repair.column, sa.Date),
        *(sa.column(name, sa.Date) for name, _ in repair.derived),
    ]
    t = sa.table(repair.table, *columns)
    created_col = t.c[repair.created_column]
    stmt = sa.select(t).where(t.c[repair.column].is_not(None), created_col.is_not(None))
    if before is not None:
        stmt = stmt.where(created_col < before)

    offset = timedelta(minutes=offset_minutes)
    fixed = 0
    for row in conn.execute(stmt).mappings().all():
        created = _as_utc(row[repair.created_column])
        utc_day = created.date()
        local_day = (created + offset).date()
        # Kunlar bir xil — xato bo'lishi mumkin emas; sana UTC kuniga teng
        # emas — qo'lda kiritilgan yoki allaqachon to'g'ri
        if local_day == utc_day or row[repair.column] != utc_day:
            continue
        values = {repair.column: local_day}
        for name, shift in repair.derived:
            if row[name] == utc_day + shift:
                values[name] = local_day + shift
        conn.execute(sa.update(t).where(t.c.id == row["id"]).values(**values))
        fixed += 1
    return fixed


def repair_all_utc_dates(
    conn: Connection,
    offset_minutes: int,
    before: datetime | None = None,
    repairs: tuple[DateRepair, ...] = REPAIRS,
) -> dict[str, int]:
    """Barcha jadvallar; natija — "jadval.ustun": tuzatilgan qatorlar soni."""
    return {
        f"{r.table}.{r.column}": repair_utc_dates(conn, r, offset_minutes, before)
        for r in repairs
    }
