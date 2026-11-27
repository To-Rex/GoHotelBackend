"""E-mehmon integratsiyasi — mehmonni davlat ro'yxatiga olish va chiqarish.

Qanday ishlaydi:

1. Sozlovchi / tizim ma'muri filial sozlamasiga E-mehmon login va maxfiy
   kalitini kiritib, integratsiyani yoqadi. Yoqilmagan filialda bu servis
   hech narsa yozmaydi va hech qayerga so'rov yubormaydi.
2. Mehmon kirgach (bron `CHECKED_IN`), xodim «E-mehmon» oynasida har bir
   mehmon (asosiy mehmon va hamrohlar) uchun shaklni tekshirib yuboradi.
   Ro'yxatga olish DOIM xodim tasdig'i bilan: bu davlat tizimiga yoziladigan
   va mehmonxona depozitidan to'lov yechiladigan amal.
3. Mehmon bizda chiqarilganda (bron yopildi yoki hamroh "ketdi" deb
   belgilandi) E-mehmon ro'yxati fon vazifasi bilan avtomatik yopiladi
   (`run_checkout_sweep`), sozlamada o'chirilgan bo'lsa — xodim o'zi yopadi.

Tarmoq va tranzaksiya: E-mehmon so'rovi bir necha soniya davom etishi
mumkin, shuning uchun tarmoqqa chiqadigan yo'llar filial yozuv qulfini
olmaydi (`/emehmon/gate/` — auth.LOCK_EXEMPT_PREFIXES) va so'rovdan OLDIN
urinish bazaga yoziladi (commit): javob yo'qolsa ham `idempotency_key`
saqlanib qoladi va qayta yuborish mehmonni ikki marta ro'yxatga olmaydi.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import emehmon_crypto
from app.application.services import emehmon_payload as ep
from app.application.services.branch_settings import branch_settings, settings_owner
from app.application.services.companion_ops import companion_guest_id, is_present
from app.application.services.emehmon_client import EmehmonClient
from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.emehmon import EmehmonRegistration
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.invoice import Invoice
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_status_history import RoomStatusHistory
from app.infrastructure.database.models.user import User

logger = logging.getLogger(__name__)

ST_SENDING = "sending"
ST_REGISTERED = "registered"
ST_CHECKED_OUT = "checked_out"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"
#: Ro'yxatga olinmagan (ko'rinishdagi holat — bazada bunday qator yo'q)
ST_NONE = "none"

STAYING = "CHECKED_IN"

#: `sending` shu vaqtdan uzoq tursa — so'rov uzilgan, natija noma'lum
SENDING_STALE = timedelta(minutes=2)
#: `Idempotency-Key` E-mehmon'da 24 soat yashaydi
IDEMPOTENCY_TTL = timedelta(hours=23)
#: Ma'lumotnomalar kam o'zgaradi — xotirada shuncha turadi
DICTIONARY_TTL = 6 * 3600
#: Avtomatik chiqarish: shuncha urinishdan keyin xodimga qoldiriladi
AUTO_CHECKOUT_ATTEMPTS = 6
#: Hamroh "ketdi" deb belgilangach shuncha kutiladi (xato bosilgan bo'lsa
#: «Ketishni bekor qilish» ulgursin)
LEAVE_GRACE = timedelta(minutes=3)

#: Shakldagi shaxsga tegishli maydonlar — mehmon keyingi safar kelganda
#: shulardan to'ldiriladi (turishga tegishlilari — yo'q)
PERSONAL_FIELDS = (
    "surname", "firstname", "lastname", "citizen_id", "passport_number",
    "passport_type_id", "passport_date", "passport_issued_by", "birth_date",
    "sex", "pinfl", "birth_country_id",
)

_dictionary_cache: dict[tuple, tuple[float, list]] = {}
#: Hozir yuborilayotgan mehmonlar (bitta jarayon — Procfile): ikki xodim bir
#: mehmonni bir vaqtda yubora olmaydi
_busy: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _utc(moment: datetime | None) -> datetime | None:
    """Bazaga yoziladigan vaqt — doim UTC."""
    return moment.astimezone(timezone.utc) if moment is not None else None


def _iso(moment: datetime | None) -> str | None:
    moment = _aware(moment)
    return moment.isoformat() if moment else None


def _uuid(value) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _db_moment(value) -> datetime | None:
    """Bazadan kelgan vaqt (SQLite matn qaytaradi) → UTC bilan."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return None
    return _aware(value)


def _stale(row: EmehmonRegistration, now: datetime) -> bool:
    if row.status != ST_SENDING:
        return False
    updated = _aware(row.updated_at) or _aware(row.created_at)
    return updated is None or now - updated > SENDING_STALE


def serialize(row: EmehmonRegistration, now: datetime | None = None) -> dict:
    now = now or _now()
    status, error_code, error = row.status, row.error_code, row.error
    if _stale(row, now):
        # So'rov uzilib qolgan: yozilgan-yozilmagani noma'lum
        status = ST_FAILED
        error_code = error_code or ep.NETWORK_ERROR
        error = error or "Natija noma'lum — qayta yuboring (mehmon ikki marta yozilmaydi)"
    response = row.response if isinstance(row.response, dict) else {}
    return {
        "id": str(row.id),
        "reservation_id": str(row.reservation_id) if row.reservation_id else None,
        "guest_id": str(row.guest_id) if row.guest_id else None,
        "role": row.role,
        "full_name": row.full_name,
        "passport_number": row.passport_number,
        "environment": row.environment,
        "status": status,
        "listok_id": row.listok_id,
        "reg_num": row.reg_num,
        "uncertain": bool(row.uncertain),
        "attempts": row.attempts or 0,
        "error_code": error_code,
        "error": error,
        "error_meta": row.error_meta if isinstance(row.error_meta, dict) else {},
        "arrived_at": _iso(row.arrived_at),
        "registered_at": _iso(row.registered_at),
        "checked_out_at": _iso(row.checked_out_at),
        "checkout_sum": float(row.checkout_sum) if row.checkout_sum is not None else None,
        "checkout_payment_type": row.checkout_payment_type,
        "checkout_auto": row.status == ST_CHECKED_OUT and row.checkout_by is None,
        "checkout_attempts": row.checkout_attempts or 0,
        "checkout_error_code": row.checkout_error_code,
        "checkout_error": row.checkout_error,
        "date_visit_off": row.date_visit_off,
        "note": row.note,
        "foreign_duplicates": response.get("foreign_duplicates") or [],
        "created_at": _iso(row.created_at),
    }


def secret_of(cfg: dict) -> str | None:
    """Filialning E-mehmon maxfiy kaliti (ochilgan). Yo'q yoki ochilmasa — `None`."""
    return emehmon_crypto.decrypt(cfg.get("secret_enc"))


def person_state(row: EmehmonRegistration | None, person: dict, now: datetime) -> str:
    """Mehmonning E-mehmon bo'yicha holati (ko'rsatish va navbat uchun)."""
    if row is None:
        return ST_NONE
    if row.status == ST_CHECKED_OUT:
        returned = person.get("returned_at")
        left = _aware(row.checked_out_at)
        if person.get("present") and returned and left and returned > left:
            # Ketib, yana qaytib kelgan hamroh — yangidan ro'yxatga olinadi
            return ST_NONE
        return ST_CHECKED_OUT
    if _stale(row, now):
        return ST_FAILED
    return row.status


class EmehmonService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- sozlamalar --

    async def config(self, hotel_id: UUID | None, branch_id: UUID | None = None) -> dict:
        raw = (await branch_settings(self.session, hotel_id, branch_id)).get(ep.SETTINGS_KEY)
        return ep.resolve(raw)

    async def save_settings(self, hotel_id: UUID, data: dict) -> dict:
        owner = await settings_owner(self.session, hotel_id)
        if owner is None:
            raise NotFoundException("Hotel not found")
        stored = dict((owner.settings or {}).get(ep.SETTINGS_KEY) or {})
        was = ep.resolve(stored)
        data = dict(data)
        new_secret = "".join(str(data.pop("secret", None) or "").split())
        clear_secret = bool(data.pop("clear_secret", False))
        allowed = (
            "enabled", "environment", "username", "default_visit_type_id",
            "default_guest_type_id", "card_payment_type_id", "auto_checkout",
            "prompt_on_checkin",
        )
        raw = {**stored, **{k: data[k] for k in allowed if k in data}}
        # Maxfiy kalit bazada faqat SHIFRLANGAN holda turadi. Yangi qiymat
        # kelsa — almashtiriladi; kelmasa — saqlangani qoladi.
        if new_secret:
            if len(new_secret) < 16:
                raise ValidationException(
                    "Maxfiy kalit juda qisqa — E-mehmon bergan kalitni to'liq kiriting",
                    "EMEHMON_SECRET_INVALID",
                )
            raw["secret_enc"] = emehmon_crypto.encrypt(new_secret)
            raw["secret_hint"] = emehmon_crypto.hint(new_secret)
        elif clear_secret:
            raw["secret_enc"], raw["secret_hint"] = "", ""
        merged = ep.resolve(raw)
        if merged["enabled"]:
            problems = ep.validate(merged)
            if problems:
                raise ValidationException(problems[0], "EMEHMON_SETTINGS_INVALID")
            # Faqat yoqilgandan KEYIN kirgan mehmonlar navbatga tushadi
            merged["enabled_at"] = (
                was["enabled_at"] if was["enabled"] and was["enabled_at"] else _now().isoformat()
            )
        else:
            merged["enabled_at"] = None
        # JSONB YANGI dict bilan almashtiriladi — SQLAlchemy o'zgarishni sezishi uchun
        new_settings = dict(owner.settings or {})
        new_settings[ep.SETTINGS_KEY] = {
            k: v for k, v in merged.items() if k != "secret_set"
        }
        owner.settings = new_settings
        await self.session.flush()
        return merged

    def _client(self, cfg: dict) -> EmehmonClient:
        """Imzoli mijoz. Login yoki kalit kiritilmagan bo'lsa — tushunarli xato."""
        secret = secret_of(cfg)
        if not cfg.get("username") or not secret:
            raise ValidationException(
                "E-mehmon login va maxfiy kaliti kiritilmagan — Sozlamalar → «E-mehmon»",
                "EMEHMON_NOT_CONFIGURED",
            )
        return EmehmonClient(ep.base_url(cfg), cfg["username"], secret)

    async def _ready(self, hotel_id: UUID, branch_id: UUID | None = None) -> tuple[dict, EmehmonClient]:
        """Ishlash uchun: integratsiya YOQILGAN va sozlangan bo'lishi kerak."""
        cfg = await self.config(hotel_id, branch_id)
        if not cfg["enabled"]:
            raise ValidationException(
                "E-mehmon integratsiyasi bu filialda yoqilmagan", "EMEHMON_DISABLED"
            )
        return cfg, self._client(cfg)

    # ------------------------------------------- E-mehmon'dan o'qish --

    async def status(self, hotel_id: UUID) -> dict:
        """Ulanishni tekshirish: mehmonxona kartasi va ro'yxatga olish mumkinligi.
        Yoqishdan OLDIN ham ishlaydi (login va kalit kiritilgan bo'lsa)."""
        cfg = await self.config(hotel_id)
        client = self._client(cfg)
        try:
            hotel = await client.request("GET", "/hotel")
            can = await client.request("GET", "/listok/check-can-create")
        except ep.EmehmonError as exc:
            return {"ok": False, "environment": cfg["environment"], **exc.as_dict()}
        card, gate = hotel.data, can.data
        license_info = card.get("license") if isinstance(card.get("license"), dict) else {}
        return {
            "ok": True,
            "environment": cfg["environment"],
            "sandbox": hotel.sandbox,
            "hotel": {
                "hotel_id": card.get("hotel_id"),
                "name": card.get("name"),
                "inn": card.get("inn"),
                "address": card.get("address"),
                "license_number": license_info.get("number"),
            },
            "can_create": bool(gate.get("can_create", card.get("can_register_listok"))),
            "blocking_reasons": gate.get("blocking_reasons") or card.get("blocking_reasons") or [],
            "deposit": gate.get("deposit", card.get("deposit")),
            "deposit_alert": gate.get("deposit_alert"),
            "license_expires_at": gate.get("license_expires_at") or license_info.get("expires_at"),
        }

    async def dictionary(
        self,
        hotel_id: UUID,
        name: str,
        lang: str = "uz",
        region_id: int | None = None,
        refresh: bool = False,
    ) -> list[dict]:
        """E-mehmon ma'lumotnomasi (davlatlar, tashrif maqsadlari...) — keshlangan."""
        if name not in ep.DICTIONARIES:
            raise NotFoundException("Bunday ma'lumotnoma yo'q", "EMEHMON_DICTIONARY_UNKNOWN")
        cfg = await self.config(hotel_id)
        client = self._client(cfg)
        lang = lang if lang in ep.LANGUAGES else "uz"
        key = (client.base_url, client.username, name, lang, region_id)
        cached = _dictionary_cache.get(key)
        if cached and not refresh and cached[0] > time.monotonic():
            return cached[1]
        params = {"region_id": region_id} if name == "districts" and region_id else None
        try:
            reply = await client.request("GET", f"/dictionaries/{name}", params=params, lang=lang)
        except ep.EmehmonError as exc:
            if cached:
                # Vaqtincha uzilish: eski ro'yxat bilan ishlayveramiz
                return cached[1]
            raise ValidationException(exc.text, f"EMEHMON_{exc.error_code}") from None
        items = reply.data.get("items")
        items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
        _dictionary_cache[key] = (time.monotonic() + DICTIONARY_TTL, items)
        return items

    async def rooms(self, hotel_id: UUID) -> list[dict]:
        """E-mehmon'dagi xonalar ro'yxati (`propiska` uchun)."""
        cfg = await self.config(hotel_id)
        client = self._client(cfg)
        key = (client.base_url, client.username, "__rooms__", "", None)
        cached = _dictionary_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            reply = await client.request("GET", "/hotel/rooms")
        except ep.EmehmonError:
            return cached[1] if cached else []
        items = reply.data.get("items")
        items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
        _dictionary_cache[key] = (time.monotonic() + 600, items)
        return items

    async def lookup(self, hotel_id: UUID, passport, birth_date, citizen_id) -> dict:
        """«Bazadan tekshirish»: mahalliy fuqaro — IIV, chet ellik — Chegara xizmati."""
        _cfg, client = await self._ready(hotel_id)
        body = {
            "passportNumber": ep.normalize_passport(passport),
            "datebirth": ep.iso_date(birth_date),
            "id_citizen": citizen_id,
        }
        if not body["passportNumber"] or not body["datebirth"] or not body["id_citizen"]:
            raise ValidationException(
                "Tekshirish uchun pasport raqami, tug'ilgan sana va fuqarolik kerak",
                "EMEHMON_LOOKUP_INCOMPLETE",
            )
        try:
            reply = await client.request("POST", "/listok/person-lookup", body=body)
        except ep.EmehmonError as exc:
            return {"found": False, **exc.as_dict()}
        return {
            "found": True,
            "person": ep.person_from_lookup(reply.data),
            "source": reply.data.get("source"),
            "birth_place": reply.data.get("birthPlace"),
        }

    # ------------------------------------------------------- bron varag'i --

    async def _reservation(self, hotel_id: UUID, reservation_id: UUID) -> Reservation:
        reservation = (
            await self.session.execute(
                select(Reservation).where(
                    Reservation.id == reservation_id, Reservation.hotel_id == hotel_id
                )
            )
        ).scalars().first()
        if reservation is None:
            raise NotFoundException("Reservation not found", "RESERVATION_NOT_FOUND")
        return reservation

    async def _arrivals(self, hotel_id: UUID, reservations: list[Reservation]) -> dict[UUID, datetime]:
        """Bronlarning HAQIQIY kirish vaqti.

        Kirish rasmiylashtirilganda xona tarixiga "Check-in for reservation
        <raqam>" yozuvi tushadi (ReservationService.check_in) — vaqt o'shandan
        olinadi. Topilmasa — bronning rejadagi kirish vaqti (o'tgan bo'lsa).
        """
        if not reservations:
            return {}
        notes = {f"Check-in for reservation {r.reservation_number}": r.id for r in reservations}
        rows = (
            await self.session.execute(
                select(RoomStatusHistory.notes, func.max(RoomStatusHistory.created_at))
                .where(
                    RoomStatusHistory.hotel_id == hotel_id,
                    RoomStatusHistory.status == "OCCUPIED",
                    RoomStatusHistory.notes.in_(list(notes)),
                )
                .group_by(RoomStatusHistory.notes)
            )
        ).all()
        out: dict[UUID, datetime] = {}
        for note, moment in rows:
            moment = _db_moment(moment)
            if moment is not None:
                out[notes[note]] = moment
        now = _now()
        for r in reservations:
            planned = _aware(r.check_in_datetime)
            if r.id not in out and planned is not None and planned <= now:
                out[r.id] = planned
        return out

    def _persons(self, reservation: Reservation, arrival: datetime | None) -> list[dict]:
        """Bronning mehmonlari: asosiy mehmon va (mehmon yozuvi bor) hamrohlar."""
        staying = reservation.status == STAYING
        persons = [{
            "guest_id": reservation.guest_id,
            "role": "main",
            "name": None,
            "present": staying,
            "since": arrival,
            "returned_at": None,
            "left_at": None,
        }]
        seen = {str(reservation.guest_id)}
        for entry in reservation.companions or []:
            guest_id = _uuid(companion_guest_id(entry))
            if guest_id is None or str(guest_id) in seen:
                continue
            seen.add(str(guest_id))
            returned = ep.parse_moment(entry.get("returned_at"))
            joined = returned or ep.parse_moment(entry.get("added_at"))
            # Bron bilan birga yozilgan hamroh asosiy mehmon bilan birga kirgan
            since = max(joined, arrival) if joined and arrival else (joined or arrival)
            persons.append({
                "guest_id": guest_id,
                "role": "companion",
                "name": entry.get("name"),
                "present": staying and is_present(entry),
                "since": since,
                "returned_at": returned,
                "left_at": ep.parse_moment(entry.get("left_at")),
            })
        return persons

    async def _latest_rows(self, reservation_ids: list[UUID]) -> dict[tuple[UUID, UUID], EmehmonRegistration]:
        """Har (bron, mehmon) uchun ENG OXIRGI ro'yxat qatori."""
        if not reservation_ids:
            return {}
        rows = (
            await self.session.execute(
                select(EmehmonRegistration)
                .where(EmehmonRegistration.reservation_id.in_(reservation_ids))
                .order_by(EmehmonRegistration.created_at, EmehmonRegistration.id)
            )
        ).scalars().all()
        return {(r.reservation_id, r.guest_id): r for r in rows}

    async def _guests(self, guest_ids) -> dict[UUID, Guest]:
        ids = [g for g in {*guest_ids} if g]
        if not ids:
            return {}
        rows = (await self.session.execute(select(Guest).where(Guest.id.in_(ids)))).scalars().all()
        return {g.id: g for g in rows}

    async def _previous_forms(self, hotel_id: UUID, guest_ids) -> dict[UUID, dict]:
        """Mehmonning shu mehmonxonadagi avvalgi ro'yxatidan shaxsiy maydonlar."""
        ids = [g for g in {*guest_ids} if g]
        if not ids:
            return {}
        rows = (
            await self.session.execute(
                select(EmehmonRegistration.guest_id, EmehmonRegistration.form)
                .where(
                    EmehmonRegistration.hotel_id == hotel_id,
                    EmehmonRegistration.guest_id.in_(ids),
                    EmehmonRegistration.status.in_((ST_REGISTERED, ST_CHECKED_OUT)),
                )
                .order_by(EmehmonRegistration.created_at)
            )
        ).all()
        return {gid: form for gid, form in rows if isinstance(form, dict)}

    def _prefill(
        self,
        cfg: dict,
        reservation: Reservation,
        room: Room | None,
        person: dict,
        guest: Guest | None,
        row: EmehmonRegistration | None,
        previous: dict | None,
        now: datetime,
    ) -> dict:
        """Shaklning dastlabki qiymatlari.

        Tartib: shu turishdagi yuborilmagan urinish (xodim kiritgani
        yo'qolmasin) → mehmonning avvalgi ro'yxati → mehmon kartasi.
        """
        form = ep.clean_form({})
        if guest is not None:
            form.update({
                "surname": ep.latin_name(guest.last_name),
                "firstname": ep.latin_name(guest.first_name),
                "passport_number": ep.normalize_passport(
                    guest.passport_number or guest.id_document_number
                ),
                "birth_date": ep.iso_date(guest.birth_date),
                "citizen_id": ep.citizen_id_for(guest.nationality, None),
            })
        if previous:
            form.update({k: previous[k] for k in PERSONAL_FIELDS if previous.get(k)})
        form["visit_type_id"] = cfg["default_visit_type_id"]
        form["guest_type_id"] = cfg["default_guest_type_id"]
        since = person.get("since")
        earliest, latest = ep.arrival_window(now)
        start = now
        if since is not None and earliest <= since <= latest + timedelta(minutes=1):
            # Haqiqiy kirish vaqti. 12 soatdan eski bo'lsa taklif QILINMAYDI:
            # E-mehmon uni qabul qilmaydi, soxta vaqtni esa xodim o'zi tanlasin
            start = min(since, latest)
            form["arrival"] = ep.tashkent_iso(start)
        form["days"] = ep.stay_days(start.astimezone(ep.TASHKENT).date(), reservation.check_out_date)
        form["room"] = room.room_number if room is not None else ""
        if person["role"] == "main":
            form["amount"] = int(round(float(reservation.total_amount or 0))) or None
            form["paid"] = reservation.payment_status == "PAID"
        if row is not None and row.status in (ST_FAILED, ST_SENDING) and isinstance(row.form, dict):
            kept = {k: v for k, v in row.form.items() if k in form and v not in (None, "", [])}
            # Eskirgan kirish vaqti qayta taklif qilinmaydi
            if ep.arrival_problem(ep.parse_moment(kept.get("arrival")), now):
                kept.pop("arrival", None)
            form.update(kept)
        return form

    async def _checkout_defaults(
        self, cfg: dict, reservation: Reservation | None, rows: list[EmehmonRegistration]
    ) -> dict:
        """Chiqarishda yuboriladigan summa va to'lov turi (taklif).

        Summa — bron summasidan shu mehmonning ulushi: hali chiqarilmagan
        ro'yxatlar o'rtasida teng bo'linadi. To'lov turi — bronga eng ko'p
        pul tushgan usul.
        """
        if reservation is None:
            return {"sum": 0, "payment_type_id": ep.PAYMENT_OTHER}
        reported = sum(float(r.checkout_sum or 0) for r in rows if r.status == ST_CHECKED_OUT)
        remaining = sum(1 for r in rows if r.status == ST_REGISTERED)
        amounts = {
            method: float(amount or 0)
            for method, amount in (
                await self.session.execute(
                    select(Payment.payment_method, func.sum(Payment.amount))
                    .join(Invoice, Invoice.id == Payment.invoice_id)
                    .where(Invoice.reservation_id == reservation.id)
                    .group_by(Payment.payment_method)
                )
            ).all()
        }
        return {
            "sum": ep.checkout_share(float(reservation.total_amount or 0), reported, remaining),
            "payment_type_id": ep.payment_type_for(ep.dominant_method(amounts), cfg),
        }

    async def sheet(self, hotel_id: UUID, reservation_id: UUID) -> dict:
        """Bronning E-mehmon varag'i: mehmonlar, holatlari va to'ldirilgan shakllar."""
        cfg = await self.config(hotel_id)
        reservation = await self._reservation(hotel_id, reservation_id)
        now = _now()
        arrival = (await self._arrivals(hotel_id, [reservation])).get(reservation.id)
        persons = self._persons(reservation, arrival)
        latest = await self._latest_rows([reservation.id])
        guests = await self._guests(p["guest_id"] for p in persons)
        previous = await self._previous_forms(hotel_id, (p["guest_id"] for p in persons))
        room = await self.session.get(Room, reservation.room_id)
        defaults = await self._checkout_defaults(cfg, reservation, list(latest.values()))
        earliest, latest_allowed = ep.arrival_window(now)
        out = []
        for person in persons:
            guest = guests.get(person["guest_id"])
            row = latest.get((reservation.id, person["guest_id"]))
            state = person_state(row, person, now)
            current = row if state != ST_NONE else None
            name = (
                " ".join(filter(None, [guest.last_name, guest.first_name])) if guest else None
            ) or person.get("name") or "—"
            since = person.get("since")
            item = {
                "guest_id": str(person["guest_id"]),
                "role": person["role"],
                "name": name,
                "present": person["present"],
                "state": state,
                "since": _iso(since),
                # Kirganiga 12 soatdan oshgan: E-mehmon haqiqiy vaqtni qabul qilmaydi
                "overdue": bool(since and since < earliest),
                "citizen_code": ep.country_code(guest.nationality) if guest else None,
                "registration": serialize(current, now) if current is not None else None,
                "form": None,
                "checkout": defaults if state == ST_REGISTERED else None,
            }
            if state in (ST_NONE, ST_FAILED, ST_SKIPPED) and person["present"]:
                item["form"] = self._prefill(
                    cfg, reservation, room, person, guest, current,
                    previous.get(person["guest_id"]), now,
                )
            out.append(item)
        return {
            "enabled": cfg["enabled"],
            "environment": cfg["environment"],
            "reservation": {
                "id": str(reservation.id),
                "number": reservation.reservation_number,
                "status": reservation.status,
                "room_number": room.room_number if room is not None else None,
                "check_in_date": reservation.check_in_date.isoformat(),
                "check_out_date": reservation.check_out_date.isoformat(),
                "arrived_at": _iso(arrival),
                "total_amount": float(reservation.total_amount or 0),
            },
            "window": {"min": ep.tashkent_iso(earliest), "max": ep.tashkent_iso(latest_allowed)},
            "persons": out,
        }

    # --------------------------------------------------- ro'yxatga olish --

    async def _person(self, reservation: Reservation, guest_id: UUID, arrival: datetime | None) -> dict:
        for person in self._persons(reservation, arrival):
            if str(person["guest_id"]) == str(guest_id):
                return person
        raise NotFoundException("Bu mehmon bronda yo'q", "EMEHMON_PERSON_NOT_FOUND")

    async def _actor(self, user_id) -> tuple[str | None, str | None]:
        uid = _uuid(user_id)
        if uid is None:
            return None, None
        user = await self.session.get(User, uid)
        name = " ".join(filter(None, [user.first_name, user.last_name])) if user else None
        return str(uid), name

    async def _target_row(
        self, hotel_id: UUID, reservation: Reservation, person: dict, now: datetime
    ) -> EmehmonRegistration | None:
        """Shu mehmonning joriy qatori (bo'lsa). Ro'yxatga olingan bo'lsa — xato."""
        latest = await self._latest_rows([reservation.id])
        row = latest.get((reservation.id, person["guest_id"]))
        state = person_state(row, person, now)
        if state == ST_REGISTERED:
            raise ConflictException(
                "Bu mehmon E-mehmon'da allaqachon ro'yxatga olingan", "EMEHMON_ALREADY_REGISTERED"
            )
        if state == ST_CHECKED_OUT:
            raise ConflictException(
                "Bu mehmon E-mehmon'dan allaqachon chiqarilgan", "EMEHMON_ALREADY_CHECKED_OUT"
            )
        if state == ST_SENDING:
            raise ConflictException(
                "Bu mehmon hozir ro'yxatga olinmoqda — biroz kuting", "EMEHMON_BUSY"
            )
        return row if state != ST_NONE else None

    async def register(
        self, hotel_id: UUID, reservation_id: UUID, guest_id: UUID, form: dict, user_id
    ) -> dict:
        """Mehmonni E-mehmon'da ro'yxatga oladi (`POST /listok`).

        Natija DOIM qator bo'lib qaytadi: E-mehmon rad etsa ham urinish
        (xato kodi va matni bilan) saqlanadi — istisno faqat so'rov umuman
        yuborilmaydigan holatlarda (shakl to'liq emas, mehmon xonada emas).
        """
        cfg, client = await self._ready(hotel_id)
        reservation = await self._reservation(hotel_id, reservation_id)
        if reservation.status != STAYING:
            raise ValidationException(
                "Ro'yxatga olish uchun mehmon kirgan (joylashgan) bo'lishi kerak",
                "EMEHMON_NOT_CHECKED_IN",
            )
        now = _now()
        arrival = (await self._arrivals(hotel_id, [reservation])).get(reservation.id)
        person = await self._person(reservation, guest_id, arrival)
        if not person["present"]:
            raise ValidationException("Bu mehmon xonadan ketgan", "EMEHMON_PERSON_LEFT")
        row = await self._target_row(hotel_id, reservation, person, now)

        clean = ep.clean_form(form)
        problems = ep.form_problems(clean, now)
        if problems:
            raise ValidationException(" · ".join(problems), "EMEHMON_FORM_INVALID")

        busy_key = f"{reservation.id}:{guest_id}"
        if busy_key in _busy:
            raise ConflictException(
                "Bu mehmon hozir ro'yxatga olinmoqda — biroz kuting", "EMEHMON_BUSY"
            )
        _busy.add(busy_key)
        try:
            actor_id, actor_name = await self._actor(user_id)
            # Natijasi noma'lum urinish AYNAN o'sha shakl bilan takrorlansa —
            # o'sha tana va o'sha kalit: E-mehmon saqlagan javobini qaytaradi
            previous = row.form if row is not None and isinstance(row.form, dict) else {}
            replay = (
                row is not None
                and bool(row.uncertain)
                and bool(row.idempotency_key)
                and {k: v for k, v in previous.items() if k != "_actor"} == clean
                and now - (_aware(row.updated_at) or now) < IDEMPOTENCY_TTL
            )
            if replay:
                stored_actor = previous.get("_actor") or {}
                actor_id, actor_name = stored_actor.get("id"), stored_actor.get("name")
            body = ep.build_listok(clean, actor_id=actor_id, actor_name=actor_name)
            key = row.idempotency_key if replay else str(uuid.uuid4())

            guest = (await self._guests([guest_id])).get(guest_id)
            if row is None:
                row = EmehmonRegistration(
                    hotel_id=hotel_id,
                    branch_id=reservation.branch_id,
                    reservation_id=reservation.id,
                    guest_id=guest_id,
                    role=person["role"],
                )
                self.session.add(row)
            row.full_name = (
                " ".join(filter(None, [clean["surname"], clean["firstname"]]))
                or (f"{guest.last_name} {guest.first_name}" if guest else None)
            )
            row.passport_number = clean["passport_number"]
            row.environment = cfg["environment"]
            row.status = ST_SENDING
            row.form = {**clean, "_actor": {"id": actor_id, "name": actor_name}}
            row.body_hash = ep.body_hash(body)
            row.idempotency_key = key
            row.uncertain = True
            row.attempts = (row.attempts or 0) + 1
            row.error_code = row.error = None
            row.error_meta = None
            row.note = None
            row.arrived_at = _utc(ep.parse_moment(clean["arrival"]))
            # Urinish tarmoqqa chiqishdan OLDIN saqlanadi: javob yo'qolsa ham
            # kalit bazada qoladi
            await self.session.commit()

            try:
                reply = await client.request("POST", "/listok", body=body, idempotency_key=key)
            except ep.EmehmonError as exc:
                row.status = ST_FAILED
                row.uncertain = exc.uncertain
                row.error_code = exc.error_code
                row.error = exc.text
                row.error_meta = exc.meta or None
                await self.session.flush()
                return serialize(row)
            data = reply.data
            row.status = ST_REGISTERED
            row.uncertain = False
            row.listok_id = ep._int(data.get("listok_id"))
            row.reg_num = ep._text(data.get("regNum"), 40) or None
            row.response = data
            row.registered_at = _now()
            row.registered_by = _uuid(user_id)
            resolved = ep.person_from_lookup(data.get("resolved_person"))
            if resolved:
                # E-mehmon IIV bazasidan topgan ma'lumot (masalan PINFL) —
                # mehmon keyingi safar kelganda shakl tayyor bo'ladi
                row.form = {**row.form, **{k: v for k, v in resolved.items() if k in PERSONAL_FIELDS}}
            await self.session.flush()
            return serialize(row)
        finally:
            _busy.discard(busy_key)

    async def link(
        self, hotel_id: UUID, reservation_id: UUID, guest_id: UUID, listok_id: int, user_id
    ) -> dict:
        """E-mehmon'da ALLAQACHON bor ro'yxatni (kabinetda qo'lda kiritilgan
        yoki javobi yo'qolgan) shu mehmonga bog'laydi — chiqarish shu yerdan
        yurishi uchun. Pasport raqami mos kelishi shart."""
        cfg, client = await self._ready(hotel_id)
        reservation = await self._reservation(hotel_id, reservation_id)
        now = _now()
        arrival = (await self._arrivals(hotel_id, [reservation])).get(reservation.id)
        person = await self._person(reservation, guest_id, arrival)
        row = await self._target_row(hotel_id, reservation, person, now)
        try:
            reply = await client.request("GET", f"/listok/{int(listok_id)}")
        except ep.EmehmonError as exc:
            raise ValidationException(exc.text, f"EMEHMON_{exc.error_code}") from None
        data = reply.data
        guest = (await self._guests([guest_id])).get(guest_id)
        known = {
            ep.normalize_passport(value)
            for value in (
                (row.form or {}).get("passport_number") if row is not None else None,
                row.passport_number if row is not None else None,
                guest.passport_number if guest else None,
                guest.id_document_number if guest else None,
            )
            if value
        }
        remote = ep.normalize_passport(data.get("passportNumber"))
        if not remote or remote not in known:
            raise ValidationException(
                "Bu ro'yxat boshqa pasportga tegishli — bog'lab bo'lmaydi", "EMEHMON_LINK_MISMATCH"
            )
        if data.get("datevisitoff"):
            raise ValidationException(
                "Bu ro'yxat E-mehmon'da allaqachon yopilgan", "EMEHMON_LINK_CLOSED"
            )
        if row is None:
            row = EmehmonRegistration(
                hotel_id=hotel_id,
                branch_id=reservation.branch_id,
                reservation_id=reservation.id,
                guest_id=guest_id,
                role=person["role"],
            )
            self.session.add(row)
        row.full_name = " ".join(
            filter(None, [ep._text(data.get("surname"), 60), ep._text(data.get("firstname"), 60)])
        ) or row.full_name
        row.passport_number = remote
        row.environment = cfg["environment"]
        row.status = ST_REGISTERED
        row.uncertain = False
        row.listok_id = ep._int(data.get("listok_id")) or int(listok_id)
        row.reg_num = ep._text(data.get("regNum"), 40) or None
        row.response = data
        row.error_code = row.error = None
        row.error_meta = None
        # E-mehmon vaqtni zonasiz, Toshkent vaqti bilan qaytaradi
        row.arrived_at = _utc(ep.parse_moment(str(data.get("datevisiton") or "").replace(" ", "T")))
        row.registered_at = _now()
        row.registered_by = _uuid(user_id)
        row.note = "E-mehmon'dagi mavjud ro'yxat bog'landi"
        await self.session.flush()
        return serialize(row)

    async def skip(
        self, hotel_id: UUID, reservation_id: UUID, guest_id: UUID, note: str | None, user_id
    ) -> dict:
        """«Kerak emas»: mehmon navbatdan chiqadi (E-mehmon'ga hech narsa ketmaydi)."""
        cfg = await self.config(hotel_id)
        if not cfg["enabled"]:
            raise ValidationException(
                "E-mehmon integratsiyasi bu filialda yoqilmagan", "EMEHMON_DISABLED"
            )
        reservation = await self._reservation(hotel_id, reservation_id)
        now = _now()
        arrival = (await self._arrivals(hotel_id, [reservation])).get(reservation.id)
        person = await self._person(reservation, guest_id, arrival)
        row = await self._target_row(hotel_id, reservation, person, now)
        if row is None:
            guest = (await self._guests([guest_id])).get(guest_id)
            row = EmehmonRegistration(
                hotel_id=hotel_id,
                branch_id=reservation.branch_id,
                reservation_id=reservation.id,
                guest_id=guest_id,
                role=person["role"],
                full_name=(f"{guest.last_name} {guest.first_name}" if guest else person.get("name")),
                environment=cfg["environment"],
            )
            self.session.add(row)
        row.status = ST_SKIPPED
        row.uncertain = False
        row.note = ep._text(note, 300) or None
        row.registered_by = _uuid(user_id)
        await self.session.flush()
        return serialize(row)

    async def unskip(self, hotel_id: UUID, reservation_id: UUID, guest_id: UUID) -> None:
        """«Kerak emas» belgisini olib tashlaydi — mehmon yana navbatga qaytadi."""
        reservation = await self._reservation(hotel_id, reservation_id)
        row = (await self._latest_rows([reservation.id])).get((reservation.id, guest_id))
        if row is not None and row.status == ST_SKIPPED:
            await self.session.delete(row)
            await self.session.flush()

    # ----------------------------------------------------------- chiqarish --

    async def _row(self, hotel_id: UUID, registration_id: UUID) -> EmehmonRegistration:
        row = (
            await self.session.execute(
                select(EmehmonRegistration).where(
                    EmehmonRegistration.id == registration_id,
                    EmehmonRegistration.hotel_id == hotel_id,
                )
            )
        ).scalars().first()
        if row is None:
            raise NotFoundException("Ro'yxat topilmadi", "EMEHMON_REGISTRATION_NOT_FOUND")
        return row

    async def checkout(
        self,
        hotel_id: UUID,
        registration_id: UUID,
        amount: float | None = None,
        payment_type_id: int | None = None,
        user_id=None,
    ) -> dict:
        """Mehmonni E-mehmon'dan chiqaradi (`POST /listok/{id}/checkout`).

        `amount` / `payment_type_id` berilmasa — bron bo'yicha hisoblanadi
        (avtomatik chiqarish shunday chaqiradi; `user_id` bo'sh).
        """
        row = await self._row(hotel_id, registration_id)
        if row.status == ST_CHECKED_OUT:
            return serialize(row)
        if row.status != ST_REGISTERED or not row.listok_id:
            raise ValidationException(
                "Faqat ro'yxatga olingan mehmon chiqariladi", "EMEHMON_NOT_REGISTERED"
            )
        cfg, client = await self._ready(hotel_id, row.branch_id)
        if row.environment != cfg["environment"]:
            raise ValidationException(
                "Bu ro'yxat boshqa muhitda (sinov / ishchi) yaratilgan", "EMEHMON_ENVIRONMENT_MISMATCH"
            )
        if amount is None or payment_type_id is None:
            reservation = (
                await self.session.get(Reservation, row.reservation_id)
                if row.reservation_id else None
            )
            siblings = list(
                (await self._latest_rows([row.reservation_id])).values()
            ) if row.reservation_id else [row]
            defaults = await self._checkout_defaults(cfg, reservation, siblings)
            amount = defaults["sum"] if amount is None else amount
            payment_type_id = defaults["payment_type_id"] if payment_type_id is None else payment_type_id
        amount = max(0, int(round(float(amount))))
        if payment_type_id not in ep.PAYMENT_TYPES:
            raise ValidationException("To'lov turi noto'g'ri", "EMEHMON_PAYMENT_TYPE_INVALID")

        busy_key = f"out:{row.id}"
        if busy_key in _busy:
            raise ConflictException("Bu mehmon hozir chiqarilmoqda — biroz kuting", "EMEHMON_BUSY")
        _busy.add(busy_key)
        try:
            same = (
                row.checkout_key
                and row.checkout_sum is not None
                and int(round(float(row.checkout_sum))) == amount
                and row.checkout_payment_type == payment_type_id
            )
            key = row.checkout_key if same else str(uuid.uuid4())
            row.checkout_key = key
            row.checkout_sum = amount
            row.checkout_payment_type = payment_type_id
            row.checkout_attempts = (row.checkout_attempts or 0) + 1
            row.checkout_tried_at = _now()
            await self.session.commit()

            try:
                reply = await client.request(
                    "POST",
                    f"/listok/{row.listok_id}/checkout",
                    body={"sum": amount, "payment_type_id": payment_type_id},
                    idempotency_key=key,
                )
                data = reply.data
            except ep.EmehmonError as exc:
                if exc.error_code != "LISTOK_ALREADY_CHECKED_OUT":
                    row.checkout_error_code = exc.error_code
                    row.checkout_error = exc.text
                    await self.session.flush()
                    return serialize(row)
                # Kabinetda qo'lda yopilgan — bizda ham yopilgan deb belgilanadi
                data = {}
                row.note = "E-mehmon'da avvalroq yopilgan"
            row.status = ST_CHECKED_OUT
            row.checked_out_at = _now()
            row.checkout_by = _uuid(user_id)
            row.checkout_error_code = row.checkout_error = None
            row.date_visit_off = ep._text(data.get("dateVisitOff"), 40) or None
            await self.session.flush()
            return serialize(row)
        finally:
            _busy.discard(busy_key)

    async def refresh(self, hotel_id: UUID, registration_id: UUID) -> dict:
        """E-mehmon'dagi holat bilan solishtiradi: kabinetda yopilgan bo'lsa —
        bizda ham yopiladi."""
        row = await self._row(hotel_id, registration_id)
        if row.status != ST_REGISTERED or not row.listok_id:
            return serialize(row)
        _cfg, client = await self._ready(hotel_id, row.branch_id)
        try:
            reply = await client.request("GET", f"/listok/{row.listok_id}")
        except ep.EmehmonError as exc:
            raise ValidationException(exc.text, f"EMEHMON_{exc.error_code}") from None
        closed = reply.data.get("datevisitoff")
        if closed:
            row.status = ST_CHECKED_OUT
            row.checked_out_at = _now()
            row.date_visit_off = ep._text(closed, 40)
            row.checkout_error_code = row.checkout_error = None
            row.note = "E-mehmon'da yopilgan"
            await self.session.flush()
        return serialize(row)

    # -------------------------------------------------------------- navbat --

    async def pending(self, hotel_id: UUID) -> dict:
        """Xodim e'tiborini kutayotganlar: ro'yxatga olinmagan mehmonlar va
        E-mehmon'dan chiqarilmay qolganlar."""
        cfg = await self.config(hotel_id)
        empty = {"enabled": cfg["enabled"], "register": [], "checkout": [], "count": 0}
        if not cfg["enabled"]:
            return empty
        now = _now()
        since = ep.enabled_since(cfg) or now
        staying = (
            await self.session.execute(
                select(Reservation).where(
                    Reservation.hotel_id == hotel_id, Reservation.status == STAYING
                )
            )
        ).scalars().all()
        registered = (
            await self.session.execute(
                select(EmehmonRegistration).where(
                    EmehmonRegistration.hotel_id == hotel_id,
                    EmehmonRegistration.status == ST_REGISTERED,
                )
            )
        ).scalars().all()
        by_id = {r.id: r for r in staying}
        missing = {r.reservation_id for r in registered if r.reservation_id} - set(by_id)
        if missing:
            others = (
                await self.session.execute(select(Reservation).where(Reservation.id.in_(missing)))
            ).scalars().all()
            by_id.update({r.id: r for r in others})
        arrivals = await self._arrivals(hotel_id, list(staying))
        latest = await self._latest_rows([r.id for r in staying])
        rooms = {}
        room_ids = {r.room_id for r in by_id.values()}
        if room_ids:
            rooms = {
                rid: number
                for rid, number in (
                    await self.session.execute(
                        select(Room.id, Room.room_number).where(Room.id.in_(room_ids))
                    )
                ).all()
            }
        earliest, _latest_allowed = ep.arrival_window(now)

        todo: list[dict] = []
        people: list[tuple[Reservation, dict, EmehmonRegistration | None, str]] = []
        for reservation in staying:
            arrival = arrivals.get(reservation.id)
            for person in self._persons(reservation, arrival):
                if not person["present"]:
                    continue
                moment = person.get("since")
                if moment is None or moment < since:
                    # Integratsiya yoqilishidan oldin kirganlar so'ralmaydi
                    continue
                row = latest.get((reservation.id, person["guest_id"]))
                state = person_state(row, person, now)
                if state in (ST_NONE, ST_FAILED):
                    people.append((reservation, person, row, state))
        guests = await self._guests(p["guest_id"] for _r, p, _row, _s in people)
        for reservation, person, row, state in people:
            guest = guests.get(person["guest_id"])
            moment = person.get("since")
            todo.append({
                "reservation_id": str(reservation.id),
                "reservation_number": reservation.reservation_number,
                "room_number": rooms.get(reservation.room_id),
                "guest_id": str(person["guest_id"]),
                "role": person["role"],
                "name": (
                    " ".join(filter(None, [guest.last_name, guest.first_name])) if guest else None
                ) or person.get("name") or "—",
                "state": state,
                "error": serialize(row, now)["error"] if row is not None and state == ST_FAILED else None,
                "since": _iso(moment),
                "overdue": bool(moment and moment < earliest),
            })

        leaving: list[dict] = []
        for row in registered:
            reservation = by_id.get(row.reservation_id) if row.reservation_id else None
            if reservation is not None and self._still_inside(reservation, row.guest_id):
                continue
            leaving.append({
                **serialize(row, now),
                "reservation_number": reservation.reservation_number if reservation else None,
                "room_number": rooms.get(reservation.room_id) if reservation else None,
                "auto": cfg["auto_checkout"]
                and (row.checkout_attempts or 0) < AUTO_CHECKOUT_ATTEMPTS,
            })
        todo.sort(key=lambda item: item["since"] or "")
        attention = sum(1 for item in leaving if not item["auto"] or item["checkout_error"])
        return {
            "enabled": True,
            "register": todo,
            "checkout": leaving,
            "count": len(todo) + attention,
        }

    def _still_inside(self, reservation: Reservation, guest_id: UUID | None) -> bool:
        """Mehmon hali xonadami (bron ochiq va hamroh ketmagan)."""
        if reservation.status != STAYING:
            return False
        for person in self._persons(reservation, None):
            if str(person["guest_id"]) == str(guest_id):
                return person["present"]
        # Brondan olib tashlangan hamroh — endi turmaydi
        return False

    async def journal(
        self,
        hotel_id: UUID,
        *,
        status: str | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 30,
    ) -> dict:
        """Ro'yxatlar jurnali (eng yangisi tepada)."""
        conditions = [EmehmonRegistration.hotel_id == hotel_id]
        if status:
            conditions.append(EmehmonRegistration.status == status)
        text = (search or "").strip()
        if text:
            like = f"%{text}%"
            conditions.append(or_(
                EmehmonRegistration.full_name.ilike(like),
                EmehmonRegistration.passport_number.ilike(like),
                EmehmonRegistration.reg_num.ilike(like),
            ))
        total = (
            await self.session.execute(
                select(func.count(EmehmonRegistration.id)).where(*conditions)
            )
        ).scalar() or 0
        rows = (
            await self.session.execute(
                select(EmehmonRegistration)
                .where(*conditions)
                .order_by(EmehmonRegistration.created_at.desc(), EmehmonRegistration.id)
                .offset(max(skip, 0))
                .limit(max(1, min(limit, 100)))
            )
        ).scalars().all()
        numbers = {}
        reservation_ids = {r.reservation_id for r in rows if r.reservation_id}
        if reservation_ids:
            numbers = {
                rid: number
                for rid, number in (
                    await self.session.execute(
                        select(Reservation.id, Reservation.reservation_number)
                        .where(Reservation.id.in_(reservation_ids))
                    )
                ).all()
            }
        now = _now()
        return {
            "total": total,
            "items": [
                {**serialize(r, now), "reservation_number": numbers.get(r.reservation_id)}
                for r in rows
            ],
        }


# ------------------------------------------------------ fon vazifasi --


def _backoff(attempts: int) -> timedelta:
    """Muvaffaqiyatsiz urinishdan keyin kutish: 1, 2, 4, 8, 16 daqiqa."""
    return timedelta(minutes=2 ** max(min(attempts, 5) - 1, 0))


async def due_checkouts(session: AsyncSession, now: datetime | None = None, limit: int = 200) -> list[EmehmonRegistration]:
    """Avtomatik chiqarilishi kerak bo'lgan ro'yxatlar: mehmon bizda
    chiqarilgan (yoki hamroh ketgan), E-mehmon'da esa hali ochiq."""
    now = now or _now()
    open_listok = (
        EmehmonRegistration.status == ST_REGISTERED,
        EmehmonRegistration.listok_id.is_not(None),
        EmehmonRegistration.checkout_attempts < AUTO_CHECKOUT_ATTEMPTS,
    )
    oldest_first = (
        EmehmonRegistration.checkout_tried_at.is_not(None),
        EmehmonRegistration.checkout_tried_at,
    )
    # Xonada turgan mehmonlar (ularning ro'yxati ochiq turishi KERAK) bu yerga
    # tushmaydi — katta mehmonxonada ham navbat faqat ketganlardan iborat:
    # 1) broni yopilganlar; 2) broni ochiq, lekin o'zi ketgan bo'lishi mumkin
    #    bo'lgan hamrohlar (ketgani pastda, bron yozuvidan tekshiriladi).
    closed = (
        await session.execute(
            select(EmehmonRegistration)
            .join(Reservation, Reservation.id == EmehmonRegistration.reservation_id)
            .where(*open_listok, Reservation.status != STAYING)
            .order_by(*oldest_first)
            .limit(limit)
        )
    ).scalars().all()
    companions = (
        await session.execute(
            select(EmehmonRegistration)
            .join(Reservation, Reservation.id == EmehmonRegistration.reservation_id)
            .where(*open_listok, Reservation.status == STAYING, EmehmonRegistration.role == "companion")
            .order_by(*oldest_first)
            .limit(limit)
        )
    ).scalars().all()
    rows = [*closed, *companions]
    if not rows:
        return []
    reservations = {
        r.id: r
        for r in (
            await session.execute(
                select(Reservation).where(Reservation.id.in_({r.reservation_id for r in rows}))
            )
        ).scalars().all()
    }
    helper = EmehmonService(session)
    due = []
    for row in rows:
        tried = _aware(row.checkout_tried_at)
        if tried and row.checkout_attempts and now - tried < _backoff(row.checkout_attempts):
            continue
        reservation = reservations.get(row.reservation_id)
        if reservation is None:
            continue
        if reservation.status == STAYING:
            person = next(
                (p for p in helper._persons(reservation, None) if str(p["guest_id"]) == str(row.guest_id)),
                None,
            )
            if person is not None and person["present"]:
                continue
            left = person.get("left_at") if person else None
            if left is not None and now - left < LEAVE_GRACE:
                continue
        due.append(row)
    return due


async def run_checkout_sweep(limit: int = 20) -> int:
    """Bizda chiqarilgan mehmonlarni E-mehmon'dan ham chiqaradi.

    Faqat integratsiya yoqilgan va «avtomatik chiqarish» o'chirilmagan
    filiallar uchun. Har ro'yxat — alohida sessiyada, o'z filiali doirasida:
    bittasidagi xato qolganlarini to'xtatmaydi. E-mehmon ishlatilmaydigan
    tizimda bu — bitta bo'sh so'rov.
    """
    from app.core.database import _get_session_factory
    from app.infrastructure.tenant.branch_scope import set_branch_scope

    factory = _get_session_factory()
    async with factory() as session:
        candidates = [
            (row.id, row.hotel_id, row.branch_id, row.environment)
            for row in await due_checkouts(session)
        ]
    done = 0
    #: (mehmonxona, filial) → avtomatik chiqarish yoqilgan muhit (yoki `None`)
    allowed: dict[tuple, str | None] = {}
    for registration_id, hotel_id, branch_id, environment in candidates:
        if done >= limit:
            break
        try:
            async with factory() as session:
                set_branch_scope(session, hotel_id, branch_id)
                service = EmehmonService(session)
                scope = (hotel_id, branch_id)
                if scope not in allowed:
                    cfg = await service.config(hotel_id, branch_id)
                    ready = cfg["enabled"] and cfg["auto_checkout"] and cfg["username"] and secret_of(cfg)
                    allowed[scope] = cfg["environment"] if ready else None
                # Boshqa muhitda (sinov ↔ ishchi) yaratilgan ro'yxatga tegilmaydi
                if allowed[scope] is None or allowed[scope] != environment:
                    continue
                result = await service.checkout(hotel_id, registration_id)
                await session.commit()
                if result["status"] == ST_CHECKED_OUT:
                    done += 1
        except Exception:  # noqa: BLE001 — bitta ro'yxat xatosi qolganlarini to'xtatmasin
            logger.warning("E-mehmon avtomatik chiqarish bajarilmadi", exc_info=True)
    return done
