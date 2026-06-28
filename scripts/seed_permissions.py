"""
Seed default permissions into the database.
Run: python -m scripts.seed_permissions
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models.permission import Permission

PERMISSIONS = [
    # Reservation module
    ("reservation.create", "Create Reservation", "reservation", "Create new reservations"),
    ("reservation.update", "Update Reservation", "reservation", "Update existing reservations"),
    ("reservation.cancel", "Cancel Reservation", "reservation", "Cancel reservations"),
    ("reservation.view", "View Reservations", "reservation", "View reservation list and details"),
    # Guest module
    ("guest.create", "Register Guest", "guest", "Register new guests"),
    ("guest.update", "Update Guest", "guest", "Update guest profiles"),
    ("guest.view", "View Guests", "guest", "View guest details and history"),
    ("guest.checkin", "Check In Guest", "guest", "Perform guest check-in"),
    ("guest.checkout", "Check Out Guest", "guest", "Perform guest check-out"),
    # Room module
    ("room.view", "View Rooms", "room", "View room list and details"),
    ("room.status.update", "Update Room Status", "room", "Change room status"),
    ("room.manage", "Manage Rooms", "room", "Create and edit rooms"),
    # Housekeeping module
    ("housekeeping.task.create", "Create Task", "housekeeping", "Create cleaning/maintenance tasks"),
    ("housekeeping.task.assign", "Assign Task", "housekeeping", "Assign tasks to employees"),
    ("housekeeping.task.update", "Update Task Status", "housekeeping", "Update task status"),
    ("housekeeping.cleaning.start", "Start Cleaning", "housekeeping", "Start cleaning tasks"),
    ("housekeeping.cleaning.complete", "Complete Cleaning", "housekeeping", "Complete cleaning tasks"),
    # Finance module
    ("finance.view", "View Finance", "finance", "View financial data"),
    ("finance.invoice.create", "Create Invoice", "finance", "Create invoices"),
    ("finance.invoice.manage", "Manage Invoices", "finance", "Manage invoice lifecycle"),
    ("finance.payment.create", "Record Payment", "finance", "Record payments"),
    ("finance.journal.create", "Create Journal Entry", "finance", "Create journal entries"),
    # Report module
    ("report.view", "View Reports", "report", "View generated reports"),
    ("report.export", "Export Reports", "report", "Export reports"),
    # Employee module
    ("employee.view", "View Employees", "employee", "View employee list"),
    ("employee.create", "Create Employee", "employee", "Create new employees"),
    ("employee.update", "Update Employee", "employee", "Update employee details"),
    ("employee.manage", "Manage Permissions", "employee", "Manage employee permissions"),
    # Service module
    ("service.view", "View Services", "service", "View hotel services"),
    ("service.manage", "Manage Services", "service", "Enable/disable hotel services"),
]


async def seed():
    async with async_session_factory() as session:
        for code, name, module, description in PERMISSIONS:
            stmt = select(Permission).where(Permission.code == code)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()
            if not existing:
                perm = Permission(code=code, name=name, module=module, description=description)
                session.add(perm)
                print(f"  + Created: {code}")
            else:
                print(f"  = Exists: {code}")
        await session.commit()
    print(f"\nSeeded {len(PERMISSIONS)} permissions.")


if __name__ == "__main__":
    asyncio.run(seed())
