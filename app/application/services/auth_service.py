"""
Authentication service — handles login, token creation, and token refresh.
Unified login: checks username against the single `users` table.
JWT contains: sub, user_type, hotel_id, branch_id, permissions[], jti, type.
"""
import logging
from uuid import UUID
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.configurator_access import (
    ADMIN_TYPE,
    CONFIGURATOR_TYPE,
    CONTEXT_SWITCH_TYPES,
)
from app.application.services.hotel_access import assert_hotel_active
from app.application.services.work_hours_access import (
    EMPLOYEE_TYPE,
    evaluate_work_hours_block,
    resolve_work_hours_settings,
)
from app.core.config import settings
from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.infrastructure.auth.jwt import (
    FACE_CHALLENGE_EXPIRE_MINUTES,
    create_access_token,
    create_face_challenge_token,
    create_refresh_token,
    decode_face_challenge_token,
    decode_token,
)
from app.infrastructure.auth.password import verify_password
from app.infrastructure.database.repositories.user_repo import UserRepository, SessionRepository
from app.shared.utils import generate_jti


logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.session_repo = SessionRepository(session)

    async def login(
        self,
        username: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        fcm_token: str | None = None,
        device_id: str | None = None,
    ) -> dict:
        user = await self.user_repo.get_by_username(username)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedException("Invalid username or password", "INVALID_CREDENTIALS")

        if user.status != "ACTIVE":
            raise ForbiddenException("Account is not active", "ACCOUNT_INACTIVE")

        if user.is_deleted:
            raise ForbiddenException("Account has been deleted", "ACCOUNT_DELETED")

        # --- Mehmonxona hamon xizmatdami ---
        #
        # Kirishga ruxsat berib, keyin har bir so'rovni 403 bilan qaytarish
        # eng yomon variant edi: xodim bo'sh, cheksiz yuklanadigan ekranni
        # ko'rardi. Sabab shu yerda, kirish paytida aytiladi.
        await self._assert_hotel_active(user)

        # --- Qurilma tasdiqlangan bo'lishi kerak ---
        #
        # Yuz tekshiruvidan OLDIN: tasdiqlanmagan qurilmada yuz so'rashning
        # ma'nosi yo'q, u baribir kiritmaydi. Administrator bu tekshiruvdan
        # ozod — batafsil izoh device_service da.
        from app.application.services.device_service import DeviceService

        await DeviceService(self.session).ensure_allowed(
            user, device_id, user_agent=user_agent, ip_address=ip_address
        )

        # FCM token faqat so'rovda kelganda yangilanadi — token yubormaydigan
        # klientlar (masalan web) mavjud tokenni o'chirib yubormasligi uchun.
        if fcm_token:
            user.fcm_token = fcm_token

        # --- Ikkinchi bosqich: yuz tekshiruvi ---
        #
        # Yuz biriktirgan xodim uchun parol YETARLI EMAS. Tokenlar bu yerda
        # berilmaydi — o'rniga qisqa muddatli challenge qaytadi va kirish
        # `/face/verify-login` da yakunlanadi. Yuzi yo'q xodim uchun hech
        # narsa o'zgarmaydi: parol avvalgidek yetarli.
        if await self._has_face_profile(user.id):
            return {
                "face_required": True,
                "face_token": create_face_challenge_token(str(user.id)),
                "face_expires_in": FACE_CHALLENGE_EXPIRE_MINUTES * 60,
            }

        return await self.issue_tokens(
            user, ip_address=ip_address, user_agent=user_agent, device_id=device_id
        )

    async def _assert_hotel_active(self, user) -> None:
        """Xodimning mehmonxonasi to'xtatilgan bo'lsa kirishni to'sadi.

        Mehmonxonaga bog'lanmagan hisob (masalan tizim ma'muri) avvalgidek
        kiraveradi — unda tekshiradigan obyekt yo'q.
        """
        if not user.hotel_id:
            return
        await assert_hotel_active(self.session, user.hotel_id, user.user_type)

    async def _has_face_profile(self, user_id) -> bool:
        """Xodimda yuz biriktirilganmi."""
        from sqlalchemy import func, select

        from app.infrastructure.database.models.user_face_profile import (
            UserFaceProfile,
        )

        count = (
            await self.session.execute(
                select(func.count(UserFaceProfile.id)).where(
                    UserFaceProfile.user_id == user_id
                )
            )
        ).scalar() or 0
        return count > 0

    async def resolve_face_challenge(self, face_token: str):
        """Challenge tokendan xodimni topadi va holatini qayta tekshiradi.

        Qayta tekshirish kerak: token berilgandan keyin xodim o'chirilgan yoki
        bloklangan bo'lishi mumkin, besh daqiqa ham buning uchun yetarli.
        """
        user_id = decode_face_challenge_token(face_token)
        if not user_id:
            raise UnauthorizedException(
                "Kirish seansi tugadi — login va parolni qaytadan kiriting",
                "FACE_CHALLENGE_INVALID",
            )
        user = await self.user_repo.get_by_id(UUID(str(user_id)))
        if not user or user.is_deleted or user.status != "ACTIVE":
            raise ForbiddenException("Account is not active", "ACCOUNT_INACTIVE")
        return user

    async def complete_login_without_face(
        self,
        face_token: str,
        reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_id: str | None = None,
    ) -> dict:
        """Kamerasiz qurilmada ikkinchi bosqichni o'tkazib yuborish."""
        user = await self.resolve_face_challenge(face_token)
        note = reason or "kamera topilmadi"
        logger.info(
            "Yuz tekshiruvisiz kirish: %s (sabab: %s)", user.username, note
        )
        # Sabab sessiya yozuviga tushadi — keyin kim qaysi yo'l bilan
        # kirgani ko'rinadi
        return await self.issue_tokens(
            user,
            ip_address=ip_address,
            user_agent=f"{user_agent or ''} [yuzsiz: {note}]".strip(),
            device_id=device_id,
        )

    async def issue_tokens(
        self,
        user,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_id: str | None = None,
        context: tuple | None = None,
    ) -> dict:
        """Access/refresh token juftligini yaratib, sessiya yozuvini saqlaydi.

        Parol bilan login va WebAuthn (Face ID/passkey) login uchun umumiy —
        ikkalasi ham foydalanuvchi allaqachon tekshirilgach shu yerga keladi.

        `context` — (hotel_id, branch_id): sozlovchi yoki tizim ma'muri
        tanlagan mehmonxona va filial (`switch_context`). Berilmasa —
        foydalanuvchi yozuvidagi mehmonxona (avvalgidek).
        """
        # Yuz orqali kirish parol bosqichini chetlab o'tadi — mehmonxona
        # holati bu yerda ham tekshiriladi
        await self._assert_hotel_active(user)

        permissions: list[str] = []
        if user.user_type == "EMPLOYEE":
            permissions = [p["code"] for p in await self.user_repo.get_user_permissions(user.id)]

        if context is None:
            user.last_login_at = datetime.now(timezone.utc)
            token_hotel_id, token_branch_id = user.hotel_id, user.branch_id
        else:
            token_hotel_id, token_branch_id = context

        jti = generate_jti()
        token_data = {
            "sub": str(user.id),
            "user_type": user.user_type,
            "hotel_id": str(token_hotel_id) if token_hotel_id else None,
            "branch_id": str(token_branch_id) if token_branch_id else None,
            "permissions": permissions,
            "jti": jti,
            # Qurilma tokenga bog'lanadi. Sarlavhaga tayanib bo'lmaydi:
            # taqiqlangan qurilma uni yubormay qo'yib, ishlashda davom
            # etardi. Tokendagi qiymatni esa o'zgartirib bo'lmaydi.
            "device_id": device_id,
        }

        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        await self.session_repo.create_session(
            user_id=user.id,
            token_jti=jti,
            refresh_token_hash=refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "id": str(user.id),
                "user_type": user.user_type,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "hotel_id": str(token_hotel_id) if token_hotel_id else None,
                "branch_id": str(token_branch_id) if token_branch_id else None,
                "permissions": permissions,
            },
        }

    async def refresh_token(self, refresh_token: str) -> dict:
        payload = decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise UnauthorizedException("Invalid refresh token", "INVALID_TOKEN")

        jti = payload.get("jti")
        session = await self.session_repo.get_by_jti(jti)
        if not session or session.revoked_at:
            raise UnauthorizedException("Session revoked", "SESSION_REVOKED")

        session.revoked_at = datetime.now(timezone.utc)

        user_id = UUID(payload["sub"])
        user = await self.user_repo.get_by_id(user_id)
        if not user or user.status != "ACTIVE":
            raise UnauthorizedException("User not active", "USER_INACTIVE")

        # Mehmonxona sessiya ochilgandan keyin to'xtatilgan bo'lishi mumkin
        await self._assert_hotel_active(user)

        permissions: list[str] = []
        if user.user_type == "EMPLOYEE":
            permissions = [p["code"] for p in await self.user_repo.get_user_permissions(user.id)]

        # Sozlovchi / tizim ma'muri tanlagan mehmonxona yangilanishda
        # yo'qolmasin — u imzolangan refresh tokenda turadi. Mehmonxona yoki
        # filial o'chirilgan bo'lsa — tanlov bekor, qaytadan tanlanadi.
        token_hotel_id, token_branch_id = user.hotel_id, user.branch_id
        if user.user_type in CONTEXT_SWITCH_TYPES:
            token_hotel_id, token_branch_id = await self._valid_context(
                payload.get("hotel_id"), payload.get("branch_id")
            )
        elif user.user_type == ADMIN_TYPE and user.hotel_id:
            # Administrator tanlagan filial — faqat o'z mehmonxonasida
            _, chosen = await self._valid_context(user.hotel_id, payload.get("branch_id"))
            token_branch_id = chosen or user.branch_id

        new_jti = generate_jti()
        token_data = {
            "sub": str(user.id),
            "user_type": user.user_type,
            "hotel_id": str(token_hotel_id) if token_hotel_id else None,
            "branch_id": str(token_branch_id) if token_branch_id else None,
            "permissions": permissions,
            "jti": new_jti,
            # Qurilma yangilangan tokenda ham qoladi — aks holda yangilashdan
            # keyin qurilma tekshiruvi faqat sarlavhaga tayanib qolardi
            "device_id": payload.get("device_id"),
        }

        new_access = create_access_token(token_data)
        new_refresh = create_refresh_token(token_data)

        await self.session_repo.create_session(
            user_id=user.id,
            token_jti=new_jti,
            refresh_token_hash=new_refresh,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        )

        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
            "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

    async def logout(self, jti: str) -> None:
        await self.session_repo.revoke_session(jti)

    # ------------------------------------------- mehmonxona/filial tanlash --

    async def _valid_context(self, hotel_id, branch_id) -> tuple:
        """Tokendagi tanlovni tekshiradi: mehmonxona hamon bormi, filial shu
        mehmonxonaniki va o'chirilmaganmi. Yaroqsiz bo'lsa — (None, None)
        yoki faqat mehmonxona (filial qaytadan tanlanadi)."""
        from app.infrastructure.database.models.branch import Branch
        from app.infrastructure.database.models.hotel import Hotel

        try:
            hotel_uuid = UUID(str(hotel_id)) if hotel_id else None
        except (ValueError, TypeError):
            hotel_uuid = None
        if hotel_uuid is None or await self.session.get(Hotel, hotel_uuid) is None:
            return None, None
        try:
            branch_uuid = UUID(str(branch_id)) if branch_id else None
        except (ValueError, TypeError):
            branch_uuid = None
        if branch_uuid is not None:
            branch = await self.session.get(Branch, branch_uuid)
            if branch is None or branch.hotel_id != hotel_uuid or getattr(branch, "is_deleted", False):
                branch_uuid = None
        return hotel_uuid, branch_uuid

    async def context_options(self, current_user: dict | None = None) -> list[dict]:
        """Tanlash uchun mehmonxonalar va ularning filiallari.

        Administrator faqat o'z mehmonxonasini (filiallari bilan) ko'radi.
        """
        from sqlalchemy import select

        from app.infrastructure.database.models.branch import Branch
        from app.infrastructure.database.models.hotel import Hotel

        hotel_stmt = select(Hotel).order_by(Hotel.name)
        branch_stmt = select(Branch).order_by(Branch.name)
        actual = (current_user or {}).get("actual_user_type") or (current_user or {}).get("user_type")
        if actual == ADMIN_TYPE:
            own = current_user.get("hotel_id")
            hotel_stmt = hotel_stmt.where(Hotel.id == own)
            branch_stmt = branch_stmt.where(Branch.hotel_id == own)
        hotels = (await self.session.execute(hotel_stmt)).scalars().all()
        branches = (await self.session.execute(branch_stmt)).scalars().all()
        by_hotel: dict = {}
        for branch in branches:
            if getattr(branch, "is_deleted", False):
                continue
            by_hotel.setdefault(branch.hotel_id, []).append(
                {
                    "id": str(branch.id),
                    "name": branch.name,
                    "code": branch.code,
                    "is_main": bool(branch.is_main_branch),
                    "status": branch.status,
                }
            )
        result = []
        for hotel in hotels:
            items = by_hotel.get(hotel.id, [])
            # Asosiy filial birinchi
            items.sort(key=lambda b: (not b["is_main"], b["name"] or ""))
            result.append(
                {
                    "id": str(hotel.id),
                    "name": hotel.name,
                    "code": getattr(hotel, "code", None),
                    "status": hotel.status,
                    "branches": items,
                }
            )
        return result

    async def switch_context(
        self,
        current_user: dict,
        hotel_id: UUID | None,
        branch_id: UUID | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Sozlovchi / tizim ma'muri mehmonxona va filialni tanlaydi.

        Yangi token juftligi beriladi (tanlov tokenda turadi), joriy sessiya
        yopiladi. Filial berilmasa — mehmonxonaning asosiy (yoki birinchi)
        filiali. Sozlovchi mehmonxonasiz qola olmaydi; tizim ma'muri
        `hotel_id=None` bilan avvalgi "barcha mehmonxonalar" holatiga qaytadi.
        """
        from sqlalchemy import select

        from app.infrastructure.database.models.branch import Branch
        from app.infrastructure.database.models.hotel import Hotel

        actual = current_user.get("actual_user_type") or current_user.get("user_type")
        if actual not in CONTEXT_SWITCH_TYPES and actual != ADMIN_TYPE:
            raise ForbiddenException(
                "Mehmonxonani faqat sozlovchi tanlay oladi", "CONTEXT_FORBIDDEN"
            )
        user = await self.user_repo.get_by_id(current_user["id"])
        if not user or user.status != "ACTIVE" or user.is_deleted:
            raise UnauthorizedException("User not active", "USER_INACTIVE")
        if user.user_type not in CONTEXT_SWITCH_TYPES and user.user_type != ADMIN_TYPE:
            raise ForbiddenException(
                "Mehmonxonani faqat sozlovchi tanlay oladi", "CONTEXT_FORBIDDEN"
            )
        if user.user_type == ADMIN_TYPE and (hotel_id is None or hotel_id != user.hotel_id):
            # Administrator boshqa mehmonxonaga o'ta olmaydi — faqat o'z
            # mehmonxonasining filiallari orasida
            raise ForbiddenException(
                "Faqat o'z mehmonxonangiz filialini tanlay olasiz", "CONTEXT_FORBIDDEN"
            )

        if hotel_id is None:
            if user.user_type == CONFIGURATOR_TYPE:
                raise ValidationException("Mehmonxonani tanlang", "HOTEL_REQUIRED")
            branch_id = None
        else:
            hotel = await self.session.get(Hotel, hotel_id)
            if hotel is None:
                raise NotFoundException("Mehmonxona topilmadi", "HOTEL_NOT_FOUND")
            branches = [
                b
                for b in (
                    await self.session.execute(
                        select(Branch).where(Branch.hotel_id == hotel_id)
                    )
                ).scalars().all()
                if not getattr(b, "is_deleted", False)
            ]
            if branch_id is not None:
                if not any(b.id == branch_id for b in branches):
                    raise ValidationException(
                        "Filial bu mehmonxonaga tegishli emas", "BRANCH_NOT_IN_HOTEL"
                    )
            elif branches:
                branches.sort(key=lambda b: (not b.is_main_branch, b.name or ""))
                branch_id = branches[0].id

        # Faqat OCHIQ sessiya bilan: yopilgan (masalan oldingi tanlovdagi)
        # yoki o'g'irlangan eski access token bilan yangi 7 kunlik sessiya
        # ochib bo'lmasin. Eski sessiya yopiladi — tanlov faqat yangi tokenda.
        jti = current_user.get("jti") or ""
        live = await self.session_repo.get_by_jti(jti) if jti else None
        if (
            live is None
            or live.revoked_at is not None
            or str(live.user_id) != str(user.id)
        ):
            raise UnauthorizedException("Session revoked", "SESSION_REVOKED")
        live.revoked_at = datetime.now(timezone.utc)
        return await self.issue_tokens(
            user,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=current_user.get("device_id"),
            context=(hotel_id, branch_id),
        )

    async def get_me(
        self,
        user_id: UUID,
        context_hotel_id: UUID | None = None,
        context_branch_id: UUID | None = None,
    ) -> dict:
        """Joriy foydalanuvchi.

        Sozlovchi va tizim ma'muri uchun mehmonxona/filial — TOKENDAGI tanlov
        (`switch_context`), foydalanuvchi yozuvidagi emas (u bo'sh).
        """
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UnauthorizedException("User not found", "USER_NOT_FOUND")

        own_hotel_id, own_branch_id = user.hotel_id, user.branch_id
        branch_name: str | None = None
        if user.user_type in CONTEXT_SWITCH_TYPES:
            own_hotel_id, own_branch_id = context_hotel_id, context_branch_id
        elif context_branch_id and context_hotel_id == user.hotel_id:
            # Joriy so'rov filiali: xodimda — o'ziniki, administratorda —
            # tanlagani (get_current_user hisoblaydi)
            own_branch_id = context_branch_id
        if own_branch_id:
            from app.infrastructure.database.models.branch import Branch

            branch = await self.session.get(Branch, own_branch_id)
            branch_name = branch.name if branch is not None else None

        permissions: list[str] = []
        if user.user_type == "EMPLOYEE":
            permissions = [p["code"] for p in await self.user_repo.get_user_permissions(user.id)]

        # Foydalanuvchi mehmonxonasining nomi (frontend tab sarlavhasi uchun).
        # SUPER_ADMIN da hotel_id bo'lmasligi mumkin — None qoladi.
        # Mehmonxona to'liq o'qiladi: ish vaqti sozlamasi ham kerak.
        hotel_name: str | None = None
        hotel = None
        if own_hotel_id:
            from app.infrastructure.database.models.hotel import Hotel

            hotel = await self.session.get(Hotel, own_hotel_id)
            hotel_name = hotel.name if hotel is not None else None

        # Ish vaqtidan tashqarida ishlash — `get_current_user` dagi to'siq
        # bilan AYNAN bir xil qaror (bitta funksiya)
        work_hours_enforced = False
        work_hours_blocked = False
        if user.user_type == EMPLOYEE_TYPE and hotel is not None:
            from app.application.services.branch_settings import settings_owner

            owner = await settings_owner(self.session, hotel.id, user.branch_id)
            work_hours_enforced = resolve_work_hours_settings(
                owner.settings if owner else None
            )["enforce"]
            work_hours_blocked = (
                await evaluate_work_hours_block(
                    self.session,
                    user_type=user.user_type,
                    hotel_id=user.hotel_id,
                    user_id=user.id,
                    hotel=hotel,
                    user=user,
                )
                is not None
            )

        return {
            "id": str(user.id),
            "user_type": user.user_type,
            "hotel_id": str(own_hotel_id) if own_hotel_id else None,
            "hotel_name": hotel_name,
            "branch_id": str(own_branch_id) if own_branch_id else None,
            "branch_name": branch_name,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "status": user.status,
            "permissions": permissions,
            "work_hours_per_day": user.work_hours_per_day or 8,
            "work_start": user.work_start or "09:00",
            "work_end": user.work_end or "18:00",
            "allow_outside_work_hours": bool(getattr(user, "allow_outside_work_hours", False)),
            "work_hours_enforced": work_hours_enforced,
            "work_hours_blocked": work_hours_blocked,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        }
