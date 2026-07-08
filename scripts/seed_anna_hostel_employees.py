"""
Anna Hostel (code=123) — Main Branch filiali uchun xodimlar:
  - Admin (hotel administrator)
  - Manager (menejer)
  - Reseptionist (qabulxona xodimi)

Usage: python -m scripts.seed_anna_hostel_employees
"""
import asyncio
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models import (
    Hotel, Branch, User, Permission, UserPermission,
)
from app.infrastructure.auth.password import hash_password
from app.domain.enums import UserType, UserStatus


PERMISSIONS = [
    ("reservation.create", "Create Reservation", "reservation"),
    ("reservation.update", "Update Reservation", "reservation"),
    ("reservation.cancel", "Cancel Reservation", "reservation"),
    ("reservation.view", "View Reservations", "reservation"),
    ("guest.create", "Register Guest", "guest"),
    ("guest.update", "Update Guest", "guest"),
    ("guest.view", "View Guests", "guest"),
    ("guest.checkin", "Check In Guest", "guest"),
    ("guest.checkout", "Check Out Guest", "guest"),
    ("guest.delete", "Delete Guest", "guest"),
    ("room.view", "View Rooms", "room"),
    ("room.status.update", "Update Room Status", "room"),
    ("room.manage", "Manage Rooms", "room"),
    ("room.create", "Create Room", "room"),
    ("room.update", "Update Room", "room"),
    ("room.delete", "Delete Room", "room"),
    ("housekeeping.task.create", "Create Task", "housekeeping"),
    ("housekeeping.task.assign", "Assign Task", "housekeeping"),
    ("housekeeping.task.update", "Update Task Status", "housekeeping"),
    ("housekeeping.cleaning.start", "Start Cleaning", "housekeeping"),
    ("housekeeping.cleaning.complete", "Complete Cleaning", "housekeeping"),
    ("finance.view", "View Finance", "finance"),
    ("finance.invoice.create", "Create Invoice", "finance"),
    ("finance.invoice.manage", "Manage Invoices", "finance"),
    ("finance.payment.create", "Record Payment", "finance"),
    ("finance.journal.create", "Create Journal Entry", "finance"),
    ("report.view", "View Reports", "report"),
    ("report.export", "Export Reports", "report"),
    ("report.generate", "Generate Report", "report"),
    ("employee.view", "View Employees", "employee"),
    ("employee.create", "Create Employee", "employee"),
    ("employee.update", "Update Employee", "employee"),
    ("employee.manage", "Manage Permissions", "employee"),
    ("employee.delete", "Delete Employee", "employee"),
    ("service.view", "View Services", "service"),
    ("service.manage", "Manage Services", "service"),
    ("service.create", "Create Service", "service"),
    ("service.update", "Update Service", "service"),
    ("hotel.create", "Create Hotel", "hotel"),
    ("hotel.update", "Update Hotel", "hotel"),
    ("hotel.delete", "Delete Hotel", "hotel"),
    ("branch.create", "Create Branch", "branch"),
    ("branch.update", "Update Branch", "branch"),
    ("floor.create", "Create Floor", "floor"),
    ("floor.update", "Update Floor", "floor"),
    ("floor.delete", "Delete Floor", "floor"),
    ("room_type.create", "Create Room Type", "room"),
    ("room_type.update", "Update Room Type", "room"),
    ("room_type.delete", "Delete Room Type", "room"),
    ("permission.view", "View Permissions", "employee"),
    ("permission.assign", "Assign Permissions", "employee"),
    ("audit_log.view", "View Audit Logs", "audit"),
    ("file.upload", "Upload File", "file"),
    ("file.delete", "Delete File", "file"),
    ("hotel_service.manage", "Manage Hotel Services", "service"),
]

RECEPTION_CODES = [
    "reservation.create", "reservation.update", "reservation.cancel",
    "reservation.view", "guest.create", "guest.update", "guest.view",
    "guest.checkin", "guest.checkout", "guest.delete",
    "room.view", "room.status.update",
    "finance.view", "finance.invoice.create", "finance.payment.create",
    "report.view", "service.view", "file.upload",
    "hotel_service.manage",
]
ALL_CODES = [c for c, _, _ in PERMISSIONS]


async def seed():
    async with async_session_factory() as session:

        # ============================================
        # 1. SUPERADMIN (granted_by uchun kerak)
        # ============================================
        r = await session.execute(select(User).where(User.username == "admin"))
        superadmin = r.scalar_one_or_none()
        if superadmin is None:
            superadmin = User(
                user_type=UserType.SUPER_ADMIN.value,
                username="admin",
                password_hash=hash_password("admin123"),
                first_name="Super",
                last_name="Admin",
                status=UserStatus.ACTIVE.value,
            )
            session.add(superadmin)
            await session.flush()
            print("✓ SuperAdmin yaratildi: admin / admin123")
        else:
            print("✓ SuperAdmin mavjud (admin / admin123)")

        # ============================================
        # 2. PERMISSIONS (yo'q bo'lsa yaratamiz)
        # ============================================
        perm_map: dict[str, Permission] = {}
        for code, name, module in PERMISSIONS:
            r = await session.execute(select(Permission).where(Permission.code == code))
            p = r.scalar_one_or_none()
            if p is None:
                p = Permission(code=code, name=name, module=module)
                session.add(p)
            perm_map[code] = p
        await session.flush()
        print(f"✓ Permissions: {len(perm_map)} ta")

        # ============================================
        # 3. Anna Hostel (code=123)
        # ============================================
        r = await session.execute(select(Hotel).where(Hotel.code == "123"))
        hotel = r.scalar_one_or_none()
        if hotel is None:
            print("❌ Anna Hostel (code=123) topilmadi!")
            return
        print(f"✓ Mehmonxona: {hotel.name} (code={hotel.code})")
        hotel_id = hotel.id

        # ============================================
        # 4. MAIN BRANCH
        # ============================================
        r = await session.execute(
            select(Branch).where(
                Branch.hotel_id == hotel_id,
                Branch.is_main_branch == True,
            )
        )
        main_branch = r.scalar_one_or_none()
        if main_branch is None:
            print("❌ Main Branch topilmadi!")
            return
        print(f"✓ Filial: {main_branch.name} (code={main_branch.code})")
        branch_id = main_branch.id

        # ============================================
        # 5. XODIMLARNI YARATISH
        # ============================================
        employees_to_create = [
            {
                "user_type": UserType.ADMIN.value,
                "username": "anna_admin",
                "password": "anna123",
                "first_name": "Anna",
                "last_name": "Administrator",
                "email": "admin@annahostel.uz",
                "phone": "+998901110001",
                "role_label": "Admin",
                "perm_codes": ALL_CODES,
            },
            {
                "user_type": UserType.EMPLOYEE.value,
                "username": "anna_manager",
                "password": "anna123",
                "first_name": "Manager",
                "last_name": "Anna Hostel",
                "email": "manager@annahostel.uz",
                "phone": "+998901110002",
                "role_label": "Manager",
                "perm_codes": ALL_CODES,
            },
            {
                "user_type": UserType.EMPLOYEE.value,
                "username": "anna_reception",
                "password": "anna123",
                "first_name": "Receptionist",
                "last_name": "Anna Hostel",
                "email": "reception@annahostel.uz",
                "phone": "+998901110003",
                "role_label": "Reseptionist",
                "perm_codes": RECEPTION_CODES,
            },
        ]

        for emp_data in employees_to_create:
            r = await session.execute(
                select(User).where(User.username == emp_data["username"])
            )
            existing = r.scalar_one_or_none()
            if existing:
                existing_up = set()
                r2 = await session.execute(
                    select(UserPermission.permission_id).where(UserPermission.user_id == existing.id)
                )
                for pid in r2.scalars().all():
                    existing_up.add(pid)

                new_perm_count = 0
                for code in emp_data["perm_codes"]:
                    p = perm_map[code]
                    if p.id not in existing_up:
                        session.add(UserPermission(
                            user_id=existing.id,
                            permission_id=p.id,
                            hotel_id=hotel_id,
                            granted_by=superadmin.id,
                        ))
                        new_perm_count += 1

                if new_perm_count > 0:
                    print(f"  ✓ {emp_data['role_label']} ga {new_perm_count} ta yangi permission qo'shildi: {emp_data['username']}")
                else:
                    print(f"  ⚠ {emp_data['role_label']} allaqachon mavjud (barcha permissionlar bor): {emp_data['username']}")
                continue

            user = User(
                user_type=emp_data["user_type"],
                hotel_id=hotel_id,
                branch_id=branch_id,
                username=emp_data["username"],
                password_hash=hash_password(emp_data["password"]),
                first_name=emp_data["first_name"],
                last_name=emp_data["last_name"],
                email=emp_data["email"],
                phone=emp_data["phone"],
                status=UserStatus.ACTIVE.value,
                hire_date=date.today(),
            )
            session.add(user)
            await session.flush()

            for code in emp_data["perm_codes"]:
                p = perm_map[code]
                session.add(UserPermission(
                    user_id=user.id,
                    permission_id=p.id,
                    hotel_id=hotel_id,
                    granted_by=superadmin.id,
                ))

            print(f"  ✓ {emp_data['role_label']}: {emp_data['username']} / {emp_data['password']}")

        # ============================================
        await session.commit()

    print(f"""
{'=' * 50}
  ✅ BAJARILDI
{'=' * 50}
  Mehmonxona:  {hotel.name} (code={hotel.code})
  Filial:      {main_branch.name} (code={main_branch.code})

  Admin:       anna_admin / anna123
  Manager:     anna_manager / anna123
  Reseptionist: anna_reception / anna123
""")


if __name__ == "__main__":
    asyncio.run(seed())
