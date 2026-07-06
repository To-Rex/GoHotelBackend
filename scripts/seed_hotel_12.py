"""
Test data seeder for existing "test" hotel (code=12).
Inserts: floors, room types, rooms, amenities, employees, guests,
         reservations, services, housekeeping tasks.

Usage: python -m scripts.seed_hotel_12
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


async def seed():
    async with async_session_factory() as session:

        # ---- Find hotel & branch ----
        r = await session.execute(
            select(Hotel).where(Hotel.code == "12")
        )
        hotel = r.scalar_one_or_none()
        if not hotel:
            print("❌ Hotel with code='12' not found!")
            return
        print(f"✓ Hotel: {hotel.name}  (code={hotel.code}, stars={hotel.stars})")

        r = await session.execute(
            select(Branch).where(Branch.hotel_id == hotel.id, Branch.is_main_branch == True)
        )
        main_branch = r.scalar_one_or_none()
        if not main_branch:
            print("❌ No main branch found!")
            return
        print(f"✓ Main Branch: {main_branch.name}  (code={main_branch.code})")

        hotel_id = hotel.id
        branch_id = main_branch.id

        # ============================================
        # 1. AMENITIES (Qulayliklar)
        # ============================================
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
        existing_ha_ids: set[str] = set()
        r = await session.execute(select(HotelAmenity).where(HotelAmenity.hotel_id == hotel_id))
        for ha in r.scalars().all():
            existing_ha_ids.add(str(ha.amenity_id))

        for name, icon in amenity_data:
            r = await session.execute(select(Amenity).where(Amenity.name == name))
            a = r.scalar_one_or_none()
            if a is None:
                a = Amenity(name=name, icon=icon, is_active=True)
                session.add(a)
                await session.flush()
            amenity_map[name] = a
            if str(a.id) not in existing_ha_ids:
                session.add(HotelAmenity(hotel_id=hotel_id, amenity_id=a.id))
        await session.flush()
        print(f"✓ Amenities: {len(amenity_map)} (hotel-linked)")

        # ============================================
        # 2. FLOORS (Qavatlar)
        # ============================================
        floor_list: list[Floor] = []
        r = await session.execute(
            select(Floor).where(Floor.branch_id == branch_id)
        )
        existing_floors = {f.floor_number for f in r.scalars().all()}

        for fn in range(1, 5):
            if fn in existing_floors:
                r = await session.execute(
                    select(Floor).where(Floor.branch_id == branch_id, Floor.floor_number == fn)
                )
                floor_list.append(r.scalar_one())
            else:
                f = Floor(
                    hotel_id=hotel_id,
                    branch_id=branch_id,
                    floor_number=fn,
                    name=f"{fn}-qavat",
                )
                session.add(f)
                await session.flush()
                floor_list.append(f)
        print(f"✓ Floors: {len(floor_list)}")

        # ============================================
        # 3. ROOM TYPES
        # ============================================
        room_type_data = [
            ("Standart (bir kishilik)", 250000, 1, ["WiFi", "TV", "Konditsioner"]),
            ("Standart (ikki kishilik)", 380000, 2, ["WiFi", "TV", "Konditsioner", "Mini bar"]),
            ("Deluxe", 650000, 2, ["WiFi", "TV", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali"]),
            ("Suite (Lyuks)", 1200000, 4, ["WiFi", "TV", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali", "Shaxsiy hovuz"]),
            ("Family Room", 550000, 5, ["WiFi", "TV", "Konditsioner", "Mini bar", "Bolalar maydonchasi"]),
        ]
        room_type_map: dict[str, RoomType] = {}
        existing_hrt_ids: set[str] = set()
        r = await session.execute(select(HotelRoomType).where(HotelRoomType.hotel_id == hotel_id))
        for hrt in r.scalars().all():
            existing_hrt_ids.add(str(hrt.room_type_id))

        for name, price, capacity, am_list in room_type_data:
            r = await session.execute(select(RoomType).where(RoomType.name == name))
            rt = r.scalar_one_or_none()
            if rt is None:
                rt = RoomType(
                    name=name, base_price=price, capacity=capacity,
                    amenities=am_list, is_active=True,
                )
                session.add(rt)
                await session.flush()
            room_type_map[name] = rt
            if str(rt.id) not in existing_hrt_ids:
                session.add(HotelRoomType(hotel_id=hotel_id, room_type_id=rt.id))
        await session.flush()
        print(f"✓ RoomTypes: {len(room_type_map)}")

        # ============================================
        # 4. ROOMS (Xonalar) — 10 per floor × 4 floors = 40
        # ============================================
        room_type_amenity_map = {
            "Standart (bir kishilik)": ["Wi-Fi (bepul)", "Televizor", "Konditsioner"],
            "Standart (ikki kishilik)": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar"],
            "Deluxe": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali"],
            "Suite (Lyuks)": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali", "Shaxsiy hovuz"],
            "Family Room": ["Wi-Fi (bepul)", "Televizor", "Konditsioner", "Mini bar", "Bolalar maydonchasi"],
        }

        r = await session.execute(select(Room).where(Room.branch_id == branch_id))
        existing_rooms = {(rm.floor_id, rm.room_number) for rm in r.scalars().all()}

        room_map: dict[str, list[Room]] = {}  # floor_key → rooms
        room_num = 101
        for floor_ in floor_list:
            floor_rooms: list[Room] = []
            for rt_name, rt_obj in room_type_map.items():
                for i in range(2):
                    rn = str(room_num + i)
                    if (floor_.id, rn) not in existing_rooms:
                        notes = "Deraza tomondan ko'cha" if i == 0 else "Deraza tomondan hovli"
                        room = Room(
                            hotel_id=hotel_id,
                            branch_id=branch_id,
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

                        for am_name in room_type_amenity_map.get(rt_name, []):
                            am = amenity_map.get(am_name)
                            if am:
                                session.add(RoomAmenity(room_id=room.id, amenity_id=am.id))
                    else:
                        r = await session.execute(
                            select(Room).where(Room.floor_id == floor_.id, Room.room_number == rn)
                        )
                        room = r.scalar_one()
                    floor_rooms.append(room)
                room_num += 2
            room_map[str(floor_.id)] = floor_rooms

        all_rooms = [rm for fms in room_map.values() for rm in fms]
        await session.flush()
        print(f"✓ Rooms: {len(all_rooms)}  (10 per floor × 4 floors)")

        # ============================================
        # 5. SUPERADMIN & EMPLOYEES
        # ============================================
        r = await session.execute(select(User).where(User.username == "admin"))
        admin = r.scalar_one_or_none()
        if not admin:
            admin = User(
                user_type=UserType.SUPER_ADMIN.value,
                username="admin", password_hash=hash_password("admin123"),
                first_name="Super", last_name="Admin",
                status=UserStatus.ACTIVE.value,
            )
            session.add(admin)
            await session.flush()

        employee_data = [
            (branch_id, "bobur", "bobur123", "Bobur", "Ahmedov", "bobur@hotel12.uz", "+998931112233"),
            (branch_id, "dilnoza", "dilnoza123", "Dilnoza", "Karimova", "dilnoza@hotel12.uz", "+998931112244"),
            (branch_id, "sardor", "sardor123", "Sardor", "Yusupov", "sardor@hotel12.uz", "+998931112255"),
            (branch_id, "zilola", "zilola123", "Zilola", "Norboyeva", "zilola@hotel12.uz", "+998931112266"),
            (branch_id, "jasur", "jasur123", "Jasur", "To'xtayev", "jasur@hotel12.uz", "+998931112277"),
            (branch_id, "lola", "lola123", "Lola", "Mirzayeva", "lola@hotel12.uz", "+998931112288"),
        ]
        emp_map: dict[str, User] = {}
        for bid, uname, pwd, fname, lname, email, phone in employee_data:
            r = await session.execute(select(User).where(User.username == uname))
            emp = r.scalar_one_or_none()
            if emp is None:
                emp = User(
                    user_type=UserType.EMPLOYEE.value,
                    hotel_id=hotel_id, branch_id=bid,
                    username=uname,
                    password_hash=hash_password(pwd),
                    first_name=fname, last_name=lname,
                    email=email, phone=phone,
                    status=UserStatus.ACTIVE.value,
                    hire_date=date(2026, 1, 15),
                )
                session.add(emp)
            emp_map[uname] = emp
        await session.flush()

        # ---- Permissions ----
        PERMS = [
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
        ]
        perm_map: dict[str, Permission] = {}
        for code, name, module in PERMS:
            r = await session.execute(select(Permission).where(Permission.code == code))
            p = r.scalar_one_or_none()
            if p is None:
                p = Permission(code=code, name=name, module=module)
                session.add(p)
            perm_map[code] = p
        await session.flush()

        reception_codes = [
            "reservation.create", "reservation.update", "reservation.cancel",
            "reservation.view", "guest.create", "guest.update", "guest.view",
            "guest.checkin", "guest.checkout", "room.view", "room.status.update",
        ]
        cleaner_codes = [
            "housekeeping.task.create", "housekeeping.task.assign",
            "housekeeping.task.update", "housekeeping.cleaning.start",
            "housekeeping.cleaning.complete", "room.view",
        ]
        finance_codes = [
            "finance.view", "finance.invoice.create", "finance.invoice.manage",
            "finance.payment.create", "finance.journal.create",
            "reservation.view", "guest.view", "room.view",
        ]
        mgr_codes = [c for c, _, _ in PERMS]

        emp_perms = {
            "bobur": mgr_codes,
            "dilnoza": reception_codes,
            "sardor": reception_codes,
            "zilola": cleaner_codes,
            "jasur": cleaner_codes,
            "lola": finance_codes,
        }

        for uname, codes in emp_perms.items():
            emp = emp_map[uname]
            r = await session.execute(
                select(UserPermission.permission_id).where(UserPermission.user_id == emp.id)
            )
            existing_up = {str(pid) for pid in r.scalars().all()}
            for code in codes:
                p = perm_map[code]
                if str(p.id) not in existing_up and p.id:
                    session.add(UserPermission(
                        user_id=emp.id, permission_id=p.id,
                        hotel_id=hotel_id, granted_by=admin.id,
                    ))
        await session.flush()
        print(f"✓ Employees: {len(emp_map)} (with permissions)")

        # ============================================
        # 6. GUESTS (Mehmonlar)
        # ============================================
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
            r = await session.execute(
                select(Guest).where(
                    Guest.hotel_id == hotel_id,
                    Guest.first_name == fname,
                    Guest.last_name == lname,
                )
            )
            g = r.scalar_one_or_none()
            if g is None:
                g = Guest(
                    hotel_id=hotel_id,
                    first_name=fname, last_name=lname,
                    phone=phone, email=email,
                    passport_number=passport,
                    nationality=nat,
                    birth_date=date.fromisoformat(bdate),
                )
                session.add(g)
            guest_map[f"{fname}_{lname}"] = g
        await session.flush()
        print(f"✓ Guests: {len(guest_map)}")

        # ============================================
        # 7. SERVICES
        # ============================================
        service_data: list[tuple[str, str, str, float]] = [
            ("Ertalabki nonushta", "BREAKFAST", "FOOD", 75000),
            ("Biznes tushlik", "LUNCH", "FOOD", 120000),
            ("Kirlarni yuvish", "LAUNDRY", "HOUSEKEEPING", 50000),
            ("Aeroportdan transfer", "AIRPORT_TRANSFER", "TRANSPORT", 180000),
            ("SPA – massaj (1 soat)", "SPA_MASSAGE", "WELLNESS", 250000),
            ("Hovuzga kirish", "POOL_ACCESS", "RECREATION", 60000),
            ("Mini bar to'ldirish", "MINIBAR", "FOOD", 85000),
            ("Avtoturargoh (kunlik)", "PARKING", "TRANSPORT", 35000),
            ("Shahar bo'ylab ekskursiya", "TOUR_CITY", "TOUR", 200000),
        ]
        existing_hs_ids: set[str] = set()
        r = await session.execute(select(HotelService).where(HotelService.hotel_id == hotel_id))
        for hs in r.scalars().all():
            existing_hs_ids.add(str(hs.service_id))

        for s_name, s_code, s_cat, s_price in service_data:
            r = await session.execute(select(Service).where(Service.code == s_code))
            svc = r.scalar_one_or_none()
            if svc is None:
                svc = Service(name=s_name, code=s_code, category=s_cat, is_active=True)
                session.add(svc)
                await session.flush()

            if str(svc.id) not in existing_hs_ids:
                session.add(HotelService(
                    hotel_id=hotel_id, service_id=svc.id,
                    price=s_price, is_active=True,
                ))
        await session.flush()
        print(f"✓ Services: {len(service_data)}")

        # ============================================
        # 8. RESERVATIONS (Bronlar)
        # ============================================
        from datetime import timedelta
        today = date.today()
        ci_past1 = today - timedelta(days=9)
        co_past1 = ci_past1 + timedelta(days=2)
        ci_past2 = today - timedelta(days=7)
        co_past2 = ci_past2 + timedelta(days=2)
        ci_active1 = today - timedelta(days=5)
        co_active1 = today + timedelta(days=2)
        ci_active2 = today - timedelta(days=4)
        co_active2 = today + timedelta(days=3)
        ci_today = today
        co_today = today + timedelta(days=2)
        ci_future1 = today + timedelta(days=1)
        co_future1 = today + timedelta(days=5)
        ci_future2 = today + timedelta(days=3)
        co_future2 = today + timedelta(days=8)
        ci_future3 = today + timedelta(days=6)
        co_future3 = today + timedelta(days=10)

        reservation_data = [
            # (guest_key, room_index, check_in, check_out, adults, children, notes, do_ci, do_co)
            ("Akmal_Rashidov", 0, ci_active1, co_active1, 1, 0, "Erta tongda keladi", True, False),
            ("Shahlo_Tursunova", 5, ci_active2, co_active2, 2, 1, "Bola bilan", True, False),
            ("James_Smith", 10, ci_future1, co_future1, 1, 0, "Chet el fuqarosi", False, False),
            ("Aktoty_Bakytkyzy", 15, ci_future2, co_future2, 2, 2, None, False, False),
            ("Rustam_Boltayev", 20, ci_today, co_today, 1, 0, "Bugun keladi", False, False),
            ("Gulnora_Ismoilova", 25, ci_future3, co_future3, 2, 0, "To'yni o'tkazish", False, False),
            ("Michael_Brown", 3, ci_past1, co_past1, 1, 0, None, True, True),
            ("Otabek_Jalolov", 7, ci_past2, co_past2, 1, 0, None, True, True),
        ]

        res_count = 0
        for g_key, rm_idx, ci, co, ad, ch, notes, do_ci, do_co in reservation_data:
            guest = guest_map[g_key]
            room = all_rooms[rm_idx % len(all_rooms)]

            nights = (co - ci).days
            total = float(room.base_price * nights)

            status = (
                ReservationStatus.CHECKED_OUT.value if do_co
                else ReservationStatus.CHECKED_IN.value if do_ci
                else ReservationStatus.CONFIRMED.value
            )
            pstatus = (
                PaymentStatus.PAID.value if do_co
                else PaymentStatus.PARTIALLY_PAID.value if do_ci
                else PaymentStatus.UNPAID.value
            )
            paid = total if do_co else (total * 0.5 if do_ci else 0)

            res_num = f"RES-{datetime.now().strftime('%Y%m%d')}-{random.randint(100000, 999999)}"
            res = Reservation(
                hotel_id=hotel_id,
                branch_id=branch_id,
                reservation_number=res_num,
                guest_id=guest.id,
                room_id=room.id,
                booking_type=BookingType.DAILY.value,
                check_in_date=ci,
                check_out_date=co,
                check_in_datetime=datetime.now(timezone.utc) if do_ci else None,
                check_out_datetime=datetime.now(timezone.utc) if do_co else None,
                adults=ad, children=ch,
                status=status,
                total_amount=total,
                paid_amount=paid,
                payment_status=pstatus,
                discount_amount=0, discount_percent=0,
                notes=notes,
                created_by=admin.id,
            )
            session.add(res)
            await session.flush()

            if do_co:
                room.current_status = RoomStatus.AVAILABLE.value
            elif do_ci:
                room.current_status = RoomStatus.OCCUPIED.value
            else:
                room.current_status = RoomStatus.RESERVED.value
            res_count += 1
        await session.flush()
        print(f"✓ Reservations: {res_count}  (2 active, 3 upcoming, 2 checked-out, 1 today)")

        # ============================================
        # 9. HOUSEKEEPING TASKS
        # ============================================
        hk_data = [
            (all_rooms[1].id, TaskType.CLEANING.value, TaskPriority.MEDIUM.value,
             emp_map["zilola"].id, "Kunduzgi tozalash"),
            (all_rooms[6].id, TaskType.CLEANING.value, TaskPriority.HIGH.value,
             emp_map["jasur"].id, "Tezkor tozalash"),
            (all_rooms[12].id, TaskType.MAINTENANCE.value, TaskPriority.URGENT.value,
             None, "Konditsioner ishlamayapti"),
            (all_rooms[18].id, TaskType.INSPECTION.value, TaskPriority.LOW.value,
             None, "Bo'sh xonani ko'zdan kechirish"),
            (all_rooms[4].id, TaskType.TURN_DOWN.value, TaskPriority.MEDIUM.value,
             emp_map["zilola"].id, "Kechki tayyorlash"),
            (all_rooms[9].id, TaskType.CLEANING.value, TaskPriority.MEDIUM.value,
             emp_map["jasur"].id, "Qo'shimcha tozalash"),
            (all_rooms[14].id, TaskType.CLEANING.value, TaskPriority.LOW.value,
             None, "Rejali tozalash"),
        ]

        for r_id, tt, tp, assigned, notes in hk_data:
            hk = HousekeepingTask(
                hotel_id=hotel_id, branch_id=branch_id,
                room_id=r_id,
                task_type=tt, status=TaskStatus.OPEN.value,
                priority=tp, assigned_to=assigned,
                notes=notes, scheduled_date=date.today(),
                created_by=admin.id,
            )
            session.add(hk)
        await session.flush()
        print(f"✓ Housekeeping Tasks: {len(hk_data)}")

        # ============================================
        await session.commit()

    print(f"""
{'=' * 60}
  ✅ TEST DATA FOR HOTEL "{hotel.name}" COMPLETE
{'=' * 60}
  Amenities:     {len(amenity_map)} ta
  Floors:        {len(floor_list)} ta
  Room Types:    {len(room_type_map)} ta
  Rooms:         {len(all_rooms)} ta
  Employees:     {len(emp_map)} ta
  Guests:        {len(guest_map)} ta
  Reservations:  {res_count} ta
  Services:      {len(service_data)} ta
  HK Tasks:      {len(hk_data)} ta

  Login: admin / admin123
  Emp logins: bobur / bobur123  (manager)
              dilnoza / dilnoza123  (reception)
""")


if __name__ == "__main__":
    asyncio.run(seed())
