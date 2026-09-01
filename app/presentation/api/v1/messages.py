"""Xodimlar xabar/so'rovlari — Xabarlar sahifasi (web) va farrosh mobil
ilovasi uchun umumiy taxta.

Har qanday xodim yuboradi (farrosh mobil ilovadan "104-xonani tekshiring"
deb so'rashi mumkin), hamma ko'radi, istalgan xodim "Bajarildi" deb yopadi.
Yangi xabar mehmonxona xodimlariga push (FCM) bilan ham boradi — mobil
ilovadagilar darhol ko'radi.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.application.services.notification_service import NotificationService
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.staff_message import StaffMessage
from app.infrastructure.database.models.user import User
from app.presentation.middleware.auth import get_current_user
from app.presentation.api.v1._deps import require_active_hotel

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID:
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


class MessageCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=500)
    room_id: UUID | None = None


def _msg_dict(m: StaffMessage, users: dict, rooms: dict) -> dict:
    return {
        "id": str(m.id),
        "body": m.body,
        "status": m.status,
        "room_id": str(m.room_id) if m.room_id else None,
        "room_number": rooms.get(m.room_id),
        "created_by": str(m.created_by),
        "created_by_name": users.get(m.created_by),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "done_by_name": users.get(m.done_by) if m.done_by else None,
        "done_at": m.done_at.isoformat() if m.done_at else None,
    }


async def _name_maps(session: AsyncSession, messages: list[StaffMessage]) -> tuple[dict, dict]:
    """Xabarlar ro'yxati uchun yuboruvchi/bajaruvchi ismlari va xona raqamlari."""
    user_ids = {m.created_by for m in messages} | {
        m.done_by for m in messages if m.done_by
    }
    users: dict = {}
    if user_ids:
        rows = (
            await session.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
        users = {u.id: f"{u.first_name} {u.last_name}" for u in rows}
    room_ids = {m.room_id for m in messages if m.room_id}
    rooms: dict = {}
    if room_ids:
        rows = (
            await session.execute(select(Room).where(Room.id.in_(room_ids)))
        ).scalars().all()
        rooms = {r.id: r.room_number for r in rows}
    return users, rooms


#: Taxta standart holatda shuncha kunlik yozuvni ko'rsatadi. Xabarlar
#: qisqa umrli — "104-xonani tekshiring" ertasiga ahamiyatsiz bo'lib qoladi
#: — va bir necha oylik tarix taxtani o'qib bo'lmaydigan qiladi.
DEFAULT_DAYS = 2


@router.get("/")
async def list_messages(
    status: str | None = Query(default=None),
    days: int = Query(
        default=DEFAULT_DAYS,
        ge=0,
        le=365,
        description="Oxirgi shuncha kunlik xabarlar. 0 — hammasi.",
    ),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _get_hotel_id(current_user)
    stmt = (
        select(StaffMessage)
        .where(StaffMessage.hotel_id == h_id)
        .order_by(StaffMessage.created_at.desc())
        .limit(limit)
    )
    if status in ("OPEN", "DONE"):
        stmt = stmt.where(StaffMessage.status == status)
    if days > 0:
        # OCHIQ xabar muddatidan qat'i nazar qoladi. Aks holda uch kun oldin
        # yozilgan va hali bajarilmagan so'rov taxtadan jimgina yo'qolardi —
        # taxtaning butun ma'nosi esa aynan shunday yo'qolmasligida.
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = stmt.where(
            or_(StaffMessage.created_at >= cutoff, StaffMessage.status == "OPEN")
        )
    messages = list((await session.execute(stmt)).scalars().all())
    users, rooms = await _name_maps(session, messages)
    return [_msg_dict(m, users, rooms) for m in messages]


@router.post("/")
async def create_message(
    data: MessageCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _get_hotel_id(current_user)
    room = None
    if data.room_id:
        room = await session.get(Room, data.room_id)
        if not room or room.hotel_id != h_id:
            raise NotFoundException("Room not found", "ROOM_NOT_FOUND")

    msg = StaffMessage(
        hotel_id=h_id,
        room_id=data.room_id,
        body=data.body.strip(),
        status="OPEN",
        created_by=UUID(str(current_user["id"])),
    )
    session.add(msg)
    await session.flush()

    sender = await session.get(User, current_user["id"])
    sender_name = f"{sender.first_name} {sender.last_name}" if sender else "Xodim"

    # Push (FCM) — mobil ilovadagi xodimlarga darhol yetib boradi.
    # Push xatosi xabar yaratilishini to'xtatmasligi kerak.
    try:
        push_body = msg.body + (f" ({room.room_number}-xona)" if room else "")
        await NotificationService(session).send_to_hotel(
            h_id, f"💬 {sender_name}", push_body, "message", msg.id
        )
    except Exception:
        pass

    await session.refresh(msg)
    users = {msg.created_by: sender_name}
    rooms = {room.id: room.room_number} if room else {}
    return _msg_dict(msg, users, rooms)


@router.post("/{message_id}/done")
async def mark_done(
    message_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _get_hotel_id(current_user)
    msg = await session.get(StaffMessage, message_id)
    if not msg or msg.hotel_id != h_id:
        raise NotFoundException("Message not found", "MESSAGE_NOT_FOUND")
    if msg.status != "DONE":
        msg.status = "DONE"
        msg.done_by = UUID(str(current_user["id"]))
        msg.done_at = datetime.now(timezone.utc)
        await session.flush()
    users, rooms = await _name_maps(session, [msg])
    return _msg_dict(msg, users, rooms)
