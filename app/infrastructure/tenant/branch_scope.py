"""Filial chegarasi — har so'rov BITTA filial ichida ishlaydi.

Mehmonxona filiallari to'liq ajratilgan: xodim faqat o'z filialining
xonalari, mehmonlari, bronlari, kassasi, hisobotlari va sozlamalarini
ko'radi. Administrator boshqa filialni faqat tanlab (POST /auth/context)
ko'radi — xuddi sozlovchi kabi.

Qanday ishlaydi (endpoint va servislar o'zgarmasdan):

* `get_current_user` tokendagi filialni sessiyaga yozadi
  (`set_branch_scope`).
* `BranchScoped` belgili modellarning har SELECT/UPDATE/DELETE so'roviga
  `branch_id = <filial>` sharti qo'shiladi (`with_loader_criteria`,
  JOIN va subquery'dagi shu jadval ham). `BranchShared` — filiali bo'sh
  yozuv ham ko'rinadi (global xona turi, umumiy kamera kompyuteri).
* Bog'lanish orqali yuklash (lazy/selectin) filtrlanmaydi: ota yozuv
  allaqachon shu filialniki, uning bog'lanishi esa uzilmasligi kerak.
* Yangi yozuvga filial avtomatik yoziladi (`before_flush`); boshqa
  filialga yozish yoki mavjud yozuvni boshqa filialga ko'chirish
  taqiqlanadi (403 `BRANCH_SCOPE`).
* Xodimlar (`users`) avtomatik filtrlanmaydi — ism ko'rsatish uchun ko'p
  joyda id bo'yicha o'qiladi, xodim boshqa filialga o'tkazilishi ham
  mumkin. Xodimlar RO'YXATI va xabar oluvchilar filial bo'yicha aniq
  filtrlanadi (`users_in_branch`).
* Sessiyada filial bo'lmasa (fon vazifalari, kirish, tizim ma'muri
  "barcha mehmonxonalar" rejimida) — avvalgidek hamma narsa ko'rinadi.
  Fon vazifasi filial bo'yicha ishlashi kerak bo'lsa, u ham
  `set_branch_scope` qiladi.
* Ataylab hamma filialni ko'rish kerak bo'lgan so'rov:
  `.execution_options(all_branches=True)` yoki
  `with without_branch_scope(session): ...`.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID

from sqlalchemy import event, inspect, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from app.shared.mixins import BranchScoped, BranchShared

logger = logging.getLogger(__name__)

SCOPE_KEY = "gohotel.branch_scope"
MAIN_CACHE_KEY = "gohotel.main_branch"
#: Shu bajarish opsiyasi bilan so'rov filial filtrisiz ishlaydi
ALL_BRANCHES = "all_branches"

#: Yozuvi boshqa filialga biriktirilishi mumkin bo'lgan modellar
#: (kamera / kamera kompyuterini filialga biriktirish — sozlama)
REASSIGNABLE = ("vision_cameras", "vision_devices")


@dataclass(frozen=True)
class BranchScope:
    hotel_id: UUID
    branch_id: UUID


def _info(session) -> dict | None:
    """Sessiya `info` lug'ati (`AsyncSession` ham, `Session` ham). Testdagi
    soxta sessiyalarda bo'lmasligi mumkin — unda filial yo'q deb olinadi."""
    info = getattr(session, "info", None)
    return info if isinstance(info, dict) else None


def set_branch_scope(session, hotel_id: UUID | None, branch_id: UUID | None) -> None:
    """Sessiyadagi barcha keyingi so'rovlar shu filial bilan cheklanadi."""
    info = _info(session)
    if info is None:
        return
    if hotel_id and branch_id:
        info[SCOPE_KEY] = BranchScope(hotel_id, branch_id)
    else:
        info.pop(SCOPE_KEY, None)


def get_branch_scope(session) -> BranchScope | None:
    info = _info(session)
    return info.get(SCOPE_KEY) if info is not None else None


def scoped_branch_id(session, hotel_id: UUID | None = None) -> UUID | None:
    """Joriy filial (berilgan mehmonxonaniki bo'lsa)."""
    scope = get_branch_scope(session)
    if scope is None:
        return None
    if hotel_id is not None and str(scope.hotel_id) != str(hotel_id):
        return None
    return scope.branch_id


@contextmanager
def without_branch_scope(session) -> Iterator[None]:
    """Blok ichida filial filtrisiz (masalan, unikal nomni butun bazada
    tekshirish yoki mehmonxona darajasidagi qurilma tasdig'i)."""
    info = _info(session)
    saved = info.pop(SCOPE_KEY, None) if info is not None else None
    try:
        yield
    finally:
        if saved is not None:
            info[SCOPE_KEY] = saved


def users_in_branch(user_model, session, hotel_id: UUID | None = None):
    """Xodimlar ro'yxati uchun shart: joriy filial xodimlari + mehmonxona
    administratorlari (ular hamma filialni boshqaradi). Filial yo'q bo'lsa
    — shart yo'q (`None`)."""
    branch_id = scoped_branch_id(session, hotel_id)
    if branch_id is None:
        return None
    return or_(user_model.branch_id == branch_id, user_model.user_type == "ADMIN")


# ------------------------------------------------------------- filtr --
def _add_branch_criteria(state) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load or state.is_relationship_load:
        return
    scope = state.session.info.get(SCOPE_KEY)
    if scope is None or state.execution_options.get(ALL_BRANCHES):
        return
    branch_id = scope.branch_id
    state.statement = state.statement.options(
        with_loader_criteria(
            BranchScoped,
            lambda cls: cls.branch_id == branch_id,
            include_aliases=True,
            propagate_to_loaders=False,
        ),
        with_loader_criteria(
            BranchShared,
            lambda cls: or_(cls.branch_id == branch_id, cls.branch_id.is_(None)),
            include_aliases=True,
            propagate_to_loaders=False,
        ),
    )


# ------------------------------------------------- yozishda filial --
def _main_branch_id(session: Session, hotel_id) -> UUID | None:
    if hotel_id is None:
        return None
    cache = session.info.setdefault(MAIN_CACHE_KEY, {})
    key = str(hotel_id)
    if key not in cache:
        from app.infrastructure.database.models.branch import Branch

        with session.no_autoflush:
            cache[key] = session.execute(
                select(Branch.id)
                .where(Branch.hotel_id == hotel_id)
                .order_by(Branch.is_main_branch.desc(), Branch.created_at, Branch.id)
                .limit(1)
            ).scalar()
    return cache[key]


def _parent_branch_id(session: Session, obj) -> UUID | None:
    """Filiali ma'lum ota yozuvdan (xona, bron, mehmon, savdo...)."""
    mapper = inspect(obj).mapper
    for column in mapper.local_table.columns:
        if column.name == "branch_id":
            continue
        for fk in column.foreign_keys:
            target = _SCOPED_BY_TABLE.get(fk.column.table.name)
            if target is None:
                continue
            prop = mapper.get_property_by_column(column)
            value = getattr(obj, prop.key, None)
            if value is None:
                continue
            with session.no_autoflush:
                parent = session.get(target, value, execution_options={ALL_BRANCHES: True})
            branch_id = getattr(parent, "branch_id", None)
            if branch_id is not None:
                return branch_id
    return None


def _before_flush(session: Session, flush_context, instances) -> None:
    scope = session.info.get(SCOPE_KEY)
    for obj in list(session.new):
        if not isinstance(obj, (BranchScoped, BranchShared)):
            continue
        hotel_id = getattr(obj, "hotel_id", None)
        same_hotel = scope is not None and (hotel_id is None or str(hotel_id) == str(scope.hotel_id))
        current = getattr(obj, "branch_id", None)
        if current is not None:
            if same_hotel and str(current) != str(scope.branch_id) and obj.__tablename__ not in REASSIGNABLE:
                from app.core.exceptions import ForbiddenException

                raise ForbiddenException(
                    "Boshqa filial ma'lumotiga yozib bo'lmaydi", "BRANCH_SCOPE"
                )
            continue
        if isinstance(obj, BranchShared) and obj.__tablename__ in REASSIGNABLE:
            # Bo'sh filial — "umumiy" ma'nosida; avtomatik to'ldirilmaydi
            continue
        if isinstance(obj, BranchShared) and hotel_id is None:
            continue  # global yozuv (masalan, global xona turi)
        if same_hotel:
            obj.branch_id = scope.branch_id
            continue
        branch_id = _parent_branch_id(session, obj) or _main_branch_id(session, hotel_id)
        if branch_id is None:
            logger.warning(
                "Filialsiz yozuv: %s (hotel_id=%s) — mehmonxonada filial yo'q",
                obj.__tablename__, hotel_id,
            )
        obj.branch_id = branch_id

    if scope is None:
        return
    # Mavjud yozuvni boshqa filialga ko'chirish — filial ichida bo'lmaydi
    for obj in list(session.dirty):
        if not isinstance(obj, (BranchScoped, BranchShared)) or obj.__tablename__ in REASSIGNABLE:
            continue
        history = inspect(obj).attrs.branch_id.history
        if history.has_changes() and history.deleted and history.deleted[0] is not None:
            from app.core.exceptions import ForbiddenException

            raise ForbiddenException(
                "Yozuvni boshqa filialga ko'chirib bo'lmaydi", "BRANCH_SCOPE"
            )


_SCOPED_BY_TABLE: dict = {}
_installed = False


def install_branch_scope() -> None:
    """Hodisalarni bir marta ro'yxatdan o'tkazadi (ilova yuklanganda)."""
    global _installed
    if _installed:
        return
    import app.infrastructure.database.models  # noqa: F401 — barcha modellar
    from app.infrastructure.database.models.base import Base

    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, (BranchScoped, BranchShared)):
            _SCOPED_BY_TABLE[cls.__tablename__] = cls
    event.listen(Session, "do_orm_execute", _add_branch_criteria)
    event.listen(Session, "before_flush", _before_flush)
    _installed = True
