#!/usr/bin/env python3
"""
GoHotel ERP — Comprehensive API Test Suite
Usage: source .venv/bin/activate && python tests/run_all_tests.py
"""
import asyncio
import sys
import os
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from app.core.config import settings
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models.user import User
from app.infrastructure.database.models.permission import Permission
from app.infrastructure.auth.password import hash_password

BASE_URL = f"http://{settings.HOST}:{settings.PORT}"
PASSED = 0
FAILED = 0
RESULTS = []


def ok(name: str, detail: str = ""):
    global PASSED
    PASSED += 1
    RESULTS.append(("PASS", name, detail))
    print(f"  ✅ {name}")


def fail(name: str, detail: str = ""):
    global FAILED
    FAILED += 1
    RESULTS.append(("FAIL", name, detail))
    print(f"  ❌ {name} — {detail}")


async def seed_superadmin():
    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.user_type == "SUPER_ADMIN"))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                user_type="SUPER_ADMIN",
                username="admin",
                password_hash=hash_password("admin123"),
                first_name="Super",
                last_name="Admin",
                status="ACTIVE",
            )
            session.add(user)
            await session.commit()
        return user.username, "admin123"


async def seed_permissions():
    from app.infrastructure.database.models.permission import Permission as Perm
    from sqlalchemy import select

    perms = [
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
        ("report.view", "View Reports", "report"),
        ("report.export", "Export Reports", "report"),
        ("employee.view", "View Employees", "employee"),
        ("employee.create", "Create Employee", "employee"),
        ("employee.update", "Update Employee", "employee"),
        ("employee.manage", "Manage Permissions", "employee"),
        ("service.view", "View Services", "service"),
        ("service.manage", "Manage Services", "service"),
    ]

    async with async_session_factory() as session:
        for code, name, module in perms:
            r = await session.execute(select(Perm).where(Perm.code == code))
            if not r.scalar_one_or_none():
                session.add(Perm(code=code, name=name, module=module))
        await session.commit()


class Tester:
    def __init__(self, base_url: str):
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=True)
        self.token: str | None = None
        self.headers: dict = {}
        self.hotel_id: str | None = None
        self.branch_id: str | None = None
        self.admin_token: str | None = None
        self.admin_headers: dict = {}
        self.employee_token: str | None = None
        self.employee_headers: dict = {}
        self.employee_id: str | None = None

    def auth(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    async def _req(self, method: str, path: str, json_data=None, params=None, headers=None, expect_status=200):
        h = headers or self.headers
        fn = getattr(self.client, method.lower())
        
        # Auto-append hotel_id for ALL requests when hotel_id is set
        exclude = ["auth", "health", "/permissions", "/services/", "/services?"]
        if self.hotel_id and "hotel_id" not in path and not any(e in path for e in exclude):
            if method.lower() == "get":
                if params is None:
                    params = {}
                if "hotel_id" not in params:
                    params["hotel_id"] = self.hotel_id
            else:
                sep = "&" if "?" in path else "?"
                path = f"{path}{sep}hotel_id={self.hotel_id}"
        
        kwargs = {"headers": h}
        if method.lower() == "get":
            if params:
                kwargs["params"] = params
            elif json_data:
                kwargs["params"] = json_data
        else:
            if json_data:
                kwargs["json"] = json_data
            if params:
                kwargs["params"] = params
        resp = await fn(path, **kwargs)
        if resp.status_code != expect_status:
            body = resp.text[:300]
            raise Exception(f"Expected {expect_status}, got {resp.status_code}: {body}")
        return resp.json() if resp.text else {}

    async def close(self):
        await self.client.aclose()

    # ─── AUTH ─────────────────────────────────
    async def test_auth(self):
        print("\n📌 AUTH — Authentication")
        resp = await self._req("post", "/api/v1/auth/login", {"username": "admin", "password": "admin123"})
        assert "access_token" in resp, "Missing access_token"
        self.auth(resp["access_token"])
        refresh_token = resp.get("refresh_token")
        ok("POST /auth/login", f"token obtained, expires_in={resp.get('expires_in')}")

        resp = await self._req("get", "/api/v1/auth/me")
        assert resp["user_type"] == "SUPER_ADMIN"
        ok("GET /auth/me", f"user: {resp['user_type']}")

        resp = await self._req("post", "/api/v1/auth/refresh", {"refresh_token": refresh_token})
        assert "access_token" in resp
        ok("POST /auth/refresh", "new tokens issued")

        resp = await self._req("post", "/api/v1/auth/logout", expect_status=200)
        ok("POST /auth/logout", "logged out")

    # ─── HOTELS ──────────────────────────────
    async def test_hotels(self):
        print("\n📌 HOTELS — Hotel Management")
        code = f"THA{datetime.now(timezone.utc).strftime('%H%M%S')}"
        resp = await self._req("post", "/api/v1/hotels", {
            "name": f"Test Hotel {code}",
            "code": code,
            "stars": 4,
            "phone": "+77771112233",
            "email": "info@testhotel.kz",
            "city": "Astana",
            "country": "Kazakhstan",
        }, expect_status=200)
        self.hotel_id = resp["id"]
        ok("POST /hotels — create", f"id={self.hotel_id[:8]}...")

        resp = await self._req("get", "/api/v1/hotels")
        assert len(resp) >= 1
        ok("GET /hotels — list", f"count={len(resp)}")

        resp = await self._req("get", f"/api/v1/hotels/{self.hotel_id}")
        assert resp["name"].startswith("Test Hotel")
        ok("GET /hotels/{id} — detail")

        resp = await self._req("put", f"/api/v1/hotels/{self.hotel_id}", {"stars": 5})
        assert resp["stars"] == 5
        ok("PUT /hotels/{id} — update")

        resp = await self._req("patch", f"/api/v1/hotels/{self.hotel_id}/status", {"status": "ACTIVE"})
        ok("PATCH /hotels/{id}/status")

    # ─── BRANCHES ────────────────────────────
    async def test_branches(self):
        print("\n📌 BRANCHES")
        self.auth(self.token)  # re-auth if needed
        resp = await self._req("get", "/api/v1/branches", headers=self.headers)
        assert len(resp) >= 1
        self.branch_id = resp[0]["id"]
        ok("GET /branches — list", f"main branch auto-created, id={self.branch_id[:8]}...")

        resp = await self._req("post", "/api/v1/branches", {
            "name": "Branch 2",
            "code": f"B2{datetime.now(timezone.utc).strftime('%H%M%S')}",
            "city": "Almaty",
        })
        branch2_id = resp["id"]
        ok("POST /branches — create", f"id={branch2_id[:8]}...")

        resp = await self._req("get", f"/api/v1/branches/{self.branch_id}")
        ok("GET /branches/{id} — detail")

        resp = await self._req("put", f"/api/v1/branches/{branch2_id}", {"name": "Branch 2 Updated"})
        ok("PUT /branches/{id} — update")

        resp = await self._req("get", f"/api/v1/branches/{self.branch_id}/floors")
        ok("GET /branches/{id}/floors", f"floors count={len(resp)}")

    # ─── FLOORS ──────────────────────────────
    async def test_floors(self):
        print("\n📌 FLOORS")
        floor_num = int(datetime.now(timezone.utc).strftime('%H%M%S')) % 200
        resp = await self._req("post", "/api/v1/floors", {
            "branch_id": self.branch_id,
            "floor_number": floor_num,
            "name": f"Floor {floor_num}",
        })
        floor_id = resp["id"]
        ok("POST /floors — create", f"id={floor_id[:8]}...")

        resp = await self._req("get", "/api/v1/floors", params={"branch_id": self.branch_id})
        assert len(resp) >= 1
        ok("GET /floors — list", f"count={len(resp)}")

        resp = await self._req("put", f"/api/v1/floors/{floor_id}", {"floor_number": floor_num, "name": f"Floor {floor_num}+"})
        ok("PUT /floors/{id} — update")
        return floor_id

    # ─── ROOM TYPES ──────────────────────────
    async def test_room_types(self):
        print("\n📌 ROOM TYPES")
        rt_name = f"Deluxe Room {datetime.now(timezone.utc).strftime('%H%M%S')}"
        resp = await self._req("post", "/api/v1/room-types", {
            "name": rt_name,
            "base_price": 35000,
            "capacity": 2,
        })
        type_id = resp["id"]
        ok("POST /room-types — create", f"id={type_id[:8]}...")

        resp = await self._req("get", "/api/v1/room-types")
        assert len(resp) >= 1
        ok("GET /room-types — list", f"count={len(resp)}")

        resp = await self._req("get", f"/api/v1/room-types/{type_id}")
        ok("GET /room-types/{id} — detail")

        resp = await self._req("put", f"/api/v1/room-types/{type_id}", {"base_price": 40000})
        ok("PUT /room-types/{id} — update")

        resp = await self._req("patch", f"/api/v1/room-types/{type_id}/status?is_active=true")
        ok("PATCH /room-types/{id}/status")
        return type_id

    # ─── ROOMS ───────────────────────────────
    async def test_rooms(self, floor_id: str, room_type_id: str):
        print("\n📌 ROOMS")
        t = int(datetime.now(timezone.utc).strftime('%H%M%S'))
        room_num1 = str(t % 900 + 100)  # 100-999
        room_num2 = str((t + 1) % 900 + 100)
        
        resp = await self._req("post", "/api/v1/rooms", {
            "branch_id": self.branch_id,
            "floor_id": floor_id,
            "room_type_id": room_type_id,
            "room_number": room_num1,
        })
        room_id = resp["id"]
        ok("POST /rooms — create", f"room {room_num1}, id={room_id[:8]}...")

        resp = await self._req("post", "/api/v1/rooms", {
            "branch_id": self.branch_id,
            "floor_id": floor_id,
            "room_type_id": room_type_id,
            "room_number": room_num2,
        })
        room2_id = resp["id"]
        ok("POST /rooms — create room 102")

        resp = await self._req("get", "/api/v1/rooms", params={"branch_id": self.branch_id})
        assert len(resp) >= 2
        ok("GET /rooms — list", f"count={len(resp)}")

        resp = await self._req("get", f"/api/v1/rooms/{room_id}")
        assert resp["room_number"] == room_num1
        ok("GET /rooms/{id} — detail")

        resp = await self._req("put", f"/api/v1/rooms/{room_id}", {"notes": "Test note"})
        ok("PUT /rooms/{id} — update")

        resp = await self._req("patch", f"/api/v1/rooms/{room_id}/status", {"status": "CLEANING"})
        assert resp["current_status"] == "CLEANING"
        ok("PATCH /rooms/{id}/status — to CLEANING")

        resp = await self._req("patch", f"/api/v1/rooms/{room_id}/status", {"status": "AVAILABLE"})
        ok("PATCH /rooms/{id}/status — back to AVAILABLE")

        resp = await self._req("get", f"/api/v1/rooms/{room_id}/status-history")
        assert len(resp) >= 2
        ok("GET /rooms/{id}/status-history", f"{len(resp)} entries")

        resp = await self._req("get", "/api/v1/rooms/available", params={
            "check_in": str(date.today()),
            "check_out": str(date.today() + timedelta(days=3)),
        })
        ok("GET /rooms/available", f"{len(resp)} available")

        return room_id, room2_id

    # ─── GUESTS ──────────────────────────────
    async def test_guests(self):
        print("\n📌 GUESTS")
        resp = await self._req("post", "/api/v1/guests", {
            "first_name": "Nursultan",
            "last_name": "Testov",
            "phone": "+77011234567",
            "email": "guest@test.kz",
            "passport_number": "N12345678",
            "nationality": "KZ",
            "birth_date": "1990-01-15",
        })
        guest_id = resp["id"]
        ok("POST /guests — create", f"id={guest_id[:8]}...")

        resp = await self._req("get", "/api/v1/guests")
        assert len(resp) >= 1
        ok("GET /guests — list", f"count={len(resp)}")

        resp = await self._req("get", "/api/v1/guests", params={"query": "Nursultan"})
        assert len(resp) >= 1
        ok("GET /guests?query= — search")

        resp = await self._req("get", f"/api/v1/guests/{guest_id}")
        assert resp["first_name"] == "Nursultan"
        ok("GET /guests/{id} — detail")

        resp = await self._req("put", f"/api/v1/guests/{guest_id}", {"phone": "+77019876543"})
        ok("PUT /guests/{id} — update")

        return guest_id

    # ─── RESERVATIONS ────────────────────────
    async def test_reservations(self, guest_id: str, room_id: str):
        print("\n📌 RESERVATIONS")
        tomorrow = str(date.today() + timedelta(days=1))
        day_after = str(date.today() + timedelta(days=3))

        resp = await self._req("post", "/api/v1/reservations", {
            "guest_id": guest_id,
            "room_id": room_id,
            "branch_id": self.branch_id,
            "check_in_date": tomorrow,
            "check_out_date": day_after,
            "adults": 2,
            "children": 0,
        })
        reservation_id = resp["id"]
        reservation_number = resp.get("reservation_number", "N/A")
        ok("POST /reservations — create", f"#{reservation_number}")

        resp = await self._req("get", "/api/v1/reservations", params={"hotel_id": self.hotel_id})
        assert len(resp) >= 1
        ok("GET /reservations — list", f"count={len(resp)}")

        resp = await self._req("get", f"/api/v1/reservations/{reservation_id}")
        ok("GET /reservations/{id} — detail")

        resp = await self._req("get", "/api/v1/reservations/availability", params={
            "check_in": tomorrow,
            "check_out": day_after,
        })
        ok("GET /reservations/availability", f"{len(resp)} rooms available")

        resp = await self._req("get", "/api/v1/reservations/calendar", params={
            "view": "monthly",
            "date": str(date.today()),
        })
        ok("GET /reservations/calendar — monthly view")

        resp = await self._req("post", f"/api/v1/reservations/{reservation_id}/cancel", {"reason": "Test cancel"})
        ok("POST /reservations/{id}/cancel")

        return reservation_number

    # ─── EMPLOYEES & ADMIN ───────────────────────
    async def test_users(self):
        print("\n📌 EMPLOYEES & USERS")
        # Create admin
        resp = await self._req("post", "/api/v1/employees", {
            "hotel_id": self.hotel_id,
            "branch_id": self.branch_id,
            "username": f"manager_{datetime.now(timezone.utc).strftime('%H%M%S')}",
            "password": "manager123",
            "first_name": "Aidar",
            "last_name": "Manager",
            "email": "manager@test.kz",
        }, expect_status=200)
        self.employee_id = resp["id"]
        ok("POST /employees — create employee", f"id={self.employee_id[:8]}...")

        resp = await self._req("get", "/api/v1/employees", params={"hotel_id": self.hotel_id})
        assert len(resp) >= 1
        ok("GET /employees — list", f"count={len(resp)}")

        resp = await self._req("get", f"/api/v1/employees/{self.employee_id}", params={"hotel_id": self.hotel_id})
        ok("GET /employees/{id} — detail")

        resp = await self._req("put", f"/api/v1/employees/{self.employee_id}", {"first_name": "Aidar Updated"})
        ok("PUT /employees/{id} — update")

    # ─── PERMISSIONS ─────────────────────────
    async def test_permissions(self):
        print("\n📌 PERMISSIONS")
        resp = await self._req("get", "/api/v1/permissions")
        assert len(resp) > 0
        ok("GET /permissions — list", f"{len(resp)} permissions")

        resp = await self._req("get", "/api/v1/permissions/modules")
        ok("GET /permissions/modules", f"{len(resp)} modules")

        # Get permissions list
        all_perms = await self._req("get", "/api/v1/permissions")
        perm_ids = [p["id"] for p in all_perms[:5]]  # assign first 5

        resp = await self._req("get", f"/api/v1/permissions/{self.employee_id}/permissions")
        ok("GET /permissions/{eid}/permissions — current")

        resp = await self._req("put", f"/api/v1/permissions/{self.employee_id}/permissions", {
            "permission_ids": perm_ids,
        })
        ok("PUT /permissions/{eid}/permissions — bulk assign")

        resp = await self._req("get", f"/api/v1/permissions/{self.employee_id}/permissions")
        assert len(resp["permissions"]) > 0
        ok("GET /permissions/{eid}/permissions — after assign")

        resp = await self._req("delete", f"/api/v1/permissions/{self.employee_id}/permissions/{perm_ids[-1]}")
        ok("DELETE /permissions/{eid}/permissions/{pid} — revoke")

    # ─── SERVICES ────────────────────────────
    async def test_services(self):
        print("\n📌 SERVICES")
        resp = await self._req("get", "/api/v1/services")
        ok("GET /services — global catalog", f"count={len(resp)}")

        resp = await self._req("post", "/api/v1/services", {
            "name": "Test Service",
            "code": f"TEST_SVC_{datetime.now(timezone.utc).strftime('%H%M%S')}",
            "description": "Test",
            "category": "TestCat",
        })
        svc_id = resp["id"]
        ok("POST /services — create", f"id={svc_id[:8]}...")

        resp = await self._req("put", f"/api/v1/services/{svc_id}", {"name": "Test Svc Updated"})
        ok("PUT /services/{id} — update")

        # Hotel services
        resp = await self._req("get", "/api/v1/hotel-services")
        ok("GET /hotel-services — list", f"count={len(resp)}")

        resp = await self._req("post", "/api/v1/hotel-services", {
            "service_id": svc_id,
            "price": 5000,
        })
        hs_id = resp["id"]
        ok("POST /hotel-services — enable", f"price={resp.get('price')}")

        resp = await self._req("put", f"/api/v1/hotel-services/{hs_id}", {"price": 7500})
        ok("PUT /hotel-services/{id} — update price")

        resp = await self._req("delete", f"/api/v1/hotel-services/{hs_id}")
        ok("DELETE /hotel-services/{id} — disable")

    # ─── HOUSEKEEPING ────────────────────────
    async def test_housekeeping(self, room_id: str):
        print("\n📌 HOUSEKEEPING")
        resp = await self._req("post", "/api/v1/housekeeping/tasks", {
            "branch_id": self.branch_id,
            "room_id": room_id,
            "task_type": "CLEANING",
            "priority": "HIGH",
            "notes": "Post check-out cleaning",
            "scheduled_date": str(date.today()),
        })
        task_id = resp["id"]
        ok("POST /housekeeping/tasks — create", f"id={task_id[:8]}...")

        resp = await self._req("get", "/api/v1/housekeeping/tasks")
        assert len(resp) >= 1
        ok("GET /housekeeping/tasks — list", f"count={len(resp)}")

        resp = await self._req("get", "/api/v1/housekeeping/tasks/open")
        assert len(resp) >= 1
        ok("GET /housekeeping/tasks/open")

        resp = await self._req("get", f"/api/v1/housekeeping/tasks/{task_id}")
        ok("GET /housekeeping/tasks/{id} — detail")

        resp = await self._req("put", f"/api/v1/housekeeping/tasks/{task_id}", {"notes": "Updated notes"})
        ok("PUT /housekeeping/tasks/{id} — update")

        resp = await self._req("post", f"/api/v1/housekeeping/tasks/{task_id}/assign", {"assigned_to": self.employee_id})
        ok("POST /housekeeping/tasks/{id}/assign")

        resp = await self._req("patch", f"/api/v1/housekeeping/tasks/{task_id}/status", {"status": "IN_PROGRESS"})
        assert resp["status"] == "IN_PROGRESS"
        ok("PATCH /housekeeping/tasks/{id}/status — IN_PROGRESS")

        resp = await self._req("patch", f"/api/v1/housekeeping/tasks/{task_id}/status", {"status": "COMPLETED"})
        assert resp["status"] == "COMPLETED"
        ok("PATCH /housekeeping/tasks/{id}/status — COMPLETED")

    # ─── FINANCE ─────────────────────────────
    async def test_finance(self, guest_id: str, room_id: str, room2_id: str):
        print("\n📌 FINANCE")
        # Create a reservation first to invoice
        tomorrow = str(date.today() + timedelta(days=5))
        day_after = str(date.today() + timedelta(days=7))

        resp = await self._req("post", "/api/v1/reservations", {
            "guest_id": guest_id,
            "room_id": room2_id,
            "branch_id": self.branch_id,
            "check_in_date": tomorrow,
            "check_out_date": day_after,
            "adults": 1,
        })
        reservation_id = resp["id"]

        resp = await self._req("get", "/api/v1/finance/ledgers")
        ok("GET /finance/ledgers", f"{len(resp)} accounts")

        resp = await self._req("post", "/api/v1/finance/invoices", {
            "reservation_id": reservation_id,
        })
        invoice_id = resp["id"]
        invoice_number = resp.get("invoice_number", "N/A")
        ok("POST /finance/invoices — create", f"#{invoice_number}")

        resp = await self._req("get", "/api/v1/finance/invoices")
        assert len(resp) >= 1
        ok("GET /finance/invoices — list", f"count={len(resp)}")

        resp = await self._req("get", f"/api/v1/finance/invoices/{invoice_id}")
        ok("GET /finance/invoices/{id} — detail")

        resp = await self._req("post", f"/api/v1/finance/invoices/{invoice_id}/pay", {
            "invoice_id": invoice_id,
            "amount": 10000,
            "payment_method": "CASH",
        })
        ok("POST /finance/invoices/{id}/pay — payment")

        resp = await self._req("get", "/api/v1/finance/payments", params={"invoice_id": invoice_id})
        assert len(resp) >= 1
        ok("GET /finance/payments")

        resp = await self._req("get", "/api/v1/finance/journal-entries")
        ok("GET /finance/journal-entries", f"count={len(resp)}")

    # ─── REPORTS ─────────────────────────────
    async def test_reports(self):
        print("\n📌 REPORTS")
        resp = await self._req("post", "/api/v1/reports/generate", {
            "report_type": "occupancy",
            "start_date": str(date.today()),
            "end_date": str(date.today() + timedelta(days=30)),
        })
        report_id = resp["id"]
        ok("POST /reports/generate — occupancy", f"id={report_id[:8]}...")

        resp = await self._req("post", "/api/v1/reports/generate", {
            "report_type": "revenue",
            "start_date": str(date.today()),
            "end_date": str(date.today() + timedelta(days=30)),
        })
        ok("POST /reports/generate — revenue")

        resp = await self._req("get", "/api/v1/reports")
        assert len(resp) >= 1
        ok("GET /reports — saved", f"count={len(resp)}")

        resp = await self._req("get", f"/api/v1/reports/{report_id}")
        ok("GET /reports/{id}")

    # ─── AUDIT LOGS ──────────────────────────
    async def test_audit_logs(self):
        print("\n📌 AUDIT LOGS")
        resp = await self._req("get", "/api/v1/audit-logs")
        assert len(resp) >= 1
        ok("GET /audit-logs", f"{len(resp)} entries")

        resp = await self._req("get", "/api/v1/audit-logs", params={"entity_type": "Hotel"})
        assert len(resp) >= 1
        ok("GET /audit-logs?entity_type=Hotel")

        resp = await self._req("get", "/api/v1/audit-logs", params={"action": "hotel.created"})
        assert len(resp) >= 1
        ok("GET /audit-logs?action=hotel.created")

    # ─── NOTIFICATIONS ───────────────────────
    async def test_notifications(self):
        print("\n📌 NOTIFICATIONS")
        resp = await self._req("get", "/api/v1/notifications")
        ok("GET /notifications — user", f"count={len(resp)}")

        resp = await self._req("get", "/api/v1/notifications/broadcasts")
        ok("GET /notifications/broadcasts")

    # ─── FILES ───────────────────────────────
    async def test_files(self):
        print("\n📌 FILES")
        # Test upload without actual file — metadata test
        resp = await self._req("get", "/api/v1/files/00000000-0000-0000-0000-000000000001", expect_status=404)
        ok("GET /files/{id} — 404 for non-existent")

    # ─── SOFT DELETE ─────────────────────────
    async def test_soft_delete(self, room2_id: str):
        print("\n📌 SOFT DELETE")
        resp = await self._req("delete", f"/api/v1/rooms/{room2_id}")
        ok("DELETE /rooms/{id} — soft delete")

        # Should still exist but is_deleted=True
        resp = await self._req("get", f"/api/v1/rooms/{room2_id}")
        assert resp["is_deleted"] is True
        ok("GET /rooms/{id} — soft deleted room accessible")


async def main():
    print("=" * 60)
    print("  GoHotel ERP — Full API Test Suite")
    print("=" * 60)

    # Seed data
    print("\n🔧 Seeding test data...")
    await seed_superadmin()
    await seed_permissions()
    print("   ✅ SuperAdmin + permissions seeded")

    t = Tester(BASE_URL)

    try:
        # Re-login for fresh token
        resp = await t.client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        token = resp.json()["access_token"]
        t.auth(token)

        await t.test_auth()

        # Re-login after logout in test_auth()
        resp = await t.client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        t.auth(resp.json()["access_token"])

        await t.test_hotels()
        await t.test_branches()
        floor_id = await t.test_floors()
        room_type_id = await t.test_room_types()
        room_id, room2_id = await t.test_rooms(floor_id, room_type_id)
        guest_id = await t.test_guests()
        await t.test_reservations(guest_id, room_id)
        await t.test_users()
        await t.test_permissions()
        await t.test_services()
        await t.test_housekeeping(room_id)
        await t.test_finance(guest_id, room_id, room2_id)
        await t.test_reports()
        await t.test_audit_logs()
        await t.test_notifications()
        await t.test_files()
        await t.test_soft_delete(room2_id)

    except Exception as e:
        fail("FATAL", str(e))
    finally:
        await t.close()

    # Summary
    print("\n" + "=" * 60)
    total = PASSED + FAILED
    print(f"  TOTAL:  {total}")
    print(f"  PASSED: {PASSED} ({PASSED / total * 100:.0f}%)" if total else "  PASSED: 0")
    print(f"  FAILED: {FAILED}")
    print("=" * 60)

    return FAILED == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
