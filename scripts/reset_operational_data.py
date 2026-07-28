"""
Operatsion ma'lumotlarni tozalash — tizimni "yangidek" ishga tushirish uchun.

O'CHIRILADI: bronlar, hisob-fakturalar, to'lovlar, jurnal yozuvlari, mehmonlar,
xo'jalik vazifalari (chek-listlar, muammolar bilan), bildirishnomalar, audit
loglari, hisobotlar, xona holati tarixi, mehmon/vazifa fayllari.

SAQLANADI: xodimlar (users) va sessiyalari, ruxsatlar (permissions,
user_permissions), mehmonxona tuzilmasi — hotels, branches, buildings, floors,
rooms (holati AVAILABLE ga qaytariladi), room_types, amenities, services,
hotel_services, ledgers (hisob rejasi) va mehmonxonaga tegishli fayllar.

Ishga tushirish (backend papkasidan, .env dagi DATABASE_URL ishlatiladi):
  python -m scripts.reset_operational_data          # dry-run: faqat sonlar
  python -m scripts.reset_operational_data --yes    # haqiqiy tozalash
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import bindparam, text

from app.core.config import settings
from app.infrastructure.database.session import async_session_factory

# O'chirish tartibi muhim: avval bolalar (FK), keyin ota jadvallar
CLEAR_TABLES = [
    "checklist_items",        # -> housekeeping_tasks (CASCADE)
    "problems",               # -> housekeeping_tasks (SET NULL)
    "housekeeping_tasks",     # -> reservations (SET NULL), rooms
    "reservation_services",   # -> reservations (CASCADE)
    "payments",               # -> invoices (RESTRICT)
    "invoice_line_items",     # -> invoices (CASCADE)
    "invoice_items",          # -> invoices (CASCADE)
    "journal_entry_lines",    # -> journal_entries (CASCADE)
    "journal_entries",
    "invoices",               # -> reservations, guests (RESTRICT)
    "reservations",           # -> guests (RESTRICT), rooms
    "guests",
    "notifications",
    "audit_logs",
    "reports",
    "room_status_history",
    "expenses",
]

# Faqat operatsion fayllar o'chiriladi; mehmonxona fayllari ('Hotel') qoladi
FILE_ENTITY_TYPES = ["guest", "task", "task_report", "problem"]


async def main(apply: bool) -> None:
    db_target = settings.DATABASE_URL.split("@")[-1]
    print(f"Baza: {db_target}")
    print(f"Rejim: {'HAQIQIY TOZALASH' if apply else 'DRY-RUN (hech narsa oʻchirilmaydi)'}\n")

    files_stmt = text(
        "SELECT count(*) FROM file_attachments WHERE entity_type IN :et"
    ).bindparams(bindparam("et", expanding=True))

    async with async_session_factory() as session:
        total = 0
        for t in CLEAR_TABLES:
            n = (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar() or 0
            total += n
            print(f"  {t:24s} {n}")
        n_files = (await session.execute(files_stmt, {"et": FILE_ENTITY_TYPES})).scalar() or 0
        total += n_files
        print(f"  {'file_attachments*':24s} {n_files}   (*faqat guest/task/report/problem)")
        print(f"\n  JAMI o'chiriladigan yozuvlar: {total}")

        # Saqlanadigan asosiy jadvallar haqida ma'lumot
        for t in ("users", "permissions", "user_permissions", "rooms"):
            n = (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar() or 0
            print(f"  saqlanadi -> {t}: {n}")

        if not apply:
            print("\nDRY-RUN yakunlandi. Haqiqiy tozalash uchun: python -m scripts.reset_operational_data --yes")
            return

        print("\nTozalash boshlandi...")
        for t in CLEAR_TABLES:
            result = await session.execute(text(f"DELETE FROM {t}"))
            print(f"  o'chirildi: {t} ({result.rowcount})")
        del_files_stmt = text(
            "DELETE FROM file_attachments WHERE entity_type IN :et"
        ).bindparams(bindparam("et", expanding=True))
        result = await session.execute(del_files_stmt, {"et": FILE_ENTITY_TYPES})
        print(f"  o'chirildi: file_attachments ({result.rowcount})")

        # Xonalar yangidek — hammasi bo'sh holatga qaytadi
        result = await session.execute(
            text("UPDATE rooms SET current_status = 'AVAILABLE' WHERE current_status <> 'AVAILABLE'")
        )
        print(f"  xonalar AVAILABLE holatiga qaytarildi: {result.rowcount}")

        await session.commit()
        print("\nTOZALASH MUVAFFAQIYATLI YAKUNLANDI. Tizim yangidek ishga tayyor.")


if __name__ == "__main__":
    asyncio.run(main("--yes" in sys.argv))
