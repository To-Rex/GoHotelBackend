"""Mehmonning turish tarixi: qachon, qaysi xonada, kim bilan."""
from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.floor import Floor
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_type import RoomType


def _full_name(first: str | None, last: str | None) -> str | None:
    return " ".join(p for p in (first, last) if p).strip() or None


def _safe_uuid(value) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


class GuestHistoryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_history(self, guest_id: UUID, hotel_id: UUID | None) -> dict:
        """Mehmon qatnashgan barcha turishlar.

        Mehmon ASOSIY bo'lgan bronlar ham, HAMROH bo'lganlari ham olinadi.
        Ikkinchisisiz "kim bilan kelgan" savoli chala javob olardi: birga
        kelgan ikki kishidan faqat bittasining tarixi ko'rinardi.

        Hamrohlik JSONB ichida saqlanadi, shuning uchun qidiruv `@>`
        (containment) bilan — bu GIN indeksisiz ham to'g'ri ishlaydi,
        mehmonning bronlari esa ko'p bo'lmaydi.
        """
        stmt = (
            select(
                Reservation,
                Room.room_number,
                RoomType.name.label("room_type_name"),
                Floor.floor_number,
                Branch.name.label("branch_name"),
            )
            .join(Room, Room.id == Reservation.room_id, isouter=True)
            .join(RoomType, RoomType.id == Room.room_type_id, isouter=True)
            .join(Floor, Floor.id == Room.floor_id, isouter=True)
            .join(Branch, Branch.id == Reservation.branch_id, isouter=True)
            .where(
                Reservation.is_deleted.is_(False),
                or_(
                    Reservation.guest_id == guest_id,
                    Reservation.companions.contains([{"guest_id": str(guest_id)}]),
                ),
            )
            .order_by(
                Reservation.check_in_date.desc(),
                Reservation.check_in_datetime.desc().nullslast(),
                Reservation.created_at.desc(),
            )
        )
        if hotel_id is not None:
            stmt = stmt.where(Reservation.hotel_id == hotel_id)

        rows = (await self.session.execute(stmt)).all()

        # Barcha qatnashchilarning kartochkasi — BITTA so'rovda. Har bir
        # turish uchun alohida so'rash tarixni o'nlab so'rovga bo'lardi.
        people_ids: set[UUID] = {guest_id}
        for row in rows:
            res = row[0]
            if res.guest_id:
                people_ids.add(res.guest_id)
            for c in res.companions or []:
                cid = _safe_uuid((c or {}).get("guest_id"))
                if cid:
                    people_ids.add(cid)

        cards: dict[UUID, Guest] = {}
        if people_ids:
            found = (
                (
                    await self.session.execute(
                        select(Guest).where(Guest.id.in_(people_ids))
                    )
                )
                .scalars()
                .all()
            )
            cards = {g.id: g for g in found}

        def person(pid: UUID | None, saved_name: str | None, primary: bool) -> dict:
            card = cards.get(pid) if pid else None
            return {
                "guest_id": pid,
                # Bazadagi ism ustun: mehmon keyin tahrirlangan bo'lishi
                # mumkin. Topilmasa bronda saqlangani qoladi.
                "name": (
                    _full_name(card.first_name, card.last_name) if card else None
                )
                or saved_name,
                "phone": card.phone if card else None,
                "is_primary": primary,
                "is_self": pid == guest_id,
            }

        stays: list[dict] = []
        nights = 0
        paid = 0.0
        completed = 0
        rooms_seen: Counter[str] = Counter()
        dates: list = []

        for row in rows:
            res = row[0]
            people = [person(res.guest_id, None, True)]
            for c in res.companions or []:
                people.append(
                    person(
                        _safe_uuid((c or {}).get("guest_id")),
                        (c or {}).get("name"),
                        False,
                    )
                )

            stays.append(
                {
                    "id": res.id,
                    "reservation_number": res.reservation_number,
                    "role": "MAIN" if res.guest_id == guest_id else "COMPANION",
                    "booking_type": res.booking_type,
                    "check_in_date": res.check_in_date,
                    "check_out_date": res.check_out_date,
                    "check_in_datetime": res.check_in_datetime,
                    "check_out_datetime": res.check_out_datetime,
                    "status": res.status,
                    "room_id": res.room_id,
                    "room_number": row.room_number,
                    "room_type_name": row.room_type_name,
                    "floor_number": row.floor_number,
                    "branch_name": row.branch_name,
                    "adults": res.adults,
                    "children": res.children,
                    "total_amount": float(res.total_amount or 0),
                    "paid_amount": float(res.paid_amount or 0),
                    "payment_status": res.payment_status,
                    "people": people,
                    "created_at": res.created_at,
                }
            )

            # Jamlanmaga bekor qilingan va kelmagan turishlar kirmaydi:
            # ular uchun mehmon xonada bo'lmagan
            if res.status in ("CANCELLED", "NO_SHOW"):
                continue
            completed += 1
            dates.append(res.check_in_date)
            if row.room_number:
                rooms_seen[row.room_number] += 1
            if res.check_out_date and res.check_in_date:
                nights += max((res.check_out_date - res.check_in_date).days, 0)
            # Pul faqat mehmon o'zi ochgan bronlarda hisoblanadi — hamroh
            # bo'lib turgan bronni boshqa odam to'lagan
            if res.guest_id == guest_id:
                paid += float(res.paid_amount or 0)

        return {
            "summary": {
                "total_stays": len(stays),
                "completed_stays": completed,
                "total_nights": nights,
                "total_paid": round(paid, 2),
                "first_stay": min(dates) if dates else None,
                "last_stay": max(dates) if dates else None,
                "favourite_room": rooms_seen.most_common(1)[0][0]
                if rooms_seen
                else None,
            },
            "stays": stays,
        }
