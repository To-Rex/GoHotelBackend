"""
Seed default ledger accounts for all hotels.
Run: python -m scripts.seed_ledgers
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.ledger import Ledger

DEFAULT_LEDGERS = [
    ("1000", "Assets", "ASSET", None),
    ("1100", "Cash", "ASSET", "1000"),
    ("1200", "Accounts Receivable", "ASSET", "1000"),
    ("2000", "Liabilities", "LIABILITY", None),
    ("2100", "Accounts Payable", "LIABILITY", "2000"),
    ("3000", "Equity", "EQUITY", None),
    ("4000", "Income", "INCOME", None),
    ("4100", "Room Revenue", "INCOME", "4000"),
    ("4200", "Service Revenue", "INCOME", "4000"),
    ("5000", "Expenses", "EXPENSE", None),
    ("5100", "Salary Expense", "EXPENSE", "5000"),
    ("5200", "Utility Expense", "EXPENSE", "5000"),
    ("5300", "Repair & Maintenance", "EXPENSE", "5000"),
    ("5400", "Housekeeping Supplies", "EXPENSE", "5000"),
]


async def seed():
    async with async_session_factory() as session:
        stmt = select(Hotel)
        result = await session.execute(stmt)
        hotels = result.scalars().all()

        if not hotels:
            print("No hotels found. Create a hotel first.")
            return

        for hotel in hotels:
            print(f"\nHotel: {hotel.name} ({hotel.code})")
            ledger_map = {}

            # First pass: create parent ledgers
            for code, name, ltype, parent_code in DEFAULT_LEDGERS:
                if parent_code is None:
                    stmt = select(Ledger).where(Ledger.hotel_id == hotel.id, Ledger.code == code)
                    r = await session.execute(stmt)
                    existing = r.scalar_one_or_none()
                    if not existing:
                        ledger = Ledger(hotel_id=hotel.id, name=name, code=code, type=ltype)
                        session.add(ledger)
                        await session.flush()
                        ledger_map[code] = ledger.id
                        print(f"  + {code} - {name}")
                    else:
                        ledger_map[code] = existing.id
                        print(f"  = {code} - {name}")

            # Second pass: create child ledgers
            for code, name, ltype, parent_code in DEFAULT_LEDGERS:
                if parent_code is not None:
                    stmt = select(Ledger).where(Ledger.hotel_id == hotel.id, Ledger.code == code)
                    r = await session.execute(stmt)
                    existing = r.scalar_one_or_none()
                    if not existing and parent_code in ledger_map:
                        ledger = Ledger(
                            hotel_id=hotel.id, name=name, code=code,
                            type=ltype, parent_id=ledger_map[parent_code]
                        )
                        session.add(ledger)
                        print(f"  + {code} - {name} (parent: {parent_code})")
                    elif existing:
                        print(f"  = {code} - {name}")

            await session.commit()

    print("\nLedger seeding complete.")


if __name__ == "__main__":
    asyncio.run(seed())
