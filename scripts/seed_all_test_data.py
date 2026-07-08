"""
Comprehensive test data seeder — direct database insert via SQLAlchemy.
Creates: hotel, branches, floors, room types, rooms, amenities,
         employees, permissions, guests, reservations, services, housekeeping.

Usage: python -m scripts.seed_all_test_data
"""
import asyncio
import sys
import os
import random
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models import (
    Hotel, Branch, Floor, RoomType, HotelRoomType, Room,
    Amenity, HotelAmenity, RoomAmenity,
    User, Permission, UserPermission,
    Guest, Reservation, Service, HotelService,
    HousekeepingTask,
)
from app.infrastructure.auth.password import hash_password
from app.domain.enums import (
    UserType, UserStatus,
    RoomStatus, ReservationStatus,
    TaskType, TaskPriority, TaskStatus,
    BookingType, PaymentStatus,
)


# ============================================================
# HELPERS
# ============================================================
def _rin(n: int) -> str:
    """Random integer-as-string for reservation numbers."""
    return f"{random.randint(100000, 999999)}"


def _reservation_number() -> str:
    return f"RES-{datetime.now().strftime('%Y%m%d')}-{_rin(6)}"


# ============================================================
# MAIN SEEDER
# ============================================================
async def seed():
    async with async_session_factory() as session:
        # ------------------------------------------------
        # 1. SUPERADMIN (skip if exists)
        # ------------------------------------------------
        stmt = select(User).where(User.username == "admin")
        r = await session.execute(stmt)
        admin = r.scalar_one_or_none()
        if admin is None:
            admin = User(
                user_type=UserType.SUPER_ADMIN.value,
                username="admin",
                password_hash=hash_password("admin123"),
                first_name="Super",
                last_name="Admin",
                status=UserStatus.ACTIVE.value,
            )
            session.add(admin)
            await session.flush()
            print("✓ SuperAdmin: admin / admin123")
        else:
            print(f"✓ SuperAdmin already exists (id={str(admin.id)[:8]}...)")

        # ------------------------------------------------
        # 2. PERMISSIONS
        # ------------------------------------------------
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
            ("room.view", "View Rooms", "room"),
            ("room.status.update", "Update Room Status", "room"),
            ("room.manage", "Manage Rooms", "room"),
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
            ("employee.view", "View Employees", "employee"),
            ("employee.create", "Create Employee", "employee"),
            ("employee.update", "Update Employee", "employee"),
            ("employee.manage", "Manage Permissions", "employee"),
            ("service.view", "View Services", "service"),
            ("service.manage", "Manage Services", "service"),
            ("service.create", "Create Service", "service"),
            ("service.update", "Update Service", "service"),
            ("guest.delete", "Delete Guest", "guest"),
            ("room.create", "Create Room", "room"),
            ("room.update", "Update Room", "room"),
            ("room.delete", "Delete Room", "room"),
            ("employee.delete", "Delete Employee", "employee"),
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
            ("report.generate", "Generate Report", "report"),
            ("permission.view", "View Permissions", "employee"),
            ("permission.assign", "Assign Permissions", "employee"),
            ("audit_log.view", "View Audit Logs", "audit"),
            ("file.upload", "Upload File", "file"),
            ("file.delete", "Delete File", "file"),
            ("hotel_service.manage", "Manage Hotel Services", "service"),
        ]
        perm_map: dict[str, Permission] = {}
        for code, name, module in PERMISSIONS:
            stmt = select(Permission).where(Permission.code == code)
            r = await session.execute(stmt)
            p = r.scalar_one_or_none()
            if p is None:
                p = Permission(code=code, name=name, module=module)
                session.add(p)
            perm_map[code] = p
        await session.flush()
        print(f"✓ Permissions: {len(perm_map)}")

        # ------------------------------------------------
        # 3. AMENITIES (Qulayliklar)
        # ------------------------------------------------
        amenity_data: list[tuple[str, str | None]] = [
            ("Wi-Fi (bepul)", "wifi"),
            ("Televizor", "tv"),
            ("Konditsioner", "snowflake"),
            ("Mini bar", "wine"),
            ("Jakkuzi", "bath"),
            ("Seans zali", "film"),
            ("Shaxsiy hovuz", "pool"),
            ("Bolalar maydonchasi", "child"),
            ("24/7 xona xizmati", "concierge"),
            ("Avtoturargoh", "car"),
            ("Fitnes zal", "dumbbell"),
            ("SPA", "spa"),
            ("Restoran", "utensils"),
            ("Bar", "martini"),
            ("Biznes markaz", "briefcase"),
            ("Kir yuvish xizmati", "shirt"),
            ("Aeroport transfer", "plane"),
            ("Xavfsizlik seyfi", "safe"),
            ("Soch fen", "wind"),
            ("Choy/qahva to'plami", "coffee"),
        ]
        amenity_map: dict[str, Amenity] = {}
        for name, icon in amenity_data:
            stmt = select(Amenity).where(Amenity.name == name)
            r = await session.execute(stmt)
            a = r.scalar_one_or_none()
            if a is None:
                a = Amenity(name=name, icon=icon, is_active=True)
                session.add(a)
            amenity_map[name] = a
        await session.flush()
        print(f"✓ Amenities: {len(amenity_map)}")

        # ------------------------------------------------
        # 4. HOTEL
        # ------------------------------------------------
        stmt = select(Hotel).where(Hotel.code == "GPH001")
        r = await session.execute(stmt)
        hotel = r.scalar_one_or_none()
        if hotel is None:
            hotel = Hotel(
                name="Grand Plaza Hotel",
                code="GPH001",
                stars=5,
                phone="+998901234567",
                email="info@grandplaza.uz",
                address_line1="Mustaqillik maydoni, 5",
                city="Toshkent",
                state="Toshkent viloyati",
                country="O'zbekistan",
                postal_code="100000",
                status="ACTIVE",
            )
            session.add(hotel)
            await session.flush()
            print(f"✓ Hotel: {hotel.name}")
        else:
            print(f"✓ Hotel already exists: {hotel.name}")
        hotel_id = hotel.id

        # Link all amenities to hotel
        existing_ha = set()
        stmt = select(HotelAmenity).where(HotelAmenity.hotel_id == hotel_id)
        r = await session.execute(stmt)
        for ha in r.scalars().all():
            existing_ha.add(ha.amenity_id)
        for a in amenity_map.values():
            if a.id not in existing_ha:
                session.add(HotelAmenity(hotel_id=hotel_id, amenity_id=a.id))
        await session.flush()
        print(f"  ✓ Hotel-amenities linked: {len(amenity_map)}")

        # ------------------------------------------------
        # 5. BRANCHES
        # ------------------------------------------------
        branches_info = [
            ("Grand Plaza – Toshkent", "GPH001", "Toshkent", True),
            ("Grand Plaza – Samarqand", "GPH002", "Samarqand", False),
            ("Grand Plaza – Buxoro", "GPH003", "Buxoro", False),
        ]
        branch_map: dict[str, Branch] = {}
        for name, code, city, is_main in branches_info:
            stmt = select(Branch).where(Branch.hotel_id == hotel_id, Branch.code == code)
            r = await session.execute(stmt)
            b = r.scalar_one_or_none()
            if b is None:
                b = Branch(
                    hotel_id=hotel_id,
                    name=name,
                    code=code,
                    city=city,
                    country="O'zbekistan",
                    is_main_branch=is_main,
                    status="ACTIVE",
                )
                session.add(b)
            branch_map[city] = b
        await session.flush()
        print(f"✓ Branches: {len(branch_map)}")

        # ------------------------------------------------
        # 6. FLOORS
        # ------------------------------------------------
        floor_map: dict[str, list[Floor]] = {}  # city → floors
        for city, branch in branch_map.items():
            floors = []
            for fn in range(1, 5):
                stmt = select(Floor).where(
                    Floor.branch_id == branch.id, Floor.floor_number == fn
                )
                r = await session.execute(stmt)
                f_ = r.scalar_one_or_none()
                if f_ is None:
                    f_ = Floor(
                        hotel_id=hotel_id,
                        branch_id=branch.id,
                        floor_number=fn,
                        name=f"{branch.name} – {fn}-qavat",
                    )
                    session.add(f_)
                floors.append(f_)
            floor_map[city] = floors
        await session.flush()
        total_floors = sum(len(fs) for fs in floor_map.values())
        print(f"✓ Floors: {total_floors}  (4 per branch × 3 branches)")

        # ------------------------------------------------
        # 7. ROOM TYPES
        # ------------------------------------------------
        room_type_data = [
            ("Standart (bir kishilik)", 250000, 1, ["WiFi", "TV", "Konditsioner"]),
            ("Standart (ikki kishilik)", 380000, 2, ["WiFi", "TV", "Konditsioner", "Mini bar"]),
            ("Deluxe", 650000, 2, ["WiFi", "TV", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali"]),
            ("Suite (Lyuks)", 1200000, 4, ["WiFi", "TV", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali", "Shaxsiy hovuz"]),
            ("Family Room", 550000, 5, ["WiFi", "TV", "Konditsioner", "Mini bar", "Bolalar maydonchasi"]),
        ]
        room_type_map: dict[str, RoomType] = {}
        for name, price, capacity, am_list in room_type_data:
            stmt = select(RoomType).where(RoomType.name == name)
            r = await session.execute(stmt)
            rt = r.scalar_one_or_none()
            if rt is None:
                rt = RoomType(
                    name=name,
                    base_price=price,
                    capacity=capacity,
                    amenities=am_list,
                    is_active=True,
                )
                session.add(rt)
            room_type_map[name] = rt
        await session.flush()

        # Link room types to hotel
        existing_hrt = set()
        stmt = select(HotelRoomType).where(HotelRoomType.hotel_id == hotel_id)
        r = await session.execute(stmt)
        for hrt in r.scalars().all():
            existing_hrt.add(hrt.room_type_id)
        for rt in room_type_map.values():
            if rt.id not in existing_hrt:
                session.add(HotelRoomType(hotel_id=hotel_id, room_type_id=rt.id))
        await session.flush()
        print(f"✓ RoomTypes: {len(room_type_map)}")

        # ------------------------------------------------
        # 8. ROOMS (Xonalar)
        # ------------------------------------------------
        room_map: dict[str, list[Room]] = {}  # city → rooms
        # amenity mapping for room-amenity links
        amenity_name_to_obj = {a.name: a for a in amenity_map.values()}
        room_type_amenity_map = {
            "Standart (bir kishilik)": ["Wi-Fi (bepul)", "Televizor", "Konditsioner"],
            "Standart (ikki kishilik)": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar"],
            "Deluxe": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali"],
            "Suite (Lyuks)": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali", "Shaxsiy hovuz"],
            "Family Room": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar", "Bolalar maydonchasi"],
        }
        rnum_base = {"Toshkent": 101, "Samarqand": 201, "Buxoro": 301}

        for city in ["Toshkent", "Samarqand", "Buxoro"]:
            branch = branch_map[city]
            floors = floor_map[city]
            rooms: list[Room] = []
            room_num = rnum_base[city]
            for floor_ in floors:
                for rt_name, rt_obj in room_type_map.items():
                    for i in range(2):
                        rn = str(room_num + i)
                        stmt = select(Room).where(
                            Room.branch_id == branch.id, Room.room_number == rn
                        )
                        r = await session.execute(stmt)
                        room = r.scalar_one_or_none()
                        if room is None:
                            notes = "Deraza tomondan ko'cha" if i == 0 else "Deraza tomondan hovli"
                            room = Room(
                                hotel_id=hotel_id,
                                branch_id=branch.id,
                                floor_id=floor_.id,
                                room_type_id=rt_obj.id,
                                room_number=rn,
                                base_price=rt_obj.base_price,
                                capacity=rt_obj.capacity,
                                current_status=RoomStatus.AVAILABLE.value,
                                notes=notes,
                            )
                            session.add(room)
                            await session.flush()

                            # Link amenities to room
                            am_names = room_type_amenity_map.get(rt_name, [])
                            for am_name in am_names:
                                am = amenity_name_to_obj.get(am_name)
                                if am:
                                    session.add(RoomAmenity(room_id=room.id, amenity_id=am.id))

                        rooms.append(room)
                    room_num += 2
            room_map[city] = rooms

        total_rooms = sum(len(rs) for rs in room_map.values())
        await session.flush()
        print(f"✓ Rooms: {total_rooms}  (40 per branch × 3)")

        # ------------------------------------------------
        # 9. EMPLOYEES (Xodimlar)
        # ------------------------------------------------
        toshkent_branch = branch_map["Toshkent"]
        samarqand_branch = branch_map["Samarqand"]
        buxoro_branch = branch_map["Buxoro"]

        employee_data = [
            (toshkent_branch.id, "bobur", "bobur123", "Bobur", "Ahmedov", "bobur@grandplaza.uz", "+998931112233"),
            (toshkent_branch.id, "dilnoza", "dilnoza123", "Dilnoza", "Karimova", "dilnoza@grandplaza.uz", "+998931112244"),
            (toshkent_branch.id, "sardor", "sardor123", "Sardor", "Yusupov", "sardor@grandplaza.uz", "+998931112255"),
            (toshkent_branch.id, "zilola", "zilola123", "Zilola", "Norboyeva", "zilola@grandplaza.uz", "+998931112266"),
            (toshkent_branch.id, "jasur", "jasur123", "Jasur", "To'xtayev", "jasur@grandplaza.uz", "+998931112277"),
            (toshkent_branch.id, "lola", "lola123", "Lola", "Mirzayeva", "lola@grandplaza.uz", "+998931112288"),
            (samarqand_branch.id, "rustam", "rustam123", "Rustam", "Hamidov", "rustam@grandplaza.uz", "+998931113311"),
            (buxoro_branch.id, "feruza", "feruza123", "Feruza", "Saidova", "feruza@grandplaza.uz", "+998931113322"),
        ]
        emp_map: dict[str, User] = {}
        for bid, uname, pwd, fname, lname, email, phone in employee_data:
            stmt = select(User).where(User.username == uname)
            r = await session.execute(stmt)
            emp = r.scalar_one_or_none()
            if emp is None:
                emp = User(
                    user_type=UserType.EMPLOYEE.value,
                    hotel_id=hotel_id,
                    branch_id=bid,
                    username=uname,
                    password_hash=hash_password(pwd),
                    first_name=fname,
                    last_name=lname,
                    email=email,
                    phone=phone,
                    status=UserStatus.ACTIVE.value,
                    hire_date=date(2026, 1, 15),
                )
                session.add(emp)
            emp_map[uname] = emp
        await session.flush()
        print(f"✓ Employees: {len(emp_map)}")

        # ------------------------------------------------
        # 10. PERMISSIONS — assign to employees
        # ------------------------------------------------
        reception_codes = ["reservation.create", "reservation.update", "reservation.cancel",
                           "reservation.view", "guest.create", "guest.update", "guest.view",
                           "guest.checkin", "guest.checkout", "room.view", "room.status.update"]
        cleaner_codes = ["housekeeping.task.create", "housekeeping.task.assign",
                         "housekeeping.task.update", "housekeeping.cleaning.start",
                         "housekeeping.cleaning.complete", "room.view"]
        finance_codes = ["finance.view", "finance.invoice.create", "finance.invoice.manage",
                         "finance.payment.create", "finance.journal.create",
                         "reservation.view", "guest.view", "room.view"]
        mgr_codes = [c for c, _, _ in PERMISSIONS]  # all

        emp_perms: dict[str, list[str]] = {
            "bobur": mgr_codes,
            "dilnoza": reception_codes,
            "sardor": reception_codes,
            "zilola": cleaner_codes,
            "jasur": cleaner_codes,
            "lola": finance_codes,
            "rustam": reception_codes,
            "feruza": reception_codes,
        }

        for uname, codes in emp_perms.items():
            emp = emp_map[uname]
            existing_up = set()
            stmt = select(UserPermission.permission_id).where(UserPermission.user_id == emp.id)
            r = await session.execute(stmt)
            for pid in r.scalars().all():
                existing_up.add(pid)
            for code in codes:
                p = perm_map[code]
                if p.id not in existing_up:
                    session.add(UserPermission(
                        user_id=emp.id,
                        permission_id=p.id,
                        hotel_id=hotel_id,
                        granted_by=admin.id,
                    ))
        await session.flush()
        print("✓ Permissions assigned to employees")

        # ------------------------------------------------
        # 11. GUESTS (Mehmonlar)
        # ------------------------------------------------
        guest_data = [
            ("Akmal", "Rashidov", "+998971112233", "akmal@mail.uz", "AA1234567", "UZ", "1990-03-15"),
            ("Shahlo", "Tursunova", "+998971112244", "shahlo@mail.uz", "AB2345678", "UZ", "1992-07-22"),
            ("James", "Smith", "+447766554433", "james@email.co.uk", "UK7890123", "GB", "1985-11-03"),
            ("Aktoty", "Bakytkyzy", "+77775556677", "aktoty@mail.kz", "KZ3456789", "KZ", "1995-05-10"),
            ("Rustam", "Boltayev", "+998971113355", "rustam@mail.uz", "AC4567890", "UZ", "1988-01-28"),
            ("Gulnora", "Ismoilova", "+998971113366", "gulnora@mail.uz", "AD5678901", "UZ", "1993-09-14"),
            ("Michael", "Brown", "+12025551234", "mbrown@email.com", "US6789012", "US", "1980-06-20"),
            ("Otabek", "Jalolov", "+998971113377", "otabek@mail.uz", "AE6789012", "UZ", "1996-12-05"),
        ]
        guest_map: dict[str, Guest] = {}
        for fname, lname, phone, email, passport, nat, bdate in guest_data:
            stmt = select(Guest).where(
                Guest.hotel_id == hotel_id,
                Guest.first_name == fname,
                Guest.last_name == lname,
            )
            r = await session.execute(stmt)
            g = r.scalar_one_or_none()
            if g is None:
                g = Guest(
                    hotel_id=hotel_id,
                    first_name=fname,
                    last_name=lname,
                    phone=phone,
                    email=email,
                    passport_number=passport,
                    nationality=nat,
                    birth_date=date.fromisoformat(bdate),
                )
                session.add(g)
            guest_map[f"{fname}_{lname}"] = g
        await session.flush()
        print(f"✓ Guests: {len(guest_map)}")

        # ------------------------------------------------
        # 12. GLOBAL SERVICES & HOTEL SERVICES
        # ------------------------------------------------
        service_data: list[tuple[str, str, str, float]] = [
            ("Ertalabki nonushta (shved stoli)", "BREAKFAST", "FOOD", 75000),
            ("Biznes tushlik", "LUNCH", "FOOD", 120000),
            ("Kirlarni yuvish", "LAUNDRY", "HOUSEKEEPING", 50000),
            ("Aeroportdan transfer", "AIRPORT_TRANSFER", "TRANSPORT", 180000),
            ("SPA – massaj (1 soat)", "SPA_MASSAGE", "WELLNESS", 250000),
            ("Hovuzga kirish", "POOL_ACCESS", "RECREATION", 60000),
            ("Mini bar to'ldirish", "MINIBAR", "FOOD", 85000),
            ("Avtoturargoh (kunlik)", "PARKING", "TRANSPORT", 35000),
            ("Toshkent bo'ylab ekskursiya", "TOUR_TASHKENT", "TOUR", 200000),
        ]
        hotel_service_map: dict[str, HotelService] = {}
        for s_name, s_code, s_cat, s_price in service_data:
            svc_stmt = select(Service).where(Service.code == s_code)
            r = await session.execute(svc_stmt)
            svc = r.scalar_one_or_none()
            if svc is None:
                svc = Service(name=s_name, code=s_code, category=s_cat, is_active=True)
                session.add(svc)
                await session.flush()

            hs_stmt = select(HotelService).where(
                HotelService.hotel_id == hotel_id, HotelService.service_id == svc.id
            )
            r = await session.execute(hs_stmt)
            hs = r.scalar_one_or_none()
            if hs is None:
                hs = HotelService(hotel_id=hotel_id, service_id=svc.id, price=s_price, is_active=True)
                session.add(hs)
            hotel_service_map[s_code] = hs
        await session.flush()
        print(f"✓ Services: {len(service_data)}")

        # ------------------------------------------------
        # 13. RESERVATIONS (Bronlar)
        # ------------------------------------------------
        t_rooms = room_map["Toshkent"]
        s_rooms = room_map["Samarqand"]
        b_rooms = room_map["Buxoro"]
        admin_id = admin.id

        reservation_data = [
            # (guest_key, room_index, branch, check_in, check_out, adults, children, notes, check_in_now, check_out_now)
            ("Akmal_Rashidov", 0, toshkent_branch, "2026-06-24", "2026-06-26", 1, 0, "Erta tongda keladi", True, False),
            ("Shahlo_Tursunova", 5, toshkent_branch, "2026-06-23", "2026-06-27", 2, 1, "Bola bilan", True, False),
            ("James_Smith", 10, toshkent_branch, "2026-06-28", "2026-07-03", 1, 0, "Chet el fuqarosi – pasport tekshirilsin", False, False),
            ("Aktoty_Bakytkyzy", 0, samarqand_branch, "2026-06-27", "2026-06-30", 2, 2, None, False, False),
            ("Rustam_Boltayev", 15, toshkent_branch, "2026-06-25", "2026-06-26", 1, 0, None, False, False),
            ("Gulnora_Ismoilova", 0, buxoro_branch, "2026-07-01", "2026-07-05", 2, 0, "To'yni o'tkazish maqsadida", False, False),
            ("Michael_Brown", 3, toshkent_branch, "2026-06-20", "2026-06-22", 1, 0, None, True, True),
            ("Otabek_Jalolov", 7, toshkent_branch, "2026-06-22", "2026-06-24", 1, 0, None, True, True),
        ]

        res_count = 0
        for g_key, r_idx, branch, ci, co, ad, ch, notes, do_ci, do_co in reservation_data:
            guest = guest_map[g_key]
            rooms_list = (
                t_rooms if branch == toshkent_branch
                else s_rooms if branch == samarqand_branch
                else b_rooms
            )
            room = rooms_list[r_idx % len(rooms_list)]

            ci_date = date.fromisoformat(ci)
            co_date = date.fromisoformat(co)
            nights = (co_date - ci_date).days
            total = float(room.base_price * nights)

            status = ReservationStatus.CHECKED_OUT.value if do_co else (
                ReservationStatus.CHECKED_IN.value if do_ci else ReservationStatus.CONFIRMED.value
            )
            payment_status = PaymentStatus.PAID.value if do_co else (
                PaymentStatus.PARTIALLY_PAID.value if do_ci else PaymentStatus.UNPAID.value
            )
            paid = total if do_co else (total * 0.5 if do_ci else 0)

            res_num = _reservation_number()
            res = Reservation(
                hotel_id=hotel_id,
                branch_id=branch.id,
                reservation_number=res_num,
                guest_id=guest.id,
                room_id=room.id,
                booking_type=BookingType.DAILY.value,
                check_in_date=ci_date,
                check_out_date=co_date,
                check_in_datetime=datetime.now(timezone.utc) if do_ci else None,
                check_out_datetime=datetime.now(timezone.utc) if do_co else None,
                adults=ad,
                children=ch,
                status=status,
                total_amount=total,
                paid_amount=paid,
                payment_status=payment_status,
                discount_amount=0,
                discount_percent=0,
                notes=notes,
                created_by=admin_id,
            )
            session.add(res)
            await session.flush()

            # Update room status
            if do_co:
                room.current_status = RoomStatus.AVAILABLE.value
            elif do_ci:
                room.current_status = RoomStatus.OCCUPIED.value
            else:
                room.current_status = RoomStatus.RESERVED.value

            res_count += 1
        await session.flush()
        print(f"✓ Reservations: {res_count}  (2 active, 3 upcoming, 2 checked-out, 1 today)")

        # ------------------------------------------------
        # 14. HOUSEKEEPING TASKS
        # ------------------------------------------------
        hk_data = [
            (toshkent_branch.id, t_rooms[1].id, TaskType.CLEANING.value, TaskPriority.MEDIUM.value,
             emp_map["zilola"].id, "Kunduzgi tozalash"),
            (toshkent_branch.id, t_rooms[6].id, TaskType.CLEANING.value, TaskPriority.HIGH.value,
             emp_map["jasur"].id, "Tezkor tozalash kerak"),
            (toshkent_branch.id, t_rooms[12].id, TaskType.MAINTENANCE.value, TaskPriority.URGENT.value,
             None, "Konditsioner ishlamayapti"),
            (toshkent_branch.id, t_rooms[18].id, TaskType.INSPECTION.value, TaskPriority.LOW.value,
             None, "Bo'sh xonani ko'zdan kechirish"),
            (toshkent_branch.id, t_rooms[4].id, TaskType.TURN_DOWN.value, TaskPriority.MEDIUM.value,
             emp_map["zilola"].id, "Kechki tayyorlash"),
        ]
        if s_rooms:
            hk_data.append(
                (samarqand_branch.id, s_rooms[0].id, TaskType.CLEANING.value, TaskPriority.MEDIUM.value,
                 emp_map["rustam"].id, "Kunduzgi tozalash – Samarqand")
            )
        if b_rooms:
            hk_data.append(
                (buxoro_branch.id, b_rooms[0].id, TaskType.CLEANING.value, TaskPriority.MEDIUM.value,
                 emp_map["feruza"].id, "Kunduzgi tozalash – Buxoro")
            )

        for b_id, r_id, tt, tp, assigned, notes in hk_data:
            hk = HousekeepingTask(
                hotel_id=hotel_id,
                branch_id=b_id,
                room_id=r_id,
                task_type=tt,
                status=TaskStatus.OPEN.value,
                priority=tp,
                assigned_to=assigned,
                notes=notes,
                scheduled_date=date.today(),
                created_by=admin_id,
            )
            session.add(hk)
        await session.flush()
        print(f"✓ Housekeeping Tasks: {len(hk_data)}")

        # ================================================
        # COMMIT
        # ================================================
        await session.commit()

    print(f"""
{'=' * 60}
  ✅ TEST DATA SEEDING COMPLETE
{'=' * 60}
  Hotel:        Grand Plaza Hotel
  Amenities:    {len(amenity_map)} ta
  Branches:     3 (Toshkent, Samarqand, Buxoro)
  Floors:       {total_floors} ta (4 per branch)
  Room Types:   {len(room_type_map)} ta
  Rooms:        {total_rooms} ta (40 per branch)
  Employees:    {len(emp_map)} ta
  Guests:       {len(guest_map)} ta
  Reservations: {res_count} ta
  Services:     {len(service_data)} ta
  HK Tasks:     {len(hk_data)} ta
  Permissions:  {len(perm_map)} ta

  Login: admin / admin123
  Employee login: bobur / bobur123  (manager – full access)
                  dilnoza / dilnoza123  (receptionist)
""")


if __name__ == "__main__":
    asyncio.run(seed())
