"""Tozalash vazifasini qaysi farroshga berish — yagona qoida.

Ilgari bu mantiq ikki joyda (reservation_service va automation_service)
alohida yozilgan edi va "farrosh" deb `housekeeping.*` ruxsati bor HAR
QANDAY xodimni olardi. Menejerda `housekeeping.*` to'liq, texnik xodimda
`housekeeping.task.*` bor — ular ham ro'yxatga tushib, "Mijoz chiqmoqda —
xonani tozalang" pushini olardi. Farroshning o'zi esa vazifani ko'rmay
qolardi.

Endi qoida shunday:

  1. Xodimning roli ruxsat kodlaridan aniqlanadi — mobil ilovadagi
     `RoleRegistry` bilan BIR XIL tartibda (kengroq huquq birinchi:
     menejer → qabulxona → texnik → farrosh). Bu muhim: vazifa aynan
     ilovada farrosh bo'limini ko'radigan odamga boradi.
  2. Nomzod — faqat FAOL (status ACTIVE, o'chirilmagan) farrosh. Iloji
     bo'lsa o'sha filialdan. Bo'lim boshlig'i (`housekeeping.task.assign`)
     oddiy farrosh bo'lmaganda oladi — u vazifa taqsimlovchi, bajaruvchi emas.
  3. Bo'sh farrosh (faol vazifasi yo'q) birinchi; hammasi band bo'lsa —
     eng kam vazifalisi; teng bo'lsa — vazifani eng uzoq vaqt olmagani
     (navbat). Shunday qilib ikki farroshda bittadan xona bo'lsa, keyingi
     xona birinchisiga, undan keyingisi ikkinchisiga tushadi.
  4. Mehmonxonada birorta ham farrosh rolidagi xodim bo'lmasa — eski keng
     qoida (istalgan `housekeeping.*` egasi) ishlaydi, vazifa yetim
     qolmasligi uchun. Topilmasa None: vazifa biriktirilmay yaratiladi va
     ro'yxatda ko'rinadi (avvalgidek).
  5. Faqat ISH VAQTIDAGI farrosh: xodimning `work_start`–`work_end` oralig'i
     (tungi smena — yarim tundan o'tuvchi ham) mahalliy vaqt bilan
     tekshiriladi. Hech kim ishda bo'lmasa — None: vazifa biriktirilmaydi,
     push ketmaydi; farrosh ishga kelishi bilan rejalashtiruvchi
     (automation_service._assign_pending_tasks) uni biriktiradi. Ish vaqti
     yozilmagan/buzuq bo'lsa xodim ishda deb hisoblanadi — ma'lumot xatosi
     vazifani to'sib qo'ymasin.

Qaror mantig'i (`pick_cleaner`, `is_on_duty`) bazaga bog'lanmagan sof
funksiyalar — ular alohida sinovdan o'tadi (tests/test_cleaner_assignment.py).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.infrastructure.database.models.permission import Permission, UserPermission
from app.infrastructure.database.models.user import User

ROLE_MANAGER = "manager"
ROLE_RECEPTION = "reception"
ROLE_MAINTENANCE = "maintenance"
ROLE_HOUSEKEEPER = "housekeeper"
ROLE_STAFF = "staff"

#: Qabulxona xodimini belgilovchi kodlar — presentation/api/v1/reception.py
#: dagi RECEPTION_CODES va mobil RoleRegistry bilan bir xil bo'lishi SHART.
RECEPTION_CODES = ("reservation.read", "reservation.create", "reservation.update")

#: Vazifa hisobida "faol" sanaladigan holatlar
ACTIVE_TASK_STATUSES = ("OPEN", "IN_PROGRESS")


def classify_role(codes: Iterable[str]) -> str:
    """Ruxsat kodlaridan xodim rolini aniqlaydi.

    Tartib mobil ilovadagi RoleRegistry.modules bilan bir xil — kengroq
    huquqli rol birinchi tekshiriladi (menejerda ham housekeeping.* bor,
    lekin u farrosh emas). Bu ikkalasi ajralib ketsa, server vazifani bir
    odamga beradi, ilova esa uni boshqa bo'limda ko'rsatadi.
    """
    code_set = set(codes)
    if any(c.startswith("shift.") for c in code_set) or "employee.create" in code_set:
        return ROLE_MANAGER
    if any(c in code_set for c in RECEPTION_CODES):
        return ROLE_RECEPTION
    if (
        "housekeeping.task.create" in code_set
        and "housekeeping.task.assign" not in code_set
    ):
        return ROLE_MAINTENANCE
    if any(c.startswith("housekeeping.") for c in code_set) or "room.status.update" in code_set:
        return ROLE_HOUSEKEEPER
    return ROLE_STAFF


@dataclass(frozen=True)
class CleanerCandidate:
    """Tanlov uchun kerakli minimal ma'lumot (baza qatorlaridan yig'iladi)."""

    user_id: UUID
    branch_id: UUID | None
    codes: frozenset[str]
    #: OPEN/IN_PROGRESS vazifalar soni — yuklama
    active_tasks: int = 0
    #: Oxirgi marta vazifa olgan vaqti (navbat uchun); hech olmagan — None
    last_assigned_at: datetime | None = None
    #: Barqaror tartib (ishga olinish vaqti) — hamma narsa teng bo'lganda
    order: int = 0
    #: Hozir ish vaqtidami (work_start–work_end); yuklovchi hisoblaydi
    on_duty: bool = True


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        hours, minutes = value.strip().split(":")[:2]
        return time(hour=int(hours), minute=int(minutes))
    except (ValueError, AttributeError):
        return None


def is_on_duty(work_start: str | None, work_end: str | None, now: time) -> bool:
    """Xodim `now` (mahalliy soat) da ish vaqtidami.

    "09:00"–"18:00" — kunduzgi smena: boshlanish kirgan, tugash kirmagan.
    "22:00"–"06:00" — tungi smena, yarim tundan o'tadi.
    Boshlanish = tugash yoki qiymat buzuq/yo'q — doim ishda (to'sib qo'ymaymiz).
    """
    start, end = _parse_hhmm(work_start), _parse_hhmm(work_end)
    if start is None or end is None or start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def local_time_now() -> time:
    """Mehmonxona mahalliy soati (server UTC da, ofset sozlamada)."""
    return (
        datetime.now(timezone.utc) + timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
    ).time()


def _queue_key(c: CleanerCandidate) -> tuple:
    # Kam yuklama → hech vazifa olmagan → eng uzoq vaqt olmagan → tartib.
    # `.timestamp()` naive va tz-aware datetime uchun bir xil ishlaydi —
    # ularni to'g'ridan-to'g'ri solishtirish TypeError berardi.
    never = c.last_assigned_at is None
    ts = 0.0 if never else c.last_assigned_at.timestamp()
    return (c.active_tasks, 0 if never else 1, ts, c.order)


def pick_cleaner(
    candidates: Iterable[CleanerCandidate], branch_id: UUID | None
) -> UUID | None:
    """Nomzodlar ichidan tozalash vazifasi beriladigan farroshni tanlaydi.

    Faqat qaror: nomzodlar allaqachon FAOL xodimlar deb hisoblanadi.
    """
    everyone = list(candidates)
    cleaners = [c for c in everyone if classify_role(c.codes) == ROLE_HOUSEKEEPER]
    if cleaners:
        # Ish vaqtidan tashqaridagi farroshga vazifa ketmaydi — hech kim ishda
        # bo'lmasa vazifa biriktirilmay qoladi, keyin rejalashtiruvchi
        # farrosh ishga kelishi bilan biriktiradi
        on_duty = [c for c in cleaners if c.on_duty]
        if not on_duty:
            return None
        pool = _prefer_branch(on_duty, branch_id)
        # Bo'lim boshlig'i taqsimlovchi — oddiy farrosh bor ekan, vazifa unga
        # emas, bajaruvchiga boradi
        line_staff = [c for c in pool if "housekeeping.task.assign" not in c.codes]
        pool = line_staff or pool
    else:
        # Farrosh roli yo'q mehmonxona — avvalgi keng qoida (istalgan
        # housekeeping.* egasi, filial, eng kam yuklama), vazifa yetim
        # qolmasin (masalan, kichik hostelda tozalashni texnik qiladi).
        # Ish vaqti qoidasi bu yerda ham amal qiladi.
        legacy = [
            c
            for c in everyone
            if c.on_duty and any(code.startswith("housekeeping.") for code in c.codes)
        ]
        if not legacy:
            return None
        pool = _prefer_branch(legacy, branch_id)

    return min(pool, key=_queue_key).user_id


def _prefer_branch(
    pool: list[CleanerCandidate], branch_id: UUID | None
) -> list[CleanerCandidate]:
    """Iloji bo'lsa o'sha filialdagilar, bo'lmasa hammasi."""
    same_branch = [c for c in pool if c.branch_id == branch_id]
    return same_branch or pool


async def find_cleaner(
    session: AsyncSession, hotel_id: UUID, branch_id: UUID | None
) -> UUID | None:
    """Mehmonxonadagi faol xodimlarni yuklab, `pick_cleaner` ga beradi.

    Ruxsatlar bitta so'rovda olinadi (ilgari har xodim uchun alohida so'rov
    ketardi). Xodimlar ishga olinish tartibida — hamma narsa teng bo'lganda
    natija baza qatorlari tartibiga bog'liq bo'lib qolmasligi uchun.
    """
    staff_rows = await session.execute(
        select(User.id, User.branch_id, User.work_start, User.work_end)
        .where(
            User.user_type == "EMPLOYEE",
            User.hotel_id == hotel_id,
            User.is_deleted.is_(False),
            User.status == "ACTIVE",
        )
        .order_by(User.created_at, User.id)
    )
    staff = staff_rows.all()
    if not staff:
        return None
    ids = [row.id for row in staff]

    codes: dict[UUID, set[str]] = defaultdict(set)
    perm_rows = await session.execute(
        select(UserPermission.user_id, Permission.code)
        .join(Permission, Permission.id == UserPermission.permission_id)
        .where(UserPermission.user_id.in_(ids), Permission.is_active.is_(True))
    )
    for user_id, code in perm_rows.all():
        codes[user_id].add(code)

    active: dict[UUID, int] = {}
    load_rows = await session.execute(
        select(HousekeepingTask.assigned_to, func.count())
        .where(
            HousekeepingTask.assigned_to.in_(ids),
            HousekeepingTask.status.in_(list(ACTIVE_TASK_STATUSES)),
        )
        .group_by(HousekeepingTask.assigned_to)
    )
    for user_id, count in load_rows.all():
        active[user_id] = count

    last_assigned: dict[UUID, datetime] = {}
    last_rows = await session.execute(
        select(HousekeepingTask.assigned_to, func.max(HousekeepingTask.created_at))
        .where(HousekeepingTask.assigned_to.in_(ids))
        .group_by(HousekeepingTask.assigned_to)
    )
    for user_id, moment in last_rows.all():
        if moment is not None:
            last_assigned[user_id] = moment

    now_local = local_time_now()
    candidates = [
        CleanerCandidate(
            user_id=row.id,
            branch_id=row.branch_id,
            codes=frozenset(codes.get(row.id, ())),
            active_tasks=active.get(row.id, 0),
            last_assigned_at=last_assigned.get(row.id),
            order=index,
            on_duty=is_on_duty(row.work_start, row.work_end, now_local),
        )
        for index, row in enumerate(staff)
    ]
    return pick_cleaner(candidates, branch_id)
