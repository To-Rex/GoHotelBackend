"""Talab, taklif va shikoyatlar — mehmon murojaatlari kitobi.

Kirish qoidalari va holat o'tishlari shu yerda, bitta joyda (router va
testlar shu funksiyalarni chaqiradi):

  Ko'rish      — ADMIN/SUPER_ADMIN; qabulxona (reservation.create/update/view);
                 menejer (shift.force_close); yoki alohida feedback.* kodi.
  Kiritish     — yuqoridagilar, lekin reservation.view YETARLI EMAS: "faqat
                 ko'rish" roli murojaat yozmaydi.
  Boshqarish   — holat, javob, mas'ul: feedback.manage, reservation.update
                 yoki shift.force_close.
  O'chirish    — ADMIN/SUPER_ADMIN yoki feedback.manage. Kitobdan yozuv
                 o'chirish kamdan-kam va javobgar ish — qabulxona qilmaydi.

Qabulxona kodlari ATAYLAB ro'yxatda: modul deploy bo'lishi bilan mavjud
xodimlar yangi ruxsat berilmasdan ishlashi uchun (do'kon /shop bilan bir xil
yondashuv). Farrosh va texnik xodim ko'rmaydi — murojaatda mehmonning ismi
va telefoni bor.

Holatlar: NEW → IN_PROGRESS → RESOLVED | REJECTED; yopilgani IN_PROGRESS ga
qayta ochiladi. Yopishda javob matni (resolution) SHART — kitobda "nima
qilindi" yozuvsiz yopilgan murojaat qolmasin.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.guest_feedback import GuestFeedback
from app.infrastructure.database.models.permission import Permission, UserPermission
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.user import User
from app.application.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

ADMIN_TYPES = ("ADMIN", "SUPER_ADMIN")

VIEW_CODES = (
    "feedback.view",
    "feedback.create",
    "feedback.manage",
    "reservation.create",
    "reservation.update",
    "reservation.view",
    "shift.force_close",
)
CREATE_CODES = (
    "feedback.create",
    "feedback.manage",
    "reservation.create",
    "reservation.update",
    "shift.force_close",
)
MANAGE_CODES = ("feedback.manage", "reservation.update", "shift.force_close")
DELETE_CODES = ("feedback.manage",)

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "NEW": ("IN_PROGRESS", "RESOLVED", "REJECTED"),
    "IN_PROGRESS": ("RESOLVED", "REJECTED"),
    "RESOLVED": ("IN_PROGRESS",),
    "REJECTED": ("IN_PROGRESS",),
}
CLOSED_STATUSES = ("RESOLVED", "REJECTED")

TYPE_LABELS = {"REQUEST": "Talab", "SUGGESTION": "Taklif", "COMPLAINT": "Shikoyat"}


# ------------------------------------------------------------ sof qoidalar --
def has_access(current_user: dict, codes: Iterable[str]) -> bool:
    if current_user.get("user_type") in ADMIN_TYPES:
        return True
    granted = set(current_user.get("permissions") or [])
    return any(code in granted for code in codes)


def can_view(current_user: dict) -> bool:
    return has_access(current_user, VIEW_CODES)


def can_create(current_user: dict) -> bool:
    return has_access(current_user, CREATE_CODES)


def can_manage(current_user: dict) -> bool:
    return has_access(current_user, MANAGE_CODES)


def can_delete(current_user: dict) -> bool:
    return has_access(current_user, DELETE_CODES)


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, ())


def requires_resolution(new: str) -> bool:
    return new in CLOSED_STATUSES


def local_day_bounds(
    date_from: date | None, date_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """Mahalliy kun chegaralarini UTC ga o'giradi (created_at tz-aware).

    Server UTC da; "bugun" mehmonxona kuni bo'yicha bo'lishi kerak, aks holda
    tungi soatlarda kechagi/ertangi murojaatlar bir-biriga aralashardi.
    """
    offset = timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
    start = (
        datetime.combine(date_from, time.min, tzinfo=timezone.utc) - offset
        if date_from
        else None
    )
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        - offset
        if date_to
        else None
    )
    return start, end


# ---------------------------------------------------------------- servis --
class FeedbackService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ----- bog'lamalarni tekshirish (hamma narsa shu mehmonxonaniki bo'lsin)
    async def _reservation(self, reservation_id: UUID, hotel_id: UUID) -> Reservation:
        r = await self.session.get(Reservation, reservation_id)
        if not r or r.hotel_id != hotel_id or r.is_deleted:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        return r

    async def _room(self, room_id: UUID, hotel_id: UUID) -> Room:
        room = await self.session.get(Room, room_id)
        if not room or room.hotel_id != hotel_id or room.is_deleted:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
        return room

    async def _guest(self, guest_id: UUID, hotel_id: UUID) -> Guest:
        guest = await self.session.get(Guest, guest_id)
        if not guest or guest.hotel_id != hotel_id or guest.is_deleted:
            raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")
        return guest

    async def _staff(self, user_id: UUID, hotel_id: UUID) -> User:
        user = await self.session.get(User, user_id)
        if not user or user.is_deleted or user.hotel_id != hotel_id:
            raise NotFoundException("Employee not found", "EMPLOYEE_NOT_FOUND")
        return user

    @staticmethod
    def _full_name(user: User | Guest | None) -> str | None:
        if user is None:
            return None
        return f"{user.first_name} {user.last_name}".strip() or None

    # ------------------------------------------------------------- yaratish
    async def create(
        self,
        hotel_id: UUID,
        data: dict,
        created_by: UUID,
        default_branch_id: UUID | None = None,
    ) -> GuestFeedback:
        guest_id = data.get("guest_id")
        reservation_id = data.get("reservation_id")
        room_id = data.get("room_id")
        branch_id = data.get("branch_id")
        guest_name = (data.get("guest_name") or "").strip() or None
        guest_phone = (data.get("guest_phone") or "").strip() or None
        room_number = None

        # Bron berilsa mehmon va xona undan to'ldiriladi — qabulxona bitta
        # tanlov bilan hammasini bog'laydi
        if reservation_id:
            reservation = await self._reservation(reservation_id, hotel_id)
            guest_id = guest_id or reservation.guest_id
            room_id = room_id or reservation.room_id
            branch_id = branch_id or reservation.branch_id
        if room_id:
            room = await self._room(room_id, hotel_id)
            room_number = room.room_number
            branch_id = branch_id or room.branch_id
        if guest_id:
            guest = await self._guest(guest_id, hotel_id)
            guest_name = guest_name or self._full_name(guest)
            guest_phone = guest_phone or guest.phone
        assigned_to = data.get("assigned_to")
        if assigned_to:
            await self._staff(assigned_to, hotel_id)

        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
        if not subject or not body:
            raise ValidationException(
                "Mavzu va matn bo'sh bo'lishi mumkin emas", "FEEDBACK_TEXT_REQUIRED"
            )

        fb = GuestFeedback(
            hotel_id=hotel_id,
            branch_id=branch_id or default_branch_id,
            guest_id=guest_id,
            reservation_id=reservation_id,
            room_id=room_id,
            feedback_type=data["feedback_type"],
            status="NEW",
            priority=data.get("priority") or "MEDIUM",
            subject=subject,
            body=body,
            guest_name=guest_name,
            guest_phone=guest_phone,
            room_number=room_number,
            assigned_to=assigned_to,
            created_by=created_by,
        )
        self.session.add(fb)
        await self.session.flush()

        # Xabarlar asosiy oqimni hech qachon buzmaydi (ichida try/except)
        if fb.feedback_type == "COMPLAINT":
            await self._notify_managers(fb, exclude={created_by})
        if assigned_to and assigned_to != created_by:
            await self._notify_assignee(fb)
        return fb

    # --------------------------------------------------------------- o'qish
    async def get(self, feedback_id: UUID, hotel_id: UUID) -> GuestFeedback:
        fb = await self.session.get(GuestFeedback, feedback_id)
        if not fb or fb.hotel_id != hotel_id or fb.is_deleted:
            raise NotFoundException("Feedback not found", "FEEDBACK_NOT_FOUND")
        return fb

    async def list(
        self,
        hotel_id: UUID,
        status: str | None = None,
        feedback_type: str | None = None,
        assigned_to: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        query: str | None = None,
        skip: int = 0,
        limit: int = 500,
    ) -> tuple[list[GuestFeedback], int]:
        stmt = select(GuestFeedback).where(
            GuestFeedback.hotel_id == hotel_id,
            GuestFeedback.is_deleted.is_(False),
        )
        if status:
            stmt = stmt.where(GuestFeedback.status == status)
        if feedback_type:
            stmt = stmt.where(GuestFeedback.feedback_type == feedback_type)
        if assigned_to:
            stmt = stmt.where(GuestFeedback.assigned_to == assigned_to)
        start, end = local_day_bounds(date_from, date_to)
        if start is not None:
            stmt = stmt.where(GuestFeedback.created_at >= start)
        if end is not None:
            stmt = stmt.where(GuestFeedback.created_at < end)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    GuestFeedback.subject.ilike(pattern),
                    GuestFeedback.body.ilike(pattern),
                    GuestFeedback.guest_name.ilike(pattern),
                    GuestFeedback.guest_phone.ilike(pattern),
                    GuestFeedback.room_number.ilike(pattern),
                )
            )

        total = (
            await self.session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        rows = await self.session.execute(
            stmt.order_by(GuestFeedback.created_at.desc()).offset(skip).limit(limit)
        )
        return list(rows.scalars().all()), int(total)

    async def serialize(self, items: list[GuestFeedback]) -> list[dict]:
        """Ismlar bitta so'rovda — ro'yxatda har qator uchun alohida so'rov
        ketmasin."""
        user_ids = {
            uid
            for fb in items
            for uid in (fb.created_by, fb.assigned_to, fb.resolved_by)
            if uid is not None
        }
        names: dict[UUID, str] = {}
        if user_ids:
            rows = await self.session.execute(
                select(User.id, User.first_name, User.last_name).where(
                    User.id.in_(list(user_ids))
                )
            )
            for uid, first, last in rows.all():
                names[uid] = f"{first} {last}".strip()

        reservation_ids = {fb.reservation_id for fb in items if fb.reservation_id}
        numbers: dict[UUID, str] = {}
        if reservation_ids:
            rows = await self.session.execute(
                select(Reservation.id, Reservation.reservation_number).where(
                    Reservation.id.in_(list(reservation_ids))
                )
            )
            numbers = {rid: num for rid, num in rows.all()}

        return [self._to_dict(fb, names, numbers) for fb in items]

    @staticmethod
    def _to_dict(fb: GuestFeedback, names: dict, numbers: dict) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "id": str(fb.id),
            "hotel_id": str(fb.hotel_id),
            "branch_id": str(fb.branch_id) if fb.branch_id else None,
            "guest_id": str(fb.guest_id) if fb.guest_id else None,
            "reservation_id": str(fb.reservation_id) if fb.reservation_id else None,
            "reservation_number": numbers.get(fb.reservation_id),
            "room_id": str(fb.room_id) if fb.room_id else None,
            "room_number": fb.room_number,
            "feedback_type": fb.feedback_type,
            "status": fb.status,
            "priority": fb.priority,
            "subject": fb.subject,
            "body": fb.body,
            "guest_name": fb.guest_name,
            "guest_phone": fb.guest_phone,
            "assigned_to": str(fb.assigned_to) if fb.assigned_to else None,
            "assigned_to_name": names.get(fb.assigned_to),
            "resolution": fb.resolution,
            "resolved_at": iso(fb.resolved_at),
            "resolved_by": str(fb.resolved_by) if fb.resolved_by else None,
            "resolved_by_name": names.get(fb.resolved_by),
            "created_by": str(fb.created_by),
            "created_by_name": names.get(fb.created_by),
            "created_at": iso(fb.created_at),
            "updated_at": iso(fb.updated_at),
        }

    # ----------------------------------------------------------- o'zgartirish
    async def update(
        self, feedback_id: UUID, hotel_id: UUID, data: dict, actor: UUID
    ) -> GuestFeedback:
        """`data` — faqat yuborilgan maydonlar (exclude_unset)."""
        fb = await self.get(feedback_id, hotel_id)
        previous_assignee = fb.assigned_to

        if "reservation_id" in data:
            rid = data["reservation_id"]
            if rid:
                reservation = await self._reservation(rid, hotel_id)
                fb.reservation_id = reservation.id
                if not fb.guest_id and "guest_id" not in data:
                    data["guest_id"] = reservation.guest_id
                if not fb.room_id and "room_id" not in data:
                    data["room_id"] = reservation.room_id
            else:
                fb.reservation_id = None

        if "room_id" in data:
            room_id = data["room_id"]
            if room_id:
                room = await self._room(room_id, hotel_id)
                fb.room_id = room.id
                fb.room_number = room.room_number
            else:
                fb.room_id = None
                fb.room_number = None

        if "guest_id" in data:
            guest_id = data["guest_id"]
            if guest_id:
                guest = await self._guest(guest_id, hotel_id)
                fb.guest_id = guest.id
                if "guest_name" not in data:
                    fb.guest_name = self._full_name(guest)
                if "guest_phone" not in data and guest.phone:
                    fb.guest_phone = guest.phone
            else:
                # Bog'lama olib tashlanadi, yozilgan ism matn bo'lib qoladi
                fb.guest_id = None

        if "assigned_to" in data:
            assignee = data["assigned_to"]
            if assignee:
                await self._staff(assignee, hotel_id)
            fb.assigned_to = assignee

        for field in ("subject", "body"):
            if field in data:
                value = (data[field] or "").strip()
                if not value:
                    raise ValidationException(
                        "Mavzu va matn bo'sh bo'lishi mumkin emas",
                        "FEEDBACK_TEXT_REQUIRED",
                    )
                setattr(fb, field, value)
        for field in ("guest_name", "guest_phone", "resolution"):
            if field in data:
                setattr(fb, field, (data[field] or "").strip() or None)
        for field in ("feedback_type", "priority"):
            if field in data and data[field]:
                setattr(fb, field, data[field])

        await self.session.flush()

        if (
            fb.assigned_to
            and fb.assigned_to != previous_assignee
            and fb.assigned_to != actor
        ):
            await self._notify_assignee(fb)
        return fb

    async def set_status(
        self,
        feedback_id: UUID,
        hotel_id: UUID,
        new_status: str,
        resolution: str | None,
        actor: UUID,
    ) -> GuestFeedback:
        fb = await self.get(feedback_id, hotel_id)
        text = (resolution or "").strip() or None

        if new_status == fb.status:
            # Takroriy bosish xato emas — faqat javob matni yangilanishi mumkin
            if text:
                fb.resolution = text
                await self.session.flush()
            return fb

        if not can_transition(fb.status, new_status):
            raise ValidationException(
                f"Holatni {fb.status} dan {new_status} ga o'tkazib bo'lmaydi",
                "INVALID_STATUS_TRANSITION",
            )
        if requires_resolution(new_status) and not (text or fb.resolution):
            raise ValidationException(
                "Murojaatni yopishda javob (nima qilindi) yozilishi shart",
                "RESOLUTION_REQUIRED",
            )

        if text:
            fb.resolution = text
        if new_status in CLOSED_STATUSES:
            fb.resolved_at = datetime.now(timezone.utc)
            fb.resolved_by = actor
        else:
            # Qayta ochilganda "yopilgan" belgilar tushadi, javob matni
            # tarix sifatida qoladi
            fb.resolved_at = None
            fb.resolved_by = None
            # Jarayonga olgan xodim mas'ul bo'ladi (agar hali yo'q bo'lsa)
            if new_status == "IN_PROGRESS" and fb.assigned_to is None:
                fb.assigned_to = actor
        fb.status = new_status
        await self.session.flush()
        return fb

    async def delete(self, feedback_id: UUID, hotel_id: UUID) -> None:
        fb = await self.get(feedback_id, hotel_id)
        fb.is_deleted = True
        fb.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    # ------------------------------------------------------------- xabarlar
    async def _notify_managers(self, fb: GuestFeedback, exclude: set[UUID]) -> None:
        """Yangi shikoyat — mehmonxona ADMIN'lari va menejerlarga (shift.force_close)."""
        try:
            admins = await self.session.execute(
                select(User.id).where(
                    User.hotel_id == fb.hotel_id,
                    User.user_type == "ADMIN",
                    User.is_deleted.is_(False),
                    User.status == "ACTIVE",
                )
            )
            managers = await self.session.execute(
                select(User.id)
                .join(UserPermission, UserPermission.user_id == User.id)
                .join(Permission, Permission.id == UserPermission.permission_id)
                .where(
                    User.hotel_id == fb.hotel_id,
                    User.user_type == "EMPLOYEE",
                    User.is_deleted.is_(False),
                    User.status == "ACTIVE",
                    Permission.code == "shift.force_close",
                )
            )
            targets = {row[0] for row in admins.all()} | {row[0] for row in managers.all()}
            targets -= exclude
            # Mas'ul alohida "sizga biriktirildi" xabarini oladi
            targets.discard(fb.assigned_to)
            if not targets:
                return
            where = f"{fb.room_number}-xona" if fb.room_number else (fb.guest_name or "Mehmon")
            notifier = NotificationService(self.session)
            for user_id in targets:
                await notifier.notify(
                    hotel_id=fb.hotel_id,
                    user_id=user_id,
                    title="Yangi shikoyat",
                    body=f"{where}: {fb.subject}",
                    entity_type="feedback",
                    entity_id=fb.id,
                    send_push=True,
                )
        except Exception:
            logger.exception("Shikoyat haqida xabar yuborilmadi (feedback=%s)", fb.id)

    async def _notify_assignee(self, fb: GuestFeedback) -> None:
        if not fb.assigned_to:
            return
        try:
            label = TYPE_LABELS.get(fb.feedback_type, "Murojaat")
            where = f" · {fb.room_number}-xona" if fb.room_number else ""
            await NotificationService(self.session).notify(
                hotel_id=fb.hotel_id,
                user_id=fb.assigned_to,
                title="Sizga murojaat biriktirildi",
                body=f"{label}{where}: {fb.subject}",
                entity_type="feedback",
                entity_id=fb.id,
                send_push=True,
            )
        except Exception:
            logger.exception("Mas'ulga xabar yuborilmadi (feedback=%s)", fb.id)
