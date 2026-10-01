"""Ish vaqtidan tashqarida xodimni tizimga qo'ymaslik.

Foydalanuvchi talabi: sozlama yoqilsa, ish vaqtidan tashqarida hech bir
xodim ishlay olmasin (masalan farrosh umuman). Lekin:
  * standart — o'chiq, ya'ni yoqilmaguncha hech narsa o'zgarmaydi;
  * administrator va "ish vaqtidan tashqari ham ishlay oladi" belgili xodim
    to'silmaydi, belgini faqat administrator qo'yadi;
  * qabulxonaning bugungi oqimi ("ish vaqti tugadi" → "Davom etish") 100%
    saqlanadi — ochiq smena sessiyasi bor xodim to'silmaydi;
  * mehmonxona to'xtatilgan bo'lsa HOTEL_* xatosi ustun;
  * ilova o'zini tiklashi uchun /auth/me, chiqish va boshqa bir nechta yo'l
    ochiq qoladi.

Bazasiz: sessiya o'rinbosar bilan, HTTP darajasidagi sinovlar esa
`get_db` almashtirilgan holda haqiqiy ilova orqali o'tadi.
"""
import asyncio
import uuid
from datetime import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.application.dto.auth import UserProfileResponse
from app.application.dto.user import EmployeeCreateRequest, EmployeeUpdateRequest, UserResponse
from app.application.services import work_hours_access as wha
from app.application.services.auth_service import AuthService
from app.application.services.user_service import UserService
from app.application.services.work_hours_access import (
    ALLOWED_PATHS,
    OUTSIDE_WORK_HOURS_CODE,
    WORK_HOURS_SETTINGS_KEY,
    assert_within_work_hours,
    evaluate_work_hours_block,
    is_path_allowed,
    outside_flag_change_error,
    resolve_work_hours_settings,
    work_hours_block_error,
)
from app.core.exceptions import ForbiddenException
from app.infrastructure.auth.jwt import create_access_token
from app.infrastructure.database.models.hotel import Hotel
from app.presentation.api.v1 import hotels as hotels_api
from app.presentation.api.v1 import users as users_api
from app.presentation.middleware.auth import get_current_user

ON = {WORK_HOURS_SETTINGS_KEY: {"enforce": True}}
OFF = {WORK_HOURS_SETTINGS_KEY: {"enforce": False}}

NIGHT = time(3, 0)  # 09:00–18:00 xodim uchun ish vaqtidan tashqari
NOON = time(12, 0)

#: Bazadagi xodim qatori: (allow_outside_work_hours, work_start, work_end)
DAY_ROW = (False, "09:00", "18:00")


# ------------------------------------------------------------ o'rinbosarlar --
class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value

    def first(self):
        return self.value


class FakeSession:
    """Faqat to'siq, /auth/me va sozlama endpointlari uchun yetarli sessiya.

    Qaysi so'rovlar ketganini yozib boradi — so'rov narxi ham sinaladi.
    """

    def __init__(self, hotel=None, user_row=DAY_ROW, active_session=False):
        self.hotel = hotel
        # (allow_outside_work_hours, work_start, work_end) yoki None
        self.user_row = user_row
        self.active_session = active_session
        self.gets: list[str] = []
        self.statements: list[str] = []
        self.flushed = False

    async def get(self, model, _key):
        self.gets.append(model.__name__)
        if model is Hotel:
            return self.hotel
        return None

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "shift_sessions" in sql:
            return FakeResult(uuid.uuid4() if self.active_session else None)
        if "FROM users" in sql:
            return FakeResult(self.user_row)
        raise AssertionError(f"Kutilmagan so'rov: {sql}")

    async def flush(self):
        self.flushed = True

    @property
    def shift_queries(self) -> int:
        return sum("shift_sessions" in s for s in self.statements)

    @property
    def user_queries(self) -> int:
        return sum("FROM users" in s for s in self.statements)


def make_hotel(settings=None, status="ACTIVE"):
    return SimpleNamespace(
        id=uuid.uuid4(), name="Grand", status=status, settings=settings or {}
    )


def employee(hotel_id=None, user_type="EMPLOYEE", permissions=None):
    return {
        "id": uuid.uuid4(),
        "user_type": user_type,
        "hotel_id": hotel_id or uuid.uuid4(),
        "branch_id": None,
        "permissions": permissions or [],
        "jti": "j",
        "device_id": None,
    }


@pytest.fixture
def clock(monkeypatch):
    """Mehmonxona mahalliy soatini qotirish."""

    def set_time(value: time):
        monkeypatch.setattr(wha, "local_time_now", lambda: value)

    set_time(NIGHT)
    return set_time


def block(**overrides):
    params = dict(
        user_type="EMPLOYEE",
        enforce=True,
        allow_outside=False,
        work_start="09:00",
        work_end="18:00",
        now=NIGHT,
        has_active_session=False,
    )
    params.update(overrides)
    return work_hours_block_error(**params)


# ---------------------------------------------------------------- sozlama --
def test_setting_is_off_by_default():
    """Sozlama qo'shilishidan oldingi mehmonxonalar avvalgidek ishlaydi."""
    for settings in (None, {}, {WORK_HOURS_SETTINGS_KEY: None}, {WORK_HOURS_SETTINGS_KEY: {}}):
        assert resolve_work_hours_settings(settings) == {"enforce": False}


def test_setting_broken_values_fall_back_to_off():
    """Buzuq yozuv butun jamoani tizimdan chiqarib yubormasin."""
    for broken in ("true", 1, [True], {"enforce": "true"}, {"enforce": 1}, {"enforce": None}):
        assert resolve_work_hours_settings({WORK_HOURS_SETTINGS_KEY: broken}) == {"enforce": False}


def test_setting_round_trip_and_other_keys_untouched():
    assert resolve_work_hours_settings(ON) == {"enforce": True}
    settings = {"shift": {"mode": "cash"}, "nav": {"order": ["/a"]}, **ON}
    new_settings = dict(settings)
    new_settings[WORK_HOURS_SETTINGS_KEY] = resolve_work_hours_settings(
        {WORK_HOURS_SETTINGS_KEY: {"enforce": False}}
    )
    assert new_settings["shift"] == {"mode": "cash"}
    assert new_settings["nav"] == {"order": ["/a"]}
    assert resolve_work_hours_settings(new_settings) == {"enforce": False}


# ------------------------------------------------------------ sof qaror --
def test_off_means_never_blocked():
    assert block(enforce=False) is None


def test_admins_are_never_blocked():
    for user_type in ("ADMIN", "SUPER_ADMIN"):
        assert block(user_type=user_type) is None
    assert block(user_type="") is None


def test_flagged_employee_is_not_blocked():
    assert block(allow_outside=True) is None


def test_day_schedule():
    assert block(now=time(8, 59)) is not None
    assert block(now=time(9, 0)) is None  # boshlanish kiradi
    assert block(now=NOON) is None
    assert block(now=time(17, 59)) is None
    assert block(now=time(18, 0)) is not None  # tugash kirmaydi
    assert block(now=time(23, 30)) is not None


def test_night_schedule_crosses_midnight():
    night = dict(work_start="22:00", work_end="06:00")
    assert block(now=time(22, 0), **night) is None
    assert block(now=time(23, 30), **night) is None
    assert block(now=time(2, 0), **night) is None
    assert block(now=time(5, 59), **night) is None
    assert block(now=time(6, 0), **night) is not None
    assert block(now=NOON, **night) is not None
    assert block(now=time(21, 59), **night) is not None


def test_24h_or_invalid_schedule_never_blocks():
    """Ma'lumot xatosi odamni ishdan to'sib qo'ymasin."""
    for start, end in (
        ("09:00", "09:00"),
        ("00:00", "00:00"),
        (None, None),
        ("", "18:00"),
        ("abc", "18:00"),
        ("09:00", "25:00"),
    ):
        assert block(work_start=start, work_end=end) is None, (start, end)


def test_active_shift_session_exempts():
    """Qabulxona: vaqti tugagan, lekin kassa sessiyasi ochiq — "Davom etish"."""
    assert block(has_active_session=True) is None


def test_error_shape():
    error = block()
    assert isinstance(error, ForbiddenException)
    assert error.status_code == 403
    assert error.error_code == OUTSIDE_WORK_HOURS_CODE == "OUTSIDE_WORK_HOURS"
    # Klientlar HOTEL_* ni "mehmonxona to'xtatilgan" deb tushunadi
    assert not error.error_code.startswith("HOTEL_")
    assert "Ish vaqtingiz emas" in error.detail
    assert "09:00–18:00" in error.detail


# ------------------------------------------------------------ ochiq yo'llar --
def test_allowlist_paths():
    for path in (
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
        "/api/v1/notifications/register-device",
        "/api/v1/hotels/work-hours-settings",
        "/api/v1/auth/face/status",
        "/api/v1/auth/webauthn/passkeys",
        "/api/v1/auth/webauthn/register/options",
        "/api/v1/auth/webauthn/passkeys/123",
    ):
        assert is_path_allowed(path), path
    assert all(is_path_allowed(p) for p in ALLOWED_PATHS)
    # Oxirgi "/" va proksi prefiksi farq qilmaydi
    assert is_path_allowed("/api/v1/auth/me/")
    assert is_path_allowed("/backend/api/v1/auth/me")


def test_everything_else_is_blocked():
    for path in (
        "/api/v1/tasks",
        "/api/v1/problems",
        "/api/v1/shifts/state",
        "/api/v1/shifts/open",
        "/api/v1/reservations/",
        "/api/v1/notifications/",
        "/api/v1/notifications/push-health",
        "/api/v1/auth/face/enroll",
        "/api/v1/auth/meX",
        "/api/v1/hotels/nav-settings",
        "",
        None,
    ):
        assert not is_path_allowed(path), path


# ---------------------------------------------------- belgi — faqat admin --
def test_only_admin_may_change_flag():
    for admin in ("ADMIN", "SUPER_ADMIN"):
        assert outside_flag_change_error(admin, True, current=False) is None
        assert outside_flag_change_error(admin, False, current=True) is None
    # Menejer: o'zgartira olmaydi
    error = outside_flag_change_error("EMPLOYEE", True, current=False)
    assert error is not None and error.status_code == 403 and error.error_code == "FORBIDDEN"
    assert outside_flag_change_error("EMPLOYEE", False, current=True) is not None
    # Yuborilmagan yoki o'zgarmagan — xato emas (tahrir oynasi butun formani yuboradi)
    assert outside_flag_change_error("EMPLOYEE", None, current=True) is None
    assert outside_flag_change_error("EMPLOYEE", False, current=False) is None
    assert outside_flag_change_error("EMPLOYEE", True, current=True) is None


def test_dto_fields():
    assert EmployeeCreateRequest.model_fields["allow_outside_work_hours"].default is None
    assert EmployeeUpdateRequest(allow_outside_work_hours=False).allow_outside_work_hours is False
    assert EmployeeUpdateRequest().model_dump(exclude_none=True) == {}
    # Javobda doim bool (flush qilinmagan obyektda None bo'lsa ham)
    base = dict(
        id=uuid.uuid4(), user_type="EMPLOYEE", hotel_id=None, branch_id=None,
        username="u", first_name="A", last_name="B", email=None, phone=None,
        status="ACTIVE", hire_date=None, termination_date=None, is_deleted=False,
        last_login_at=None, created_at="2026-10-01T00:00:00Z",
    )
    assert UserResponse(**base).allow_outside_work_hours is False
    assert UserResponse(**base, allow_outside_work_hours=None).allow_outside_work_hours is False
    assert UserResponse(**base, allow_outside_work_hours=True).allow_outside_work_hours is True


# -------------------------------------------------------------- to'siq --
def run_gate(session, user, path="/api/v1/tasks"):
    return asyncio.run(assert_within_work_hours(session, user, path))


def test_gate_blocks_employee_outside_hours(clock):
    hotel = make_hotel(ON)
    session = FakeSession(hotel)
    with pytest.raises(ForbiddenException) as err:
        run_gate(session, employee(hotel.id))
    assert err.value.error_code == OUTSIDE_WORK_HOURS_CODE
    assert session.shift_queries == 1


def test_gate_lets_employee_work_in_hours(clock):
    clock(NOON)
    hotel = make_hotel(ON)
    session = FakeSession(hotel)
    run_gate(session, employee(hotel.id))
    # Ish vaqtida smena sessiyasi umuman so'ralmaydi
    assert session.shift_queries == 0


def test_gate_active_session_exempts(clock):
    hotel = make_hotel(ON)
    session = FakeSession(hotel, active_session=True)
    run_gate(session, employee(hotel.id, permissions=["reservation.create"]))
    assert session.shift_queries == 1


def test_gate_flagged_employee(clock):
    hotel = make_hotel(ON)
    session = FakeSession(hotel, user_row=(True, "09:00", "18:00"))
    run_gate(session, employee(hotel.id))
    assert session.shift_queries == 0


def test_gate_night_and_24h_schedules(clock):
    hotel = make_hotel(ON)
    # 22:00–06:00 xodim soat 03:00 da — ishda
    run_gate(FakeSession(hotel, user_row=(False, "22:00", "06:00")), employee(hotel.id))
    # 24 soatlik — hech qachon
    run_gate(FakeSession(hotel, user_row=(False, "08:00", "08:00")), employee(hotel.id))
    clock(NOON)
    with pytest.raises(ForbiddenException):
        run_gate(FakeSession(hotel, user_row=(False, "22:00", "06:00")), employee(hotel.id))


def test_gate_off_costs_one_hotel_lookup(clock):
    """Sozlama o'chiq — faqat mehmonxona (require_active_hotel bilan umumiy)."""
    hotel = make_hotel(OFF)
    session = FakeSession(hotel)
    run_gate(session, employee(hotel.id))
    assert session.gets == ["Hotel"]
    assert session.statements == []


def test_gate_admins_touch_nothing(clock):
    hotel = make_hotel(ON)
    for user_type in ("ADMIN", "SUPER_ADMIN"):
        session = FakeSession(hotel)
        run_gate(session, employee(hotel.id, user_type=user_type))
        assert session.gets == [] and session.statements == []


def test_gate_employee_without_hotel_is_skipped(clock):
    session = FakeSession(make_hotel(ON))
    user = employee()
    user["hotel_id"] = None
    run_gate(session, user)
    assert session.gets == []


def test_gate_allowlisted_path_touches_nothing(clock):
    hotel = make_hotel(ON)
    for path in ("/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/notifications/register-device"):
        session = FakeSession(hotel)
        run_gate(session, employee(hotel.id), path)
        assert session.gets == [] and session.statements == []


def test_gate_hotel_block_takes_precedence(clock):
    """To'xtatilgan mehmonxona — to'siq o'tkazib yuboriladi, HOTEL_* ustun."""
    for status in ("INACTIVE", "SUSPENDED"):
        session = FakeSession(make_hotel(ON, status=status))
        run_gate(session, employee())
        assert session.statements == []  # xodim jadvali ham so'ralmadi
    # Mehmonxona topilmadi — ham o'tkazib yuboriladi
    run_gate(FakeSession(None), employee())


def test_gate_missing_user_row_is_not_blocked(clock):
    hotel = make_hotel(ON)
    run_gate(FakeSession(hotel, user_row=None), employee(hotel.id))


# ---------------------------------------------- get_current_user orqali --
def make_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
        }
    )


def token_for(user_type: str, hotel_id, user_id=None) -> str:
    return create_access_token(
        {
            "sub": str(user_id or uuid.uuid4()),
            "user_type": user_type,
            "hotel_id": str(hotel_id) if hotel_id else None,
            "branch_id": None,
            "permissions": [],
            "jti": "j",
        }
    )


def call_current_user(session, token, path):
    return asyncio.run(
        get_current_user(
            request=make_request(path),
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            session=session,
        )
    )


def test_get_current_user_runs_the_gate(clock):
    hotel = make_hotel(ON)
    with pytest.raises(ForbiddenException) as err:
        call_current_user(FakeSession(hotel), token_for("EMPLOYEE", hotel.id), "/api/v1/tasks")
    assert err.value.error_code == OUTSIDE_WORK_HOURS_CODE

    # /auth/me ochiq — ilova to'siq ekranini ko'rsatishi uchun
    user = call_current_user(FakeSession(hotel), token_for("EMPLOYEE", hotel.id), "/api/v1/auth/me")
    assert user["user_type"] == "EMPLOYEE" and user["hotel_id"] == hotel.id

    # Administrator — hech qachon
    admin = call_current_user(FakeSession(hotel), token_for("ADMIN", hotel.id), "/api/v1/tasks")
    assert admin["user_type"] == "ADMIN"


# ------------------------------------------------------------- /auth/me --
class FakeUserRepo:
    def __init__(self, user):
        self.user = user

    async def get_by_id(self, _id):
        return self.user

    async def get_user_permissions(self, _id):
        return [{"code": "housekeeping.task.update"}]


def make_user(hotel, user_type="EMPLOYEE", allow=False, start="09:00", end="18:00"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_type=user_type,
        hotel_id=hotel.id if hotel else None,
        branch_id=None,
        username="u",
        first_name="A",
        last_name="B",
        email=None,
        phone=None,
        status="ACTIVE",
        work_hours_per_day=8,
        work_start=start,
        work_end=end,
        allow_outside_work_hours=allow,
        last_login_at=None,
    )


def get_me(session, user):
    service = AuthService(session)
    service.user_repo = FakeUserRepo(user)
    profile = asyncio.run(service.get_me(user.id))
    # Javob modeli maydonlarni tashlab yubormasligi kerak
    dumped = UserProfileResponse(**profile).model_dump()
    for key in ("allow_outside_work_hours", "work_hours_enforced", "work_hours_blocked"):
        assert dumped[key] == profile[key]
    return profile


def test_me_reports_blocked_employee(clock):
    hotel = make_hotel(ON)
    me = get_me(FakeSession(hotel), make_user(hotel))
    assert me["hotel_name"] == "Grand"
    assert me["work_hours_enforced"] is True
    assert me["work_hours_blocked"] is True
    assert me["allow_outside_work_hours"] is False


def test_me_not_blocked_in_hours_or_with_session_or_flag(clock):
    hotel = make_hotel(ON)
    me = get_me(FakeSession(hotel, active_session=True), make_user(hotel))
    assert me["work_hours_blocked"] is False and me["work_hours_enforced"] is True

    me = get_me(FakeSession(hotel), make_user(hotel, allow=True))
    assert me["work_hours_blocked"] is False and me["allow_outside_work_hours"] is True

    clock(NOON)
    me = get_me(FakeSession(hotel), make_user(hotel))
    assert me["work_hours_blocked"] is False


def test_me_defaults_when_off_admin_or_no_hotel(clock):
    me = get_me(FakeSession(make_hotel(OFF)), make_user(make_hotel(OFF)))
    assert me["work_hours_enforced"] is False and me["work_hours_blocked"] is False

    hotel = make_hotel(ON)
    me = get_me(FakeSession(hotel), make_user(hotel, user_type="ADMIN"))
    assert me["work_hours_enforced"] is False and me["work_hours_blocked"] is False

    me = get_me(FakeSession(None), make_user(None, user_type="SUPER_ADMIN"))
    assert me["work_hours_enforced"] is False and me["work_hours_blocked"] is False
    assert me["hotel_name"] is None


def test_me_hotel_block_takes_precedence(clock):
    hotel = make_hotel(ON, status="INACTIVE")
    me = get_me(FakeSession(hotel), make_user(hotel))
    assert me["work_hours_blocked"] is False


# ---------------------------------------------------- xodim endpointlari --
def test_manager_cannot_set_flag_on_create(monkeypatch):
    created = {}

    async def fake_create(self, data):
        created.update(data)
        return SimpleNamespace(**data)

    monkeypatch.setattr(UserService, "create_employee", fake_create)
    hotel_id, branch_id = uuid.uuid4(), uuid.uuid4()
    body = dict(
        hotel_id=hotel_id, branch_id=branch_id, first_name="A", last_name="B",
        username="newbie", password="secret1",
    )
    manager = employee(hotel_id, permissions=["employee.create"])

    with pytest.raises(ForbiddenException) as err:
        asyncio.run(
            users_api.create_employee(
                data=EmployeeCreateRequest(**body, allow_outside_work_hours=True),
                session=None,
                current_user=manager,
            )
        )
    assert err.value.error_code == "FORBIDDEN"
    assert created == {}

    # false yoki yuborilmagan — avvalgidek yaratiladi
    asyncio.run(
        users_api.create_employee(
            data=EmployeeCreateRequest(**body, allow_outside_work_hours=False),
            session=None,
            current_user=manager,
        )
    )
    assert created["allow_outside_work_hours"] is False

    # Administrator — belgi bilan
    asyncio.run(
        users_api.create_employee(
            data=EmployeeCreateRequest(**body, allow_outside_work_hours=True),
            session=None,
            current_user=employee(hotel_id, user_type="ADMIN"),
        )
    )
    assert created["allow_outside_work_hours"] is True


def test_manager_cannot_change_flag_on_update(monkeypatch):
    target = SimpleNamespace(id=uuid.uuid4(), allow_outside_work_hours=False)
    updates = []

    async def fake_get(self, user_id, hotel_id):
        return target

    async def fake_update(self, user_id, hotel_id, data):
        updates.append(data)
        return target

    monkeypatch.setattr(UserService, "get_employee", fake_get)
    monkeypatch.setattr(UserService, "update_employee", fake_update)
    hotel_id = uuid.uuid4()
    manager = employee(hotel_id, permissions=["employee.update"])

    def update(data, actor):
        return asyncio.run(
            users_api.update_employee(
                employee_id=target.id, data=data, hotel_id=None, session=None,
                current_user=actor,
            )
        )

    # O'zini ham, boshqani ham ozod qila olmaydi
    with pytest.raises(ForbiddenException) as err:
        update(EmployeeUpdateRequest(allow_outside_work_hours=True), manager)
    assert err.value.error_code == "FORBIDDEN"
    assert updates == []

    # Joriy qiymatni qaytarib yuborish — xato emas
    update(EmployeeUpdateRequest(first_name="Yangi", allow_outside_work_hours=False), manager)
    assert updates[-1] == {"first_name": "Yangi", "allow_outside_work_hours": False}

    # Administrator yoqadi va o'chiradi
    admin = employee(hotel_id, user_type="ADMIN")
    update(EmployeeUpdateRequest(allow_outside_work_hours=True), admin)
    assert updates[-1] == {"allow_outside_work_hours": True}
    target.allow_outside_work_hours = True
    update(EmployeeUpdateRequest(allow_outside_work_hours=False), admin)
    assert updates[-1] == {"allow_outside_work_hours": False}

    # Belgili xodimdan menejer belgini olib tashlay ham olmaydi
    with pytest.raises(ForbiddenException):
        update(EmployeeUpdateRequest(allow_outside_work_hours=False), manager)


# ------------------------------------------------------ HTTP darajasida --
def http(session, method, path, token, json=None):
    """Haqiqiy ilova orqali so'rov — bazasiz (`get_db` almashtiriladi)."""
    from app.core.database import get_db
    from app.main import app

    async def override_get_db():
        yield session

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method, path, json=json, headers={"Authorization": f"Bearer {token}"}
            )

    app.dependency_overrides[get_db] = override_get_db
    try:
        return asyncio.run(go())
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_http_blocked_housekeeper_gets_403_with_code(clock):
    hotel = make_hotel(ON)
    response = http(FakeSession(hotel), "GET", "/api/v1/tasks", token_for("EMPLOYEE", hotel.id))
    assert response.status_code == 403
    body = response.json()
    assert body["error_code"] == "OUTSIDE_WORK_HOURS"
    assert body["detail"].startswith("Ish vaqtingiz emas (09:00–18:00)")


def test_http_hotel_block_wins_over_work_hours(clock):
    hotel = make_hotel(ON, status="INACTIVE")
    response = http(FakeSession(hotel), "GET", "/api/v1/employees/", token_for("EMPLOYEE", hotel.id))
    assert response.status_code == 403
    assert response.json()["error_code"] == "HOTEL_INACTIVE"


def test_http_settings_routes_before_hotel_catch_all(clock):
    """/hotels/work-hours-settings `/{hotel_id}` deb tahlil qilinmasligi kerak."""
    hotel = make_hotel(ON)
    # To'silgan xodim ham o'qiy oladi (ochiq yo'l)
    response = http(
        FakeSession(hotel), "GET", "/api/v1/hotels/work-hours-settings",
        token_for("EMPLOYEE", hotel.id),
    )
    assert response.status_code == 200
    assert response.json() == {"enforce": True}

    # Mehmonxonasiz (super admin) — standart
    response = http(FakeSession(None), "GET", "/api/v1/hotels/work-hours-settings", token_for("SUPER_ADMIN", None))
    assert response.json() == {"enforce": False}


def test_http_only_admin_saves_setting(clock):
    clock(NOON)
    hotel = make_hotel({"shift": {"mode": "cash"}})
    session = FakeSession(hotel)
    response = http(
        session, "PUT", "/api/v1/hotels/work-hours-settings",
        token_for("EMPLOYEE", hotel.id), json={"enforce": True},
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "FORBIDDEN"
    assert WORK_HOURS_SETTINGS_KEY not in hotel.settings

    response = http(
        session, "PUT", "/api/v1/hotels/work-hours-settings",
        token_for("ADMIN", hotel.id), json={"enforce": True},
    )
    assert response.status_code == 200
    assert response.json() == {"enforce": True}
    assert hotel.settings == {"shift": {"mode": "cash"}, "work_hours": {"enforce": True}}
    assert session.flushed

    # Super admin boshqa mehmonxonani ?hotel_id= bilan
    other = make_hotel(ON)
    response = http(
        FakeSession(other), "PUT", f"/api/v1/hotels/work-hours-settings?hotel_id={other.id}",
        token_for("SUPER_ADMIN", None), json={"enforce": False},
    )
    assert response.status_code == 200
    assert other.settings[WORK_HOURS_SETTINGS_KEY] == {"enforce": False}


def test_settings_endpoint_functions_directly():
    """Endpoint funksiyalari — mehmonxona konteksti yo'q holatlar."""
    data = hotels_api.WorkHoursSettingsRequest(enforce=True)
    with pytest.raises(ForbiddenException):
        asyncio.run(
            hotels_api.save_work_hours_settings(
                data=data, hotel_id=None, session=FakeSession(None),
                current_user=employee(user_type="SUPER_ADMIN") | {"hotel_id": None},
            )
        )
    out = asyncio.run(
        hotels_api.get_work_hours_settings(
            hotel_id=None, session=FakeSession(None),
            current_user=employee() | {"hotel_id": None},
        )
    )
    assert out == {"enforce": False}


def test_evaluate_uses_preloaded_user_without_query(clock):
    hotel = make_hotel(ON)
    session = FakeSession(hotel)
    user = make_user(hotel, start="22:00", end="06:00")
    result = asyncio.run(
        evaluate_work_hours_block(
            session, user_type="EMPLOYEE", hotel_id=hotel.id, user_id=user.id,
            hotel=hotel, user=user,
        )
    )
    assert result is None
    assert session.gets == [] and session.statements == []


# ------------------------------- HTTP: to'liq oqim (ilova ko'radigan holat) --
class FullResult(FakeResult):
    """`select(User)` / ruxsatlar ro'yxati uchun ham yetarli natija."""

    def __init__(self, value=None, items=()):
        super().__init__(value)
        self.items = list(items)

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.items), first=lambda: self.value)


class AppSession(FakeSession):
    """Haqiqiy ilova orqali `/auth/me` ham ishlashi uchun sessiya.

    `select(User)` (butun obyekt) — foydalanuvchi; to'siqning ustunli
    so'rovi — (belgi, boshlanish, tugash); ruxsatlar — bo'sh.
    """

    def __init__(self, hotel, user, active_session=False):
        super().__init__(
            hotel,
            user_row=(user.allow_outside_work_hours, user.work_start, user.work_end),
            active_session=active_session,
        )
        self.user = user

    async def execute(self, statement):
        sql = str(statement)
        if "FROM permissions" in sql:
            self.statements.append(sql)
            return FullResult(items=[])
        if "FROM users" in sql and "users.password_hash" in sql:
            self.statements.append(sql)
            return FullResult(self.user)
        return await super().execute(statement)


def test_http_me_is_open_and_reports_block(clock):
    """To'silgan xodim: /auth/me 200 va `work_hours_blocked` — ilova shunga
    qarab to'siq ekranini ko'rsatadi; boshqa yo'l esa 403."""
    hotel = make_hotel(ON)
    user = make_user(hotel)
    token = token_for("EMPLOYEE", hotel.id, user.id)

    response = http(AppSession(hotel, user), "GET", "/api/v1/auth/me", token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["work_hours_blocked"] is True
    assert body["work_hours_enforced"] is True
    assert body["allow_outside_work_hours"] is False
    assert (body["work_start"], body["work_end"]) == ("09:00", "18:00")

    response = http(AppSession(hotel, user), "GET", "/api/v1/hotels/booking-settings", token)
    assert response.status_code == 403
    assert response.json()["error_code"] == OUTSIDE_WORK_HOURS_CODE

    # Ish vaqti boshlandi — /auth/me ham, ish ham ochiq
    clock(NOON)
    response = http(AppSession(hotel, user), "GET", "/api/v1/auth/me", token)
    assert response.json()["work_hours_blocked"] is False
    response = http(AppSession(hotel, user), "GET", "/api/v1/hotels/booking-settings", token)
    assert response.status_code == 200


def test_http_exemptions_reach_the_endpoint(clock):
    """Haqiqiy ilova orqali: ochiq kassa sessiyasi ("Davom etish" oqimi),
    belgili xodim, administrator va o'chiq sozlama — endpointgacha yetadi."""
    hotel = make_hotel(ON)
    path = "/api/v1/hotels/booking-settings"

    receptionist = make_user(hotel)
    response = http(
        AppSession(hotel, receptionist, active_session=True), "GET", path,
        token_for("EMPLOYEE", hotel.id, receptionist.id),
    )
    assert response.status_code == 200, response.text
    me = http(
        AppSession(hotel, receptionist, active_session=True), "GET", "/api/v1/auth/me",
        token_for("EMPLOYEE", hotel.id, receptionist.id),
    ).json()
    assert me["work_hours_blocked"] is False

    flagged = make_user(hotel, allow=True)
    response = http(
        AppSession(hotel, flagged), "GET", path, token_for("EMPLOYEE", hotel.id, flagged.id)
    )
    assert response.status_code == 200

    admin = make_user(hotel, user_type="ADMIN")
    response = http(AppSession(hotel, admin), "GET", path, token_for("ADMIN", hotel.id, admin.id))
    assert response.status_code == 200

    off = make_hotel(OFF)
    worker = make_user(off)
    response = http(AppSession(off, worker), "GET", path, token_for("EMPLOYEE", off.id, worker.id))
    assert response.status_code == 200


def test_http_refresh_is_never_gated(clock):
    """/auth/refresh autentifikatsiyasiz: to'silgan xodimning access tokeni
    sarlavhada bo'lsa ham to'siq ishlamaydi (aks holda "Davom etish" bosgan
    resepshn token muddati tugaganda tizimdan chiqib ketardi)."""
    hotel = make_hotel(ON)
    user = make_user(hotel)
    token = token_for("EMPLOYEE", hotel.id, user.id)
    response = http(
        AppSession(hotel, user), "POST", "/api/v1/auth/refresh", token,
        json={"refresh_token": "not-a-refresh-token"},
    )
    # Token yaroqsiz — 401, lekin 403 OUTSIDE_WORK_HOURS EMAS
    assert response.status_code == 401
    assert response.json().get("error_code") != OUTSIDE_WORK_HOURS_CODE


def test_http_encoded_delimiter_does_not_open_allowlist(clock):
    """`%3F` (dekodlangan `?`) dan keyingi qism `request.url.path` da
    kesilardi — ochiq yo'l bo'lib ko'rinib, to'siq chetlab o'tilardi.
    To'siq marshrutlash solishtiradigan `scope["path"]` ga qaraydi."""
    hotel = make_hotel(ON)
    user = make_user(hotel)
    token = token_for("EMPLOYEE", hotel.id, user.id)
    for method, path in (
        ("PUT", "/api/v1/notifications/register-device%3F/read"),
        ("PUT", "/api/v1/notifications/register-device%23/read"),
    ):
        response = http(AppSession(hotel, user), method, path, token)
        assert response.status_code == 403, (path, response.status_code, response.text)
        assert response.json()["error_code"] == OUTSIDE_WORK_HOURS_CODE


def test_get_current_user_checks_the_routed_path(clock):
    """`get_current_user` `scope["path"]` ni beradi, `request.url.path` ni emas."""
    hotel = make_hotel(ON)
    with pytest.raises(ForbiddenException) as err:
        call_current_user(
            FakeSession(hotel), token_for("EMPLOYEE", hotel.id),
            "/api/v1/notifications/register-device?/read",
        )
    assert err.value.error_code == OUTSIDE_WORK_HOURS_CODE


def test_whitespace_is_not_trimmed_into_allowlist():
    # `/api/v1/auth/me%20` hech qaysi marshrutga tushmaydi — ochiq ham emas
    assert not is_path_allowed("/api/v1/auth/me ")
    assert not is_path_allowed("/api/v1/notifications/register-device\t")
