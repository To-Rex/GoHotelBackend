"""Barcha mehmonxonalar va filiallarni boshqarish.

Panelning asosiy ishi shu: tizimda nechta mehmonxona bor, ular qanday
holatda, har birida nechta filial, xona va xodim bor. Oddiy foydalanuvchi
uchun bunday ko'rinish yo'q — u faqat o'z mehmonxonasini ko'radi.

Bu yerdagi so'rovlar `hotel_id` bo'yicha CHEKLANMAYDI — bu ataylab va
aynan shu sabab modul alohida turadi: mehmonxona doirasidagi kod bilan
aralashib ketsa, kimdir tasodifan cheklovsiz so'rovni oddiy endpointda
ishlatib yuborishi mumkin edi.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.audit_log import AuditLog
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.user import User

HOTEL_STATUSES = ("ACTIVE", "INACTIVE", "SUSPENDED")


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


class EstateService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------------------------------------------------- umumiy holat --

    async def overview(self) -> dict:
        """Tizim bo'yicha yig'ma raqamlar — panelning bosh sahifasi."""

        async def count(model, *conditions) -> int:
            stmt = select(func.count(model.id))
            if conditions:
                stmt = stmt.where(*conditions)
            return int((await self.session.execute(stmt)).scalar() or 0)

        return {
            "hotels": await count(Hotel),
            "hotels_active": await count(Hotel, Hotel.status == "ACTIVE"),
            "branches": await count(Branch),
            "rooms": await count(Room, Room.is_deleted.is_(False)),
            "users": await count(User, User.is_deleted.is_(False)),
            "guests": await count(Guest),
            "reservations": await count(
                Reservation, Reservation.is_deleted.is_(False)
            ),
            "reservations_active": await count(
                Reservation,
                Reservation.is_deleted.is_(False),
                Reservation.status.in_(("CONFIRMED", "CHECKED_IN")),
            ),
        }

    # ------------------------------------------------------ mehmonxona --

    async def list_hotels(self, search: str | None = None) -> list[dict]:
        stmt = select(Hotel)
        text = (search or "").strip()
        if text:
            like = f"%{text}%"
            stmt = stmt.where(
                or_(
                    Hotel.name.ilike(like),
                    Hotel.code.ilike(like),
                    Hotel.city.ilike(like),
                )
            )
        hotels = (
            (await self.session.execute(stmt.order_by(Hotel.name))).scalars().all()
        )
        if not hotels:
            return []

        ids = [h.id for h in hotels]
        branches = await self._count_by_hotel(Branch, ids)
        rooms = await self._count_by_hotel(Room, ids, Room.is_deleted.is_(False))
        users = await self._count_by_hotel(User, ids, User.is_deleted.is_(False))

        return [
            {
                **self._hotel_dict(hotel),
                "branch_count": branches.get(hotel.id, 0),
                "room_count": rooms.get(hotel.id, 0),
                "user_count": users.get(hotel.id, 0),
            }
            for hotel in hotels
        ]

    async def _count_by_hotel(self, model, hotel_ids, *conditions) -> dict:
        stmt = (
            select(model.hotel_id, func.count(model.id))
            .where(model.hotel_id.in_(hotel_ids), *conditions)
            .group_by(model.hotel_id)
        )
        return {row[0]: row[1] for row in (await self.session.execute(stmt)).all()}

    async def get_hotel(self, hotel_id: UUID) -> dict:
        hotel = await self._hotel(hotel_id)
        return self._hotel_dict(hotel, full=True)

    async def create_hotel(self, data: dict) -> dict:
        name = _clean(data.get("name"))
        code = (_clean(data.get("code")) or "").upper()
        if not name:
            raise ValidationException("Mehmonxona nomi kerak", "NAME_REQUIRED")
        if not code:
            raise ValidationException("Mehmonxona kodi kerak", "CODE_REQUIRED")
        if await self._code_taken(code):
            raise ConflictException("Bu kod band", "CODE_TAKEN")

        hotel = Hotel(
            name=name,
            code=code,
            description=_clean(data.get("description")),
            stars=int(data.get("stars") or 3),
            phone=_clean(data.get("phone")),
            email=_clean(data.get("email")),
            address_line1=_clean(data.get("address_line1")),
            city=_clean(data.get("city")),
            country=_clean(data.get("country")),
            status=(data.get("status") or "ACTIVE").upper(),
        )
        self.session.add(hotel)
        await self.session.flush()
        await self.session.refresh(hotel)
        return self._hotel_dict(hotel, full=True)

    async def update_hotel(self, hotel_id: UUID, data: dict) -> dict:
        hotel = await self._hotel(hotel_id)
        if "code" in data and data["code"]:
            code = str(data["code"]).strip().upper()
            if code != hotel.code and await self._code_taken(code):
                raise ConflictException("Bu kod band", "CODE_TAKEN")
            hotel.code = code
        for field in (
            "name", "description", "phone", "email", "address_line1",
            "city", "country",
        ):
            if field in data:
                setattr(hotel, field, _clean(data[field]))
        if data.get("stars") is not None:
            hotel.stars = int(data["stars"])
        if data.get("status"):
            status = str(data["status"]).upper()
            if status not in HOTEL_STATUSES:
                raise ValidationException("Noma'lum holat", "INVALID_STATUS")
            hotel.status = status
        await self.session.flush()
        await self.session.refresh(hotel)
        return self._hotel_dict(hotel, full=True)

    async def delete_hotel(self, hotel_id: UUID) -> dict:
        """Mehmonxonani O'CHIRMAYDI, faqat to'xtatadi.

        Mehmonxona yozuvi bronlar, to'lovlar va hisobotlar bilan
        bog'langan — uni bazadan olib tashlash o'sha tarixni ham
        yo'q qilardi. Shuning uchun holat `INACTIVE` ga o'tadi va
        xodimlar tizimga kira olmaydi.
        """
        hotel = await self._hotel(hotel_id)
        hotel.status = "INACTIVE"
        await self.session.flush()
        return self._hotel_dict(hotel, full=True)

    async def _code_taken(self, code: str) -> bool:
        return (
            await self.session.execute(select(Hotel.id).where(Hotel.code == code))
        ).first() is not None

    async def _hotel(self, hotel_id: UUID) -> Hotel:
        hotel = await self.session.get(Hotel, hotel_id)
        if hotel is None:
            raise NotFoundException("Mehmonxona topilmadi", "HOTEL_NOT_FOUND")
        return hotel

    @staticmethod
    def _hotel_dict(hotel: Hotel, full: bool = False) -> dict:
        data = {
            "id": str(hotel.id),
            "name": hotel.name,
            "code": hotel.code,
            "status": hotel.status,
            "stars": hotel.stars,
            "city": hotel.city,
            "country": hotel.country,
            "phone": hotel.phone,
            "email": hotel.email,
        }
        if full:
            data.update(
                {
                    "description": hotel.description,
                    "address_line1": hotel.address_line1,
                    "created_at": (
                        hotel.created_at.isoformat() if hotel.created_at else None
                    ),
                }
            )
        return data

    # --------------------------------------------------------- filial --

    async def list_branches(self, hotel_id: UUID) -> list[dict]:
        await self._hotel(hotel_id)
        rows = (
            (
                await self.session.execute(
                    select(Branch)
                    .where(Branch.hotel_id == hotel_id)
                    .order_by(Branch.is_main_branch.desc(), Branch.name)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        counts = await self._count_by_branch([b.id for b in rows])
        return [
            {**self._branch_dict(b), "room_count": counts.get(b.id, 0)} for b in rows
        ]

    async def _count_by_branch(self, branch_ids) -> dict:
        stmt = (
            select(Room.branch_id, func.count(Room.id))
            .where(Room.branch_id.in_(branch_ids), Room.is_deleted.is_(False))
            .group_by(Room.branch_id)
        )
        return {row[0]: row[1] for row in (await self.session.execute(stmt)).all()}

    async def create_branch(self, hotel_id: UUID, data: dict) -> dict:
        await self._hotel(hotel_id)
        name = _clean(data.get("name"))
        code = (_clean(data.get("code")) or "").upper()
        if not name:
            raise ValidationException("Filial nomi kerak", "NAME_REQUIRED")
        if not code:
            raise ValidationException("Filial kodi kerak", "CODE_REQUIRED")
        if await self._branch_code_taken(hotel_id, code):
            raise ConflictException("Bu kod shu mehmonxonada band", "CODE_TAKEN")

        branch = Branch(
            hotel_id=hotel_id,
            name=name,
            code=code,
            phone=_clean(data.get("phone")),
            email=_clean(data.get("email")),
            address_line1=_clean(data.get("address_line1")),
            city=_clean(data.get("city")),
            country=_clean(data.get("country")),
            is_main_branch=bool(data.get("is_main_branch", False)),
        )
        self.session.add(branch)
        await self.session.flush()
        await self.session.refresh(branch)
        return self._branch_dict(branch)

    async def update_branch(self, branch_id: UUID, data: dict) -> dict:
        branch = await self._branch(branch_id)
        if "code" in data and data["code"]:
            code = str(data["code"]).strip().upper()
            if code != branch.code and await self._branch_code_taken(
                branch.hotel_id, code
            ):
                raise ConflictException("Bu kod shu mehmonxonada band", "CODE_TAKEN")
            branch.code = code
        for field in ("name", "phone", "email", "address_line1", "city", "country"):
            if field in data:
                setattr(branch, field, _clean(data[field]))
        if data.get("is_main_branch") is not None:
            branch.is_main_branch = bool(data["is_main_branch"])
        await self.session.flush()
        await self.session.refresh(branch)
        return self._branch_dict(branch)

    async def delete_branch(self, branch_id: UUID) -> None:
        """Filialni o'chiradi — faqat unda xona qolmagan bo'lsa.

        Xonasi bor filialni o'chirish bronlar zanjirini uzib yuborardi;
        bunday holatda avval xonalarni ko'chirish kerak.
        """
        branch = await self._branch(branch_id)
        rooms = (
            await self.session.execute(
                select(func.count(Room.id)).where(
                    Room.branch_id == branch.id, Room.is_deleted.is_(False)
                )
            )
        ).scalar() or 0
        if rooms:
            raise ConflictException(
                f"Filialda {rooms} ta xona bor — avval ularni ko'chiring",
                "BRANCH_NOT_EMPTY",
            )
        await self.session.delete(branch)
        await self.session.flush()

    async def _branch_code_taken(self, hotel_id: UUID, code: str) -> bool:
        return (
            await self.session.execute(
                select(Branch.id).where(
                    Branch.hotel_id == hotel_id, Branch.code == code
                )
            )
        ).first() is not None

    async def _branch(self, branch_id: UUID) -> Branch:
        branch = await self.session.get(Branch, branch_id)
        if branch is None:
            raise NotFoundException("Filial topilmadi", "BRANCH_NOT_FOUND")
        return branch

    @staticmethod
    def _branch_dict(branch: Branch) -> dict:
        return {
            "id": str(branch.id),
            "hotel_id": str(branch.hotel_id),
            "name": branch.name,
            "code": branch.code,
            "city": branch.city,
            "country": branch.country,
            "phone": branch.phone,
            "email": branch.email,
            "is_main_branch": bool(branch.is_main_branch),
        }

    # --------------------------------------------------------- xodim --

    async def list_users(self, hotel_id: UUID) -> list[dict]:
        await self._hotel(hotel_id)
        rows = (
            (
                await self.session.execute(
                    select(User)
                    .where(User.hotel_id == hotel_id, User.is_deleted.is_(False))
                    .order_by(User.user_type, User.first_name)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(u.id),
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "user_type": u.user_type,
                "status": u.status,
                "email": u.email,
                "phone": u.phone,
                "last_login_at": (
                    u.last_login_at.isoformat() if u.last_login_at else None
                ),
            }
            for u in rows
        ]

    async def create_staff(self, hotel_id: UUID, data: dict) -> dict:
        """Mehmonxonaga xodim qo'shish.

        Panel egasiga SHART bo'ladigan amal: yangi mehmonxona ochilganda
        unga birinchi administratorni kiritadigan boshqa yo'l yo'q —
        tizimga kirish uchun hisob kerak, hisob ochish uchun esa kirish
        kerak bo'lardi.
        """
        from app.infrastructure.auth.password import (
            hash_password as hash_staff_password,
        )

        await self._hotel(hotel_id)
        username = (data.get("username") or "").strip().lower()
        password = data.get("password") or ""
        first_name = _clean(data.get("first_name"))
        last_name = _clean(data.get("last_name"))
        user_type = (data.get("user_type") or "EMPLOYEE").upper()

        if len(username) < 3:
            raise ValidationException(
                "Login kamida 3 belgidan iborat bo'lsin", "USERNAME_TOO_SHORT"
            )
        if len(password) < 6:
            raise ValidationException(
                "Parol kamida 6 belgidan iborat bo'lsin", "PASSWORD_TOO_SHORT"
            )
        if not first_name:
            raise ValidationException("Ism kerak", "NAME_REQUIRED")
        if user_type not in ("ADMIN", "EMPLOYEE"):
            raise ValidationException("Noma'lum rol", "INVALID_USER_TYPE")

        taken = (
            await self.session.execute(
                select(User.id).where(User.username == username)
            )
        ).first()
        if taken:
            raise ConflictException("Bu login band", "USERNAME_TAKEN")

        branch_id = data.get("branch_id")
        if not branch_id:
            # Filial ko'rsatilmasa asosiysi olinadi: xodim filialsiz
            # qolsa ba'zi ekranlar unga bo'sh ro'yxat ko'rsatardi
            branch = (
                await self.session.execute(
                    select(Branch)
                    .where(Branch.hotel_id == hotel_id)
                    .order_by(Branch.is_main_branch.desc())
                    .limit(1)
                )
            ).scalars().first()
            branch_id = branch.id if branch else None

        user = User(
            hotel_id=hotel_id,
            branch_id=branch_id,
            username=username,
            password_hash=hash_staff_password(password),
            first_name=first_name,
            last_name=last_name or "",
            email=_clean(data.get("email")),
            phone=_clean(data.get("phone")),
            user_type=user_type,
            status="ACTIVE",
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return {
            "id": str(user.id),
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "user_type": user.user_type,
            "status": user.status,
            "email": user.email,
            "phone": user.phone,
            "last_login_at": None,
        }

    async def set_user_status(self, user_id: UUID, status: str) -> dict:
        """Xodimni faollashtirish yoki to'xtatish."""
        value = (status or "").upper()
        if value not in ("ACTIVE", "INACTIVE", "TERMINATED"):
            raise ValidationException("Noma'lum holat", "INVALID_STATUS")
        user = await self.session.get(User, user_id)
        if user is None or user.is_deleted:
            raise NotFoundException("Xodim topilmadi", "USER_NOT_FOUND")
        user.status = value
        await self.session.flush()
        return {"id": str(user.id), "status": user.status}

    async def reset_user_password(self, user_id: UUID, password: str) -> dict:
        """Mehmonxona xodimining parolini almashtirish.

        Panel egasiga kerak bo'ladigan amal: administrator parolini
        unutsa, uni tiklaydigan boshqa yo'l yo'q.
        """
        # AYNAN kirish tekshiradigan modul: `auth_service` parolni
        # `infrastructure.auth.password.verify_password` bilan solishtiradi.
        # `core.security` dagi passlib varianti hech qayerda ishlatilmaydi —
        # u bilan hashlangan parol bilan xodim tizimga kira olmasdi.
        from app.infrastructure.auth.password import (
            hash_password as hash_staff_password,
        )

        if len((password or "")) < 6:
            raise ValidationException(
                "Parol kamida 6 belgidan iborat bo'lsin", "PASSWORD_TOO_SHORT"
            )
        user = await self.session.get(User, user_id)
        if user is None or user.is_deleted:
            raise NotFoundException("Xodim topilmadi", "USER_NOT_FOUND")
        user.password_hash = hash_staff_password(password)
        await self.session.flush()
        return {"id": str(user.id), "changed": True}
