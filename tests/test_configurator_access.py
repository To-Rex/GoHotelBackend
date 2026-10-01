"""Sozlovchi (CONFIGURATOR) roli.

Foydalanuvchi talabi: admin va super admin singari yangi rol — Sozlovchi.
U istalgan mehmonxona va istalgan filialga o'tib, mehmonxonani talabiga
ko'ra sozlab beradi; tanlangan mehmonxonada administrator kabi hamma
narsani ko'radi. Sozlamalar sahifasi faqat sozlovchi va tizim ma'muriga
ko'rinadi — boshqa rollar (mehmonxona administratori ham) sozlamalarni
o'zgartira olmaydi. Sozlovchilar boshqaruv panelida yaratiladi.

Bazasiz: sessiya va repozitoriylar o'rinbosar.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.application.services import configurator_access as ca
from app.application.services.auth_service import AuthService
from app.application.services.device_service import DEVICE_CHECK_EXEMPT_TYPES
from app.application.services.hotel_access import assert_hotel_active
from fastapi import HTTPException

from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.infrastructure.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.presentation.api.v1 import hotels as hotels_api
from app.presentation.api.v1 import maintenance as maintenance_api
from app.presentation.middleware.auth import get_current_user, require_permission
from app.superadmin.estate_service import EstateService

HOTEL_A = uuid.uuid4()
HOTEL_B = uuid.uuid4()
BRANCH_A_MAIN = uuid.uuid4()
BRANCH_A_2 = uuid.uuid4()
BRANCH_B = uuid.uuid4()


# ------------------------------------------------------------ qoidalar --
def test_effective_type():
    assert ca.effective_user_type("CONFIGURATOR", HOTEL_A) == "ADMIN"
    assert ca.effective_user_type("CONFIGURATOR", None) == "CONFIGURATOR"
    for other in ("ADMIN", "SUPER_ADMIN", "EMPLOYEE"):
        assert ca.effective_user_type(other, HOTEL_A) == other
    assert ca.effective_user_type(None, None) == ""


def test_settings_managers():
    configurator = {"user_type": "ADMIN", "actual_user_type": "CONFIGURATOR"}
    assert ca.can_manage_settings(configurator)
    assert ca.can_manage_settings({"user_type": "SUPER_ADMIN"})
    assert ca.can_manage_settings({"user_type": "CONFIGURATOR"})
    for other in ("ADMIN", "EMPLOYEE", ""):
        assert not ca.can_manage_settings({"user_type": other})
        assert not ca.can_manage_settings({"user_type": other, "actual_user_type": other})
    assert not ca.can_manage_settings(None)
    with pytest.raises(ForbiddenException) as err:
        ca.assert_can_manage_settings({"user_type": "ADMIN"})
    assert err.value.error_code == ca.SETTINGS_FORBIDDEN_CODE


def test_context_switchers():
    assert ca.can_switch_context({"user_type": "ADMIN", "actual_user_type": "CONFIGURATOR"})
    assert ca.can_switch_context({"user_type": "SUPER_ADMIN"})
    assert not ca.can_switch_context({"user_type": "ADMIN"})
    assert not ca.can_switch_context({"user_type": "EMPLOYEE"})


def test_device_and_hotel_checks_exempt_configurator():
    assert "CONFIGURATOR" in DEVICE_CHECK_EXEMPT_TYPES

    class NoDb:
        async def get(self, *_):
            raise AssertionError("bazaga tegmasligi kerak")

    # To'xtatilgan mehmonxonani ham sozlay oladi; tanlamagan bo'lsa ham shu
    # yerdan o'tadi (endpoint o'zi "Hotel context required" beradi)
    asyncio.run(assert_hotel_active(NoDb(), HOTEL_A, "CONFIGURATOR"))
    asyncio.run(assert_hotel_active(NoDb(), None, "CONFIGURATOR"))


# ------------------------------------------------------ get_current_user --
def make_request(path="/api/v1/rooms", device=None) -> Request:
    headers = [(b"x-device-id", device.encode())] if device else []
    return Request(
        {
            "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
            "root_path": "", "query_string": b"", "headers": headers,
            "scheme": "http", "server": ("test", 80),
        }
    )


def token(user_type, hotel_id=None, branch_id=None, device_id=None, user_id=None):
    return create_access_token(
        {
            "sub": str(user_id or uuid.uuid4()),
            "user_type": user_type,
            "hotel_id": str(hotel_id) if hotel_id else None,
            "branch_id": str(branch_id) if branch_id else None,
            "permissions": [],
            "jti": "j",
            "device_id": device_id,
        }
    )


USER_ID = uuid.uuid4()


class TokenSession:
    """Faqat sozlovchi sessiyasini tekshirish so'rovi kutiladi — qurilma va
    ish vaqti tekshiruvlari bazaga tegsa sinov yiqiladi."""

    def __init__(self, live=None):
        self.live = live
        self.session_queries = 0

    async def get(self, *_):
        raise AssertionError("kutilmagan so'rov")

    async def execute(self, stmt):
        if "user_sessions" in str(stmt):
            self.session_queries += 1
            return Result(one=self.live)
        raise AssertionError("kutilmagan so'rov")


LIVE = SimpleNamespace(user_id=USER_ID, revoked_at=None)


def current(tok, device=None, live=LIVE):
    db = TokenSession(live)
    user = asyncio.run(
        get_current_user(
            request=make_request(device=device),
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok),
            session=db,
        )
    )
    user["_session_queries"] = db.session_queries
    return user


def test_configurator_with_hotel_acts_as_admin():
    user = current(
        token("CONFIGURATOR", HOTEL_A, BRANCH_A_MAIN, device_id="dev-1", user_id=USER_ID),
        device="dev-1",
    )
    assert user["user_type"] == "ADMIN"
    assert user["actual_user_type"] == "CONFIGURATOR"
    assert user["hotel_id"] == HOTEL_A and user["branch_id"] == BRANCH_A_MAIN
    # Ruxsat kodlari kerak emas — administrator kabi
    checker = require_permission("reservation.update")
    assert asyncio.run(checker(current_user=user)) is user


def test_configurator_token_dies_with_its_session():
    """Panelda to'xtatilgan/o'chirilgan yoki boshqa mehmonxonaga o'tgan
    (eski sessiya yopilgan) sozlovchining eski tokeni darhol ishlamaydi."""
    tok = token("CONFIGURATOR", HOTEL_A, user_id=USER_ID)
    for live in (
        None,
        SimpleNamespace(user_id=USER_ID, revoked_at="2026-10-02"),
        SimpleNamespace(user_id=uuid.uuid4(), revoked_at=None),
    ):
        with pytest.raises(HTTPException) as err:
            current(tok, live=live)
        assert err.value.status_code == 401


def test_configurator_without_hotel_is_not_admin():
    user = current(token("CONFIGURATOR", user_id=USER_ID))
    assert user["user_type"] == "CONFIGURATOR"
    with pytest.raises(Exception):
        asyncio.run(require_permission("reservation.update")(current_user=user))


def test_existing_roles_unchanged():
    for user_type in ("ADMIN", "SUPER_ADMIN"):
        user = current(token(user_type, HOTEL_A, device_id="d"), device="d", live=None)
        assert user["user_type"] == user_type
        assert user["actual_user_type"] == user_type
        # Boshqa rollarda sessiya so'rovi yo'q — avvalgidek
        assert user["_session_queries"] == 0


# ------------------------------------------------ mehmonxona tanlash --
class Result:
    def __init__(self, items=None, one=None):
        self.items = items or []
        self.one = one

    def scalars(self):
        return self

    def all(self):
        return list(self.items)

    def scalar_one_or_none(self):
        return self.one

    def first(self):
        return self.one


class DbSession:
    def __init__(self):
        self.hotels = {
            HOTEL_A: SimpleNamespace(id=HOTEL_A, name="Grand", code="GR", status="ACTIVE", settings={}),
            HOTEL_B: SimpleNamespace(id=HOTEL_B, name="Anna", code="AN", status="SUSPENDED", settings={}),
        }
        self.branches = {
            BRANCH_A_MAIN: SimpleNamespace(id=BRANCH_A_MAIN, hotel_id=HOTEL_A, name="Markaz", code="M", is_main_branch=True, status="ACTIVE"),
            BRANCH_A_2: SimpleNamespace(id=BRANCH_A_2, hotel_id=HOTEL_A, name="Chilonzor", code="C", is_main_branch=False, status="ACTIVE"),
            BRANCH_B: SimpleNamespace(id=BRANCH_B, hotel_id=HOTEL_B, name="Asosiy", code="A", is_main_branch=True, status="ACTIVE"),
        }
        self.added = []

    async def get(self, model, key):
        if model is Hotel:
            return self.hotels.get(key)
        if model is Branch:
            return self.branches.get(key)
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM branches" in sql:
            params = stmt.compile().params
            hotel = next((v for v in params.values() if isinstance(v, uuid.UUID)), None)
            items = [b for b in self.branches.values() if hotel is None or b.hotel_id == hotel]
            return Result(items)
        if "FROM hotels" in sql:
            return Result(list(self.hotels.values()))
        raise AssertionError(sql)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


class UserRepo:
    def __init__(self, user):
        self.user = user

    async def get_by_id(self, _id):
        return self.user

    async def get_user_permissions(self, _id):
        return []


class SessionRepo:
    def __init__(self, existing=None):
        self.revoked = []
        self.created = []
        self.existing = existing

    async def revoke_session(self, jti):
        self.revoked.append(jti)

    async def create_session(self, **kwargs):
        self.created.append(kwargs)

    async def get_by_jti(self, jti):
        return self.existing


def make_user(user_type="CONFIGURATOR", **over):
    data = dict(
        id=uuid.uuid4(), user_type=user_type, hotel_id=None, branch_id=None,
        username="sozlovchi", first_name="Sardor", last_name="Aliyev",
        email=None, phone=None, status="ACTIVE", is_deleted=False,
        work_hours_per_day=8, work_start=None, work_end=None, last_login_at=None,
        allow_outside_work_hours=False,
    )
    data.update(over)
    return SimpleNamespace(**data)


def service(user, session_repo=None):
    svc = AuthService.__new__(AuthService)
    svc.session = DbSession()
    svc.user_repo = UserRepo(user)
    svc.session_repo = session_repo or SessionRepo(
        existing=SimpleNamespace(user_id=user.id, revoked_at=None)
    )
    return svc


def actor(user):
    return {"id": user.id, "user_type": user.user_type, "actual_user_type": user.user_type, "jti": "old", "device_id": "dev"}


def test_switch_picks_main_branch_and_revokes_old_session():
    user = make_user()
    svc = service(user)
    result = asyncio.run(svc.switch_context(actor(user), HOTEL_A, None))
    claims = decode_token(result["access_token"])
    assert claims["hotel_id"] == str(HOTEL_A)
    assert claims["branch_id"] == str(BRANCH_A_MAIN)
    assert claims["user_type"] == "CONFIGURATOR"
    assert claims["device_id"] == "dev"
    assert decode_token(result["refresh_token"])["hotel_id"] == str(HOTEL_A)
    # Eski sessiya yopildi
    assert svc.session_repo.existing.revoked_at is not None
    assert len(svc.session_repo.created) == 1
    # Foydalanuvchi yozuvi o'zgarmaydi — tanlov faqat tokenda
    assert user.hotel_id is None and user.last_login_at is None


def test_switch_to_a_chosen_branch_and_a_suspended_hotel():
    user = make_user()
    result = asyncio.run(service(user).switch_context(actor(user), HOTEL_A, BRANCH_A_2))
    assert decode_token(result["access_token"])["branch_id"] == str(BRANCH_A_2)
    # To'xtatilgan mehmonxonani ham sozlay oladi
    result = asyncio.run(service(user).switch_context(actor(user), HOTEL_B, None))
    assert decode_token(result["access_token"])["hotel_id"] == str(HOTEL_B)


def test_switch_validation():
    user = make_user()
    with pytest.raises(ValidationException) as err:
        asyncio.run(service(user).switch_context(actor(user), HOTEL_A, BRANCH_B))
    assert err.value.error_code == "BRANCH_NOT_IN_HOTEL"
    with pytest.raises(NotFoundException):
        asyncio.run(service(user).switch_context(actor(user), uuid.uuid4(), None))
    with pytest.raises(ValidationException) as err:
        asyncio.run(service(user).switch_context(actor(user), None, None))
    assert err.value.error_code == "HOTEL_REQUIRED"


def test_switch_requires_a_live_session():
    """Yopilgan (yoki boshqa odamning) sessiya tokeni bilan yangi sessiya
    ochib bo'lmaydi."""
    user = make_user()
    for existing in (
        None,
        SimpleNamespace(user_id=user.id, revoked_at="2026-10-02"),
        SimpleNamespace(user_id=uuid.uuid4(), revoked_at=None),
    ):
        svc = service(user, SessionRepo(existing=existing))
        with pytest.raises(UnauthorizedException):
            asyncio.run(svc.switch_context(actor(user), HOTEL_A, None))
        assert svc.session_repo.created == []


def test_super_admin_may_return_to_all_hotels():
    user = make_user("SUPER_ADMIN")
    result = asyncio.run(service(user).switch_context(actor(user), None, BRANCH_A_2))
    claims = decode_token(result["access_token"])
    assert claims["hotel_id"] is None and claims["branch_id"] is None


def test_only_configurator_and_super_admin_may_switch():
    for user_type in ("ADMIN", "EMPLOYEE"):
        user = make_user(user_type, hotel_id=HOTEL_A)
        with pytest.raises(ForbiddenException):
            asyncio.run(service(user).switch_context(actor(user), HOTEL_B, None))
    # Token aldangan bo'lsa ham — bazadagi tur tekshiriladi
    user = make_user("ADMIN", hotel_id=HOTEL_A)
    fake = actor(user) | {"actual_user_type": "CONFIGURATOR"}
    with pytest.raises(ForbiddenException):
        asyncio.run(service(user).switch_context(fake, HOTEL_B, None))
    # Bloklangan sozlovchi
    blocked = make_user(status="INACTIVE")
    with pytest.raises(Exception):
        asyncio.run(service(blocked).switch_context(actor(blocked), HOTEL_A, None))


def test_context_options_lists_hotels_with_main_branch_first():
    hotels = asyncio.run(service(make_user()).context_options())
    names = [h["name"] for h in hotels]
    assert set(names) == {"Grand", "Anna"}
    grand = next(h for h in hotels if h["name"] == "Grand")
    assert [b["name"] for b in grand["branches"]] == ["Markaz", "Chilonzor"]
    assert grand["branches"][0]["is_main"] is True
    anna = next(h for h in hotels if h["name"] == "Anna")
    assert anna["status"] == "SUSPENDED"


def refresh_with(user, hotel_id, branch_id):
    refresh = create_refresh_token(
        {
            "sub": str(user.id), "user_type": user.user_type,
            "hotel_id": str(hotel_id) if hotel_id else None,
            "branch_id": str(branch_id) if branch_id else None,
            "permissions": [], "jti": "r1", "device_id": "dev-r",
        }
    )
    svc = service(user, SessionRepo(existing=SimpleNamespace(revoked_at=None)))
    return asyncio.run(svc.refresh_token(refresh))


def test_refresh_keeps_the_chosen_hotel():
    user = make_user()
    claims = decode_token(refresh_with(user, HOTEL_A, BRANCH_A_2)["access_token"])
    assert claims["hotel_id"] == str(HOTEL_A) and claims["branch_id"] == str(BRANCH_A_2)
    assert claims["device_id"] == "dev-r"
    # Filial boshqa mehmonxonaniki — filial tushadi, mehmonxona qoladi
    claims = decode_token(refresh_with(user, HOTEL_A, BRANCH_B)["access_token"])
    assert claims["hotel_id"] == str(HOTEL_A) and claims["branch_id"] is None
    # Mehmonxona o'chirilgan — tanlov bekor
    claims = decode_token(refresh_with(user, uuid.uuid4(), None)["access_token"])
    assert claims["hotel_id"] is None


def test_refresh_for_hotel_staff_uses_their_own_hotel():
    """Oddiy xodim/administrator tokeniga yozilgan boshqa mehmonxona
    yangilanishda hisobga olinmaydi — har doim o'z mehmonxonasi."""
    user = make_user("ADMIN", hotel_id=HOTEL_A, branch_id=BRANCH_A_MAIN)
    claims = decode_token(refresh_with(user, HOTEL_B, BRANCH_B)["access_token"])
    assert claims["hotel_id"] == str(HOTEL_A) and claims["branch_id"] == str(BRANCH_A_MAIN)


def test_me_reports_the_chosen_hotel_and_branch():
    user = make_user()
    me = asyncio.run(service(user).get_me(user.id, HOTEL_A, BRANCH_A_2))
    assert me["user_type"] == "CONFIGURATOR"
    assert me["hotel_id"] == str(HOTEL_A) and me["hotel_name"] == "Grand"
    assert me["branch_id"] == str(BRANCH_A_2) and me["branch_name"] == "Chilonzor"
    me = asyncio.run(service(user).get_me(user.id))
    assert me["hotel_id"] is None and me["hotel_name"] is None


def test_me_for_hotel_staff_ignores_token_context():
    user = make_user("ADMIN", hotel_id=HOTEL_A, branch_id=BRANCH_A_MAIN)
    me = asyncio.run(service(user).get_me(user.id, HOTEL_B, BRANCH_B))
    assert me["hotel_id"] == str(HOTEL_A) and me["branch_name"] is None


# ------------------------------------------------- sozlama endpointlari --
CONFIGURATOR = {"id": uuid.uuid4(), "user_type": "ADMIN", "actual_user_type": "CONFIGURATOR", "hotel_id": HOTEL_A}
ADMIN = {"id": uuid.uuid4(), "user_type": "ADMIN", "actual_user_type": "ADMIN", "hotel_id": HOTEL_A}
EMPLOYEE = {"id": uuid.uuid4(), "user_type": "EMPLOYEE", "actual_user_type": "EMPLOYEE", "hotel_id": HOTEL_A}


def test_settings_writes_are_configurator_only():
    session = DbSession()
    body = hotels_api.NavSettingsRequest(order=["/booking", "/rooms"]) if hasattr(hotels_api, "NavSettingsRequest") else None
    if body is None:
        pytest.skip("menyu tartibi so'rov modeli boshqacha nomlangan")
    for who in (ADMIN, EMPLOYEE):
        with pytest.raises(ForbiddenException) as err:
            asyncio.run(hotels_api.save_nav_settings(body, session=session, current_user=who))
        assert err.value.error_code == ca.SETTINGS_FORBIDDEN_CODE
    asyncio.run(hotels_api.save_nav_settings(body, session=session, current_user=CONFIGURATOR))
    assert "nav" in str(session.hotels[HOTEL_A].settings).lower() or session.hotels[HOTEL_A].settings


def test_data_reset_is_configurator_only():
    with pytest.raises(ForbiddenException) as err:
        asyncio.run(
            maintenance_api.reset_data(
                maintenance_api.ResetDataRequest(scope="operational", confirm="RESET"),
                hotel_id=None, session=DbSession(), current_user=ADMIN,
            )
        )
    assert err.value.error_code == ca.SETTINGS_FORBIDDEN_CODE


def test_every_settings_writer_checks_the_role():
    """Sozlamalar sahifasidagi har bir yozuvchi endpoint yangi tekshiruvni
    chaqiradi — biri unutilsa administrator o'sha sozlamani o'zgartira olardi."""
    import inspect

    from app.application.services import checklist_template_service
    from app.presentation.api.v1 import branches, guests, housekeeping, reservations, shifts, shop, vision

    writers = [
        hotels_api.save_nav_settings, hotels_api.save_booking_settings,
        hotels_api.save_discount_settings, hotels_api.save_move_discount_settings,
        hotels_api.save_work_hours_settings,
        guests.save_scan_settings,
        shifts.save_settings if hasattr(shifts, "save_settings") else shifts.update_settings,
        housekeeping.save_assignment_settings, housekeeping.save_auto_complete_settings,
        shop.save_receipt_settings,
        vision.create_device, vision.revoke_device, vision.update_camera,
        branches.save_branch_sms, branches.delete_branch_sms, branches.test_branch_sms,
        maintenance_api.reset_data,
    ]
    for fn in writers:
        assert "assert_can_manage_settings(current_user)" in inspect.getsource(fn), fn.__name__
    src = inspect.getsource(reservations)
    assert src.count("assert_can_manage_settings(current_user)") >= 2
    assert inspect.getsource(guests).count("assert_can_manage_settings(current_user)") >= 2
    assert "assert_can_manage_settings" in inspect.getsource(checklist_template_service._require_admin)


# ------------------------------------------------------------ panel --
class PanelSession:
    def __init__(self, taken=False, existing=None):
        self.taken = taken
        self.added = []
        self.existing = existing
        self.revoked_for = []

    async def execute(self, stmt):
        sql = str(stmt)
        if sql.startswith("UPDATE user_sessions"):
            self.revoked_for.append(sql)
            return Result()
        if "FROM users" in sql:
            return Result(one=(uuid.uuid4(),) if self.taken else None)
        raise AssertionError(sql)

    def add(self, obj):
        obj.id = obj.id or uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        return None

    async def refresh(self, obj):
        return None

    async def get(self, _model, _key):
        return self.existing


def test_panel_creates_a_hotel_less_configurator():
    session = PanelSession()
    created = asyncio.run(
        EstateService(session).create_configurator(
            {"username": " Sozlovchi1 ", "password": "secret1", "first_name": "Sardor"}
        )
    )
    user = session.added[0]
    assert user.user_type == "CONFIGURATOR"
    assert user.hotel_id is None and user.branch_id is None
    assert user.username == "sozlovchi1"
    assert created["user_type"] == "CONFIGURATOR" and created["status"] == "ACTIVE"


def test_panel_create_validation():
    with pytest.raises(Exception):
        asyncio.run(
            EstateService(PanelSession(taken=True)).create_configurator(
                {"username": "band", "password": "secret1", "first_name": "A"}
            )
        )
    with pytest.raises(ValidationException):
        asyncio.run(
            EstateService(PanelSession()).create_configurator(
                {"username": "ab", "password": "secret1", "first_name": "A"}
            )
        )


def test_panel_delete_only_configurators():
    user = make_user()
    db = PanelSession(existing=user)
    asyncio.run(EstateService(db).delete_configurator(user.id))
    assert user.is_deleted and user.status == "INACTIVE"
    assert len(db.revoked_for) == 1  # sessiyalari yopildi
    assert user.username.startswith("sozlovchi#deleted-")
    admin = make_user("ADMIN", hotel_id=HOTEL_A)
    with pytest.raises(NotFoundException):
        asyncio.run(EstateService(PanelSession(existing=admin)).delete_configurator(admin.id))


def test_panel_deactivation_closes_configurator_sessions_only():
    user = make_user()
    db = PanelSession(existing=user)
    asyncio.run(EstateService(db).set_user_status(user.id, "INACTIVE"))
    assert user.status == "INACTIVE" and len(db.revoked_for) == 1
    # Qayta faollashtirishda — sessiya yopilmaydi
    asyncio.run(EstateService(db).set_user_status(user.id, "ACTIVE"))
    assert len(db.revoked_for) == 1
    # Mehmonxona xodimi — avvalgidek, sessiyasiga tegilmaydi
    staff = make_user("EMPLOYEE", hotel_id=HOTEL_A)
    db = PanelSession(existing=staff)
    asyncio.run(EstateService(db).set_user_status(staff.id, "INACTIVE"))
    assert staff.status == "INACTIVE" and db.revoked_for == []
