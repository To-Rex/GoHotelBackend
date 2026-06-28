"""
Comprehensive test data seeder for GoHotel.
Creates: hotel, branches, floors, room types, rooms,
employees, guests, reservations, services, housekeeping tasks.
"""
import json
import sys
import urllib.request
import urllib.error

BASE_URL = "http://localhost:8000/api/v1"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_token = None
_hotel_id = None


class FollowRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow 307/308 redirects for ALL methods, not just GET/HEAD."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = urllib.request.Request(
            newurl,
            data=req.data,
            headers=req.headers,
            method=req.get_method(),
        )
        return new_req


_redirect_opener = urllib.request.build_opener(FollowRedirectHandler())
urllib.request.install_opener(_redirect_opener)


def api(method: str, path: str, data: dict | None = None):
    """Make an API call. Returns (status, body_dict) or (status, None)."""
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode() if data else None

    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if _token:
        req.add_header("Authorization", f"Bearer {_token}")

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"  ❌ {method} {path}  → {e.code} {err_body[:200]}")
        return e.code, None


def login():
    global _token
    status, body = api("POST", "/auth/login", {"username": "admin", "password": "admin123"})
    if status == 200 and body:
        _token = body["access_token"]
        print("✓ Logged in as admin")
        return True
    print("❌ Login failed")
    return False


def hq(path: str) -> str:
    """Attach hotel_id query param."""
    return f"{path}?hotel_id={_hotel_id}"

# ---------------------------------------------------------------------------
# 1. HOTEL
# ---------------------------------------------------------------------------
def create_hotel():
    global _hotel_id
    data = {
        "name": "Grand Plaza Hotel",
        "code": "GPH001",
        "stars": 5,
        "phone": "+998901234567",
        "email": "info@grandplaza.uz",
        "address_line1": "Mustaqillik maydoni, 5",
        "city": "Toshkent",
        "state": "Toshkent viloyati",
        "country": "O'zbekistan",
        "postal_code": "100000",
    }
    status, body = api("POST", "/hotels", data)
    if status in (200, 201) and body:
        _hotel_id = body["id"]
        print(f"✓ Hotel created: {body['name']}  (id={_hotel_id[:8]}...)")
        return True
    print("❌ Hotel creation failed")
    return False


# ---------------------------------------------------------------------------
# 2. BRANCHES
# ---------------------------------------------------------------------------
def get_main_branch():
    status, body = api("GET", hq("/branches"))
    if status == 200 and body:
        items = body if isinstance(body, list) else body.get("items", [])
        for b in items:
            if b.get("is_main_branch"):
                print(f"  ✓ Main branch: {b['name']} (id={b['id'][:8]}...)")
                return b["id"]
    return None


def create_branch(name, code, city):
    data = {"name": name, "code": code, "city": city, "country": "O'zbekistan"}
    status, body = api("POST", hq("/branches"), data)
    if status in (200, 201) and body:
        print(f"  ✓ Branch: {body['name']}  (id={body['id'][:8]}...)")
        return body["id"]
    return None


# ---------------------------------------------------------------------------
# 3. FLOORS
# ---------------------------------------------------------------------------
def create_floor(branch_id, floor_number, name):
    data = {"branch_id": branch_id, "floor_number": floor_number, "name": name}
    status, body = api("POST", hq("/floors"), data)
    if status in (200, 201) and body:
        print(f"    ✓ Floor: {body['name'] or body['floor_number']}  (id={body['id'][:8]}...)")
        return body["id"]
    return None


# ---------------------------------------------------------------------------
# 4. ROOM TYPES
# ---------------------------------------------------------------------------
def create_room_type(name, base_price, capacity, amenities):
    data = {"name": name, "base_price": base_price, "capacity": capacity, "amenities": amenities}
    status, body = api("POST", hq("/room-types"), data)
    if status in (200, 201) and body:
        print(f"  ✓ RoomType: {body['name']}  ({body['base_price']} so'm, id={body['id'][:8]}...)")
        return body["id"]
    return None


# ---------------------------------------------------------------------------
# 5. ROOMS (Xonalar)
# ---------------------------------------------------------------------------
def create_room(branch_id, floor_id, room_type_id, room_number, notes=None):
    data = {
        "branch_id": branch_id,
        "floor_id": floor_id,
        "room_type_id": room_type_id,
        "room_number": room_number,
    }
    if notes:
        data["notes"] = notes
    status, body = api("POST", hq("/rooms"), data)
    if status in (200, 201) and body:
        print(f"      ✓ Room {body['room_number']} → {body['current_status']}  (id={body['id'][:8]}...)")
        return body["id"]
    return None


# ---------------------------------------------------------------------------
# 6. EMPLOYEES (Xodimlar)
# ---------------------------------------------------------------------------
def create_employee(branch_id, username, password, first_name, last_name, email, phone):
    data = {
        "hotel_id": _hotel_id,
        "branch_id": branch_id,
        "username": username,
        "password": password,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
        "hire_date": "2026-01-15",
    }
    status, body = api("POST", hq("/employees"), data)
    if status in (200, 201) and body:
        print(f"  ✓ Employee: {body['first_name']} {body['last_name']}  ({body['username']})")
        return body["id"]
    return None


# ---------------------------------------------------------------------------
# 7. PERMISSIONS – assign to employee
# ---------------------------------------------------------------------------
def get_all_permissions():
    status, body = api("GET", hq("/permissions"))
    if status == 200 and body:
        return {p["code"]: p["id"] for p in body}
    return {}


def assign_permissions(emp_id: str, perm_ids: list[str]):
    data = {"permission_ids": perm_ids}
    status, _ = api("PUT", hq(f"/permissions/{emp_id}/permissions"), data)
    if status == 200:
        print(f"    ✓ Assigned {len(perm_ids)} permissions")
        return True
    return False


# ---------------------------------------------------------------------------
# 8. GUESTS
# ---------------------------------------------------------------------------
def create_guest(first_name, last_name, phone, email, passport, nationality, birth_date):
    data = {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "email": email,
        "passport_number": passport,
        "nationality": nationality,
        "birth_date": birth_date,
    }
    status, body = api("POST", hq("/guests"), data)
    if status in (200, 201) and body:
        print(f"  ✓ Guest: {body['first_name']} {body['last_name']}  ({body['phone']})")
        return body["id"]
    return None


# ---------------------------------------------------------------------------
# 9. RESERVATIONS
# ---------------------------------------------------------------------------
def create_reservation(guest_id, room_id, branch_id, check_in, check_out, adults=1, children=0, notes=None):
    data = {
        "guest_id": guest_id,
        "room_id": room_id,
        "branch_id": branch_id,
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": adults,
        "children": children,
        "discount_amount": 0,
        "discount_percent": 0,
    }
    if notes:
        data["notes"] = notes
    status, body = api("POST", hq("/reservations"), data)
    if status in (200, 201) and body:
        print(f"  ✓ Reservation: {body['reservation_number']}  ({check_in} → {check_out})  [{body['status']}]")
        return body["id"], body.get("status")
    return None, None


def do_check_in(reservation_id):
    status, body = api("POST", hq(f"/reservations/{reservation_id}/check-in"))
    if status == 200:
        print(f"    ✓ Check-in: {body.get('status')}")
        return True
    return False


def do_check_out(reservation_id):
    status, body = api("POST", hq(f"/reservations/{reservation_id}/check-out"))
    if status == 200:
        print(f"    ✓ Check-out: {body.get('status')}")
        return True
    return False


# ---------------------------------------------------------------------------
# 10. SERVICES
# ---------------------------------------------------------------------------
def create_hotel_service(service_name, code, price, description=None):
    """Create a global service first, then link to hotel."""
    s_data = {"name": service_name, "code": code, "description": description or "", "category": "OTHER"}
    status, body = api("POST", hq("/services"), s_data)
    if status not in (200, 201) or not body:
        print(f"  ⚠️  Global service '{service_name}' skipped (status={status})")
        return None

    service_id = body["id"]
    # link to hotel with price
    hs_data = {"service_id": service_id, "price": price, "hotel_id": _hotel_id}
    status2, body2 = api("POST", hq("/hotel-services"), hs_data)
    if status2 in (200, 201):
        print(f"  ✓ Service: {service_name} → {price} so'm")
        return body2["id"]
    else:
        print(f"  ⚠️  Hotel linking failed for '{service_name}' (status={status2})")
    return None


# ---------------------------------------------------------------------------
# 11. HOUSEKEEPING
# ---------------------------------------------------------------------------
def create_housekeeping_task(branch_id, room_id, task_type, priority, assigned_to=None, notes=None):
    data = {
        "branch_id": branch_id,
        "room_id": room_id,
        "task_type": task_type,
        "priority": priority,
    }
    if assigned_to:
        data["assigned_to"] = assigned_to
    if notes:
        data["notes"] = notes
    status, body = api("POST", hq("/housekeeping/tasks"), data)
    if status in (200, 201) and body:
        print(f"  ✓ HK Task: {body['task_type']} → {body['status']}  ({body['priority']})")
        return body["id"]
    return None


# ============================================================================
# MAIN
# ============================================================================
def main():
    print("=" * 60)
    print("  GoHotel – Test Data Seeder")
    print("=" * 60)

    if not login():
        sys.exit(1)

    # ----- HOTEL -----
    print("\n🏨 CREATING HOTEL")
    if not create_hotel():
        sys.exit(1)

    # ----- BRANCH -----
    print("\n🏢 BRANCHES")
    branch_main_id = get_main_branch()
    if not branch_main_id:
        print("❌ No main branch found!")
        sys.exit(1)

    branch2_id = create_branch("Grand Plaza – Samarqand", "GPH002", "Samarqand")
    branch3_id = create_branch("Grand Plaza – Buxoro", "GPH003", "Buxoro")

    branches = [
        ("Toshkent (Main)", branch_main_id),
        ("Samarqand", branch2_id),
        ("Buxoro", branch3_id),
    ]

    # ----- FLOORS -----
    print("\n🏗️  FLOORS")
    floor_ids: dict[str, list[str]] = {}  # branch_name → [floor_id, ...]

    for bname, bid in branches:
        if not bid:
            continue
        floors = []
        for fn in range(1, 5):
            fname = f"{bname} – {fn}-qavat"
            fid = create_floor(bid, fn, fname)
            if fid:
                floors.append(fid)
        floor_ids[bname] = floors

    all_floors = [f for fs in floor_ids.values() for f in fs]

    # ----- ROOM TYPES -----
    print("\n🛏️  ROOM TYPES")
    rt_single = create_room_type("Standart (bir kishilik)", 250000, 1, ["WiFi", "TV", "Konditsioner"])
    rt_double = create_room_type("Standart (ikki kishilik)", 380000, 2, ["WiFi", "TV", "Konditsioner", "Mini bar"])
    rt_deluxe = create_room_type("Deluxe", 650000, 2, ["WiFi", "TV", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali"])
    rt_suite = create_room_type("Suite (Lyuks)", 1200000, 4, ["WiFi", "TV", "Konditsioner", "Mini bar", "Jakkuzi", "Seans zali", "Shaxsiy hovuz"])
    rt_family = create_room_type("Family Room", 550000, 5, ["WiFi", "TV", "Konditsioner", "Mini bar", "Bolalar maydonchasi"])

    room_types = [rt_single, rt_double, rt_deluxe, rt_suite, rt_family]
    room_type_prices = {rt_single: 250000, rt_double: 380000, rt_deluxe: 650000, rt_suite: 1200000, rt_family: 550000}

    # ----- ROOMS (Xonalar) -----
    print("\n🚪 ROOMS (Xonalar)")
    room_ids: dict[str, list[str]] = {}  # branch_name → [room_id]

    for bname, bid in branches:
        if not bid:
            continue
        rids = []
        floors = floor_ids.get(bname, [])
        room_num = 101 if bname == "Toshkent (Main)" else 201
        for fid in floors:
            for rt in room_types:
                if not rt:
                    continue
                for i in range(2):  # 2 rooms per type per floor
                    notes = None
                    if i == 0:
                        notes = "Deraza tomondan ko'cha"
                    else:
                        notes = "Deraza tomondan hovli"
                    rid = create_room(bid, fid, rt, str(room_num + i), notes)
                    if rid:
                        rids.append(rid)
                room_num += 2
        room_ids[bname] = rids

    all_rooms = [r for rs in room_ids.values() for r in rs]

    # ----- EMPLOYEES (Xodimlar) -----
    print("\n👨‍💼 EMPLOYEES (Xodimlar)")

    emp_bobur = create_employee(branch_main_id, "bobur", "bobur123", "Bobur", "Ahmedov",
                                "bobur@grandplaza.uz", "+998931112233")
    emp_dilnoza = create_employee(branch_main_id, "dilnoza", "dilnoza123", "Dilnoza", "Karimova",
                                  "dilnoza@grandplaza.uz", "+998931112244")
    emp_sardor = create_employee(branch_main_id, "sardor", "sardor123", "Sardor", "Yusupov",
                                 "sardor@grandplaza.uz", "+998931112255")
    emp_zilola = create_employee(branch_main_id, "zilola", "zilola123", "Zilola", "Norboyeva",
                                 "zilola@grandplaza.uz", "+998931112266")
    emp_jasur = create_employee(branch_main_id, "jasur", "jasur123", "Jasur", "To'xtayev",
                                "jasur@grandplaza.uz", "+998931112277")
    emp_lola = create_employee(branch_main_id, "lola", "lola123", "Lola", "Mirzayeva",
                               "lola@grandplaza.uz", "+998931112288")
    emp_rustam = create_employee(branch2_id, "rustam", "rustam123", "Rustam", "Hamidov",
                                 "rustam@grandplaza.uz", "+998931113311")
    emp_feruza = create_employee(branch3_id, "feruza", "feruza123", "Feruza", "Saidova",
                                 "feruza@grandplaza.uz", "+998931113322")

    # ----- PERMISSIONS -----
    print("\n🔑 PERMISSIONS")
    all_perms = get_all_permissions()
    print(f"  Loaded {len(all_perms)} permission codes")

    reception_perms = [v for k, v in all_perms.items()
                       if k.startswith(("reservation.", "guest.", "room.view", "room.status.update"))]
    cleaner_perms = [v for k, v in all_perms.items() if k.startswith("housekeeping.")]
    finance_perms = [v for k, v in all_perms.items() if k.startswith("finance.")]
    mgr_perms = list(all_perms.values())  # all

    if emp_bobur:
        assign_permissions(emp_bobur, mgr_perms)        # manager – full access
    if emp_dilnoza:
        assign_permissions(emp_dilnoza, reception_perms)  # receptionist
    if emp_sardor:
        assign_permissions(emp_sardor, reception_perms)  # receptionist
    if emp_zilola:
        assign_permissions(emp_zilola, cleaner_perms)    # cleaner
    if emp_jasur:
        assign_permissions(emp_jasur, cleaner_perms)     # cleaner
    if emp_lola:
        assign_permissions(emp_lola, finance_perms)      # accountant
    if emp_rustam:
        assign_permissions(emp_rustam, reception_perms)  # branch2 reception
    if emp_feruza:
        assign_permissions(emp_feruza, reception_perms)  # branch3 reception

    # ----- GUESTS -----
    print("\n🧑 GUESTS (Mehmonlar)")
    g1 = create_guest("Akmal", "Rashidov", "+998971112233", "akmal@mail.uz",
                      "AA1234567", "UZ", "1990-03-15")
    g2 = create_guest("Shahlo", "Tursunova", "+998971112244", "shahlo@mail.uz",
                      "AB2345678", "UZ", "1992-07-22")
    g3 = create_guest("James", "Smith", "+447766554433", "james@email.co.uk",
                      "UK7890123", "GB", "1985-11-03")
    g4 = create_guest("Aktoty", "Bakytkyzy", "+77775556677", "aktoty@mail.kz",
                      "KZ3456789", "KZ", "1995-05-10")
    g5 = create_guest("Rustam", "Boltayev", "+998971113355", "rustam@mail.uz",
                      "AC4567890", "UZ", "1988-01-28")
    g6 = create_guest("Gulnora", "Ismoilova", "+998971113366", "gulnora@mail.uz",
                      "AD5678901", "UZ", "1993-09-14")
    g7 = create_guest("Michael", "Brown", "+12025551234", "mbrown@email.com",
                      "US6789012", "US", "1980-06-20")
    g8 = create_guest("Otabek", "Jalolov", "+998971113377", "otabek@mail.uz",
                      "AE6789012", "UZ", "1996-12-05")

    guests = [g1, g2, g3, g4, g5, g6, g7, g8]

    # ----- RESERVATIONS -----
    print("\n📅 RESERVATIONS (Bronlar)")
    t_main_rooms = room_ids.get("Toshkent (Main)", [])
    t_sam_rooms = room_ids.get("Samarqand", [])
    t_bux_rooms = room_ids.get("Buxoro", [])

    # Active check-ins (today)
    res1_id, _ = create_reservation(g1, t_main_rooms[0], branch_main_id,
                                    "2026-06-24", "2026-06-26", adults=1, children=0, notes="Erta tongda keladi")
    if res1_id:
        do_check_in(res1_id)

    res2_id, _ = create_reservation(g2, t_main_rooms[5], branch_main_id,
                                    "2026-06-23", "2026-06-27", adults=2, children=1, notes="Bola bilan")
    if res2_id:
        do_check_in(res2_id)

    # Upcoming
    create_reservation(g3, t_main_rooms[10], branch_main_id,
                       "2026-06-28", "2026-07-03", adults=1, children=0, notes="Chet el fuqarosi – pasport tekshirilsin")
    create_reservation(g4, t_sam_rooms[0], branch2_id,
                       "2026-06-27", "2026-06-30", adults=2, children=2)
    create_reservation(g5, t_main_rooms[15], branch_main_id,
                       "2026-06-25", "2026-06-26", adults=1, children=0)
    create_reservation(g6, t_bux_rooms[0], branch3_id,
                       "2026-07-01", "2026-07-05", adults=2, children=0, notes="To'yni o'tkazish maqsadida")

    # Already checked out (past)
    res7_id, _ = create_reservation(g7, t_main_rooms[3], branch_main_id,
                                    "2026-06-20", "2026-06-22", adults=1)
    if res7_id:
        do_check_in(res7_id)
        do_check_out(res7_id)

    res8_id, _ = create_reservation(g8, t_main_rooms[7], branch_main_id,
                                    "2026-06-22", "2026-06-24", adults=1)
    if res8_id:
        do_check_in(res8_id)
        do_check_out(res8_id)

    # ----- SERVICES -----
    print("\n🍽️  HOTEL SERVICES")
    hs_breakfast = create_hotel_service("Ertalabki nonushta (shved stoli)", "BREAKFAST", 75000)
    hs_lunch = create_hotel_service("Biznes tushlik", "LUNCH", 120000)
    hs_laundry = create_hotel_service("Kirlarni yuvish", "LAUNDRY", 50000)
    hs_airport = create_hotel_service("Aeroportdan transfer", "AIRPORT_TRANSFER", 180000)
    hs_spa = create_hotel_service("SPA – massaj (1 soat)", "SPA_MASSAGE", 250000)
    hs_pool = create_hotel_service("Hovuzga kirish", "POOL_ACCESS", 60000)
    hs_minibar = create_hotel_service("Mini bar to'ldirish", "MINIBAR", 85000)
    hs_parking = create_hotel_service("Avtoturargoh (kunlik)", "PARKING", 35000)
    hs_tour = create_hotel_service("Toshkent bo'ylab ekskursiya", "TOUR_TASHKENT", 200000)

    # ----- HOUSEKEEPING -----
    print("\n🧹 HOUSEKEEPING")
    if t_main_rooms:
        create_housekeeping_task(branch_main_id, t_main_rooms[1], "CLEANING", "MEDIUM",
                                 emp_zilola, "Kunduzgi tozalash")
        create_housekeeping_task(branch_main_id, t_main_rooms[6], "CLEANING", "HIGH",
                                 emp_jasur, "Tezkor tozalash kerak")
        create_housekeeping_task(branch_main_id, t_main_rooms[12], "MAINTENANCE", "URGENT",
                                 None, "Konditsioner ishlamayapti")
        create_housekeeping_task(branch_main_id, t_main_rooms[18], "INSPECTION", "LOW",
                                 None, "Bo'sh xonani ko'zdan kechirish")
        create_housekeeping_task(branch_main_id, t_main_rooms[4], "TURN_DOWN", "MEDIUM",
                                 emp_zilola, "Kechki tayyorlash")
    if t_sam_rooms:
        create_housekeeping_task(branch2_id, t_sam_rooms[0], "CLEANING", "MEDIUM",
                                 emp_rustam, "Kunduzgi tozalash – Samarqand")
    if t_bux_rooms:
        create_housekeeping_task(branch3_id, t_bux_rooms[0], "CLEANING", "MEDIUM",
                                 emp_feruza, "Kunduzgi tozalash – Buxoro")

    # ----- SUMMARY -----
    print("\n" + "=" * 60)
    print("  ✅ TEST DATA SEEDING COMPLETE")
    print("=" * 60)
    print(f"""
  Hotel:        Grand Plaza Hotel  (id={_hotel_id})
  Branches:     3 (Toshkent, Samarqand, Buxoro)
  Floors:       4 per branch (12 total)
  Room Types:   5
  Rooms:        ~120 (2 rooms × 5 types × 4 floors × 3 branches)
  Employees:    8
  Guests:       8
  Reservations: 8 (2 active, 3 upcoming, 2 checked-out, 1 today)
  Services:     9
  HK Tasks:     7
""")


if __name__ == "__main__":
    main()
