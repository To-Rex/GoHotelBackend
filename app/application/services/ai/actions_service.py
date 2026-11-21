"""AI taklif qilgan YOZUVCHI amallar: taklif → tasdiq (bajarish) → orqaga qaytarish.

Qoidalar:

* AI faqat `catalog.ACTIONS` dagi amalni TAKLIF qiladi. O'chirish,
  sozlamalar, xodim/ruxsat o'zgartirish registrda yo'q.
* Bajarish — faqat xodim kartadagi "Tasdiqlash"ni bosganda, uning O'Z
  tokeni bilan oddiy API so'rovi (ruxsat, smena, filial, ish vaqti —
  hammasi o'sha joyda tekshiriladi).
* Ikki marta bosish ikki bron yaratmasin: holat atomik o'tadi
  (proposed/failed → executing) va darhol commit qilinadi.
* Orqaga qaytarish — bajarilganidan keyin saqlangan reja bo'yicha:
  bekor qilish, yaratilganini o'chirish, oldingi qiymatlarga qaytarish.
  AI yaratgan bronni XUDDI SHU xodim 30 daqiqa ichida, mehmon kirmagan va
  to'lov bo'lmagan holda qaytarsa — "bekor qilish" ruxsati bo'lmasa ham
  qaytariladi (xato bronni tuzatish; audit jurnaliga yoziladi). Boshqa
  hollarda xodimning o'z ruxsati amal qiladi.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.ai import catalog
from app.application.services.ai.executor import Auth, call_api
from app.application.services.ai.render import label_for
from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.ai import AiAction

logger = logging.getLogger(__name__)

TRUSTED_UNDO_MINUTES = 30
PROPOSAL_TTL_HOURS = 24
STALE_EXECUTING_MINUTES = 10

FIELD_LABELS = {
    "guest_id": "Mehmon", "room_id": "Xona", "branch_id": "Filial", "booking_type": "Bron turi",
    "check_in_date": "Kelish sanasi", "check_out_date": "Ketish sanasi", "check_in_datetime": "Kelish vaqti",
    "check_out_datetime": "Ketish vaqti", "adults": "Kattalar", "children": "Bolalar",
    "discount_amount": "Chegirma (so'm)", "discount_percent": "Chegirma (%)", "notes": "Izoh",
    "payment_amount": "To'lov summasi", "payment_method": "To'lov usuli", "payments": "To'lovlar",
    "companion_guest_ids": "Hamrohlar", "expected_companions": "Kutilayotgan hamrohlar",
    "check_out": "Yangi ketish vaqti", "new_room_id": "Yangi xona", "hotel_service_id": "Xizmat",
    "quantity": "Soni", "service_date": "Xizmat sanasi", "kind": "Turi", "amount": "Summa", "note": "Izoh",
    "reason": "Sabab", "refund_amount": "Qaytariladigan summa", "refund_method": "Qaytarish usuli",
    "acknowledge_debt": "Qarz bilan chiqishni tasdiqlash", "debt_note": "Qarz izohi", "invoice_id": "Hisob-faktura",
    "payment_date": "To'lov sanasi", "reference": "Raqam/havola", "title": "Nomi", "category": "Toifa",
    "expense_date": "Sana", "items": "Mahsulotlar", "reservation_id": "Bron", "task_type": "Vazifa turi",
    "priority": "Muhimlik", "assigned_to": "Mas'ul xodim", "scheduled_date": "Rejalangan sana", "status": "Holat",
    "first_name": "Ism", "last_name": "Familiya", "phone": "Telefon", "email": "Email",
    "passport_number": "Pasport raqami", "nationality": "Fuqarolik", "birth_date": "Tug'ilgan sana",
    "id_document_type": "Hujjat turi", "id_document_number": "Hujjat raqami", "address": "Manzil",
    "feedback_type": "Murojaat turi", "subject": "Mavzu", "body": "Matn", "resolution": "Yechim",
    "guest_name": "Mehmon ismi", "guest_phone": "Mehmon telefoni", "user_id": "Xodim",
    "entity_type": "Bog'liq tur", "entity_id": "Bog'liq yozuv",
}

VALUE_LABELS = {
    "CASH": "Naqd", "CARD": "Karta", "ONLINE": "Onlayn", "BANK_TRANSFER": "Bank o'tkazmasi",
    "CREDIT_CARD": "Kredit karta", "DEBIT_CARD": "Debet karta", "MOBILE_PAYMENT": "Mobil to'lov",
    "TRANSFER": "O'tkazma", "DAILY": "Kunlik", "HOURLY": "Soatlik", "REQUEST": "Talab",
    "SUGGESTION": "Taklif", "COMPLAINT": "Shikoyat", "LOW": "Past", "MEDIUM": "O'rta", "HIGH": "Yuqori",
    "LATE_CHECKOUT": "Kech chiqish", "DAMAGE": "Zarar", "OTHER": "Boshqa", "NEW": "Yangi",
    "IN_PROGRESS": "Jarayonda", "RESOLVED": "Hal qilindi", "REJECTED": "Rad etildi",
}

#: Holat maydonlari uchun ma'lum qiymatlar (sxemada oddiy "string")
STATUS_OPTIONS = {
    "room.status": ("AVAILABLE", "CLEANING", "MAINTENANCE", "OUT_OF_ORDER"),
    "task.status": ("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"),
    "problem.status": ("OPEN", "IN_PROGRESS", "RESOLVED"),
}
STATUS_LABELS = {
    "AVAILABLE": "Bo'sh", "CLEANING": "Tozalanmoqda", "MAINTENANCE": "Ta'mirda", "OUT_OF_ORDER": "Ishlamaydi",
    "OPEN": "Ochiq", "COMPLETED": "Bajarildi", "CANCELLED": "Bekor qilindi",
}

_TEXTAREA = {"notes", "note", "body", "reason", "resolution", "debt_note", "address"}
_UUID_RX = re.compile(r"^[0-9a-fA-F-]{36}$")


class ActionRejected(Exception):
    """Taklif registrga mos emas — model xatosi (asbob natijasida qaytadi)."""

    def __init__(self, message: str, dangerous: bool = False):
        super().__init__(message)
        self.dangerous = dangerous


_DANGEROUS_PATH = re.compile(r"(/users|/permissions|/superadmin|/auth|/settings|/devices|/apps|/maintenance|/branches)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _dig(data, key: str):
    cur = data
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def compact_result(data) -> dict | None:
    """Javobdan xodimga va modelga kerakli qisqa ma'lumot."""
    if not isinstance(data, dict):
        return None
    keys = (
        "id", "reservation_number", "number", "invoice_number", "payment_number", "status", "payment_status",
        "total_amount", "paid_amount", "amount", "total", "room_number", "check_in_date", "check_out_date",
        "title", "first_name", "last_name", "existing", "message",
    )
    out = {k: data[k] for k in keys if k in data and not isinstance(data[k], (dict, list))}
    for nested in ("penalty", "sale", "expense", "payment", "reservation", "task"):
        if isinstance(data.get(nested), dict):
            sub = compact_result(data[nested]) or {}
            for k, v in sub.items():
                out.setdefault(f"{nested}.{k}", v)
    return out or None


# -------------------------------------------------------------- maydonlar --


def _field_kind(name: str, type_text: str, spec_key: str) -> tuple[str, list[dict] | None]:
    t = type_text or "string"
    if t.startswith("enum("):
        values = [v for v in t[5:-1].split(",") if v]
        return "select", [{"value": v, "label": VALUE_LABELS.get(v, v)} for v in values]
    if name == "status" and spec_key in STATUS_OPTIONS:
        return "select", [{"value": v, "label": STATUS_LABELS.get(v, v)} for v in STATUS_OPTIONS[spec_key]]
    if t.startswith("array") or t.startswith("{"):
        return "json", None
    if t.startswith("boolean"):
        return "checkbox", None
    if t.startswith("integer"):
        return "integer", None
    if t.startswith("number"):
        return "number", None
    if t == "string:date":
        return "date", None
    if t == "string:date-time":
        return "datetime", None
    if t == "string:uuid" or name.endswith("_id") or name == "assigned_to":
        return "id", None
    if name in _TEXTAREA:
        return "textarea", None
    return "text", None


def _schema_fields(method: str, path: str) -> list[dict] | None:
    ep = catalog.describe(method, path)
    if ep is None or not ep.body:
        return None
    return ep.body["fields"]


def build_fields(action: AiAction) -> list[dict]:
    body = action.body or {}
    meta = action.result or {}
    labels = meta.get("labels") or {}
    schema = _schema_fields(action.method, action.path)
    if schema is None:
        schema = [{"name": k, "type": "string", "required": False, "description": None} for k in body]
    out = []
    for f in schema:
        name = f["name"]
        kind, options = _field_kind(name, f.get("type") or "string", action.kind)
        out.append(
            {
                "name": name,
                "label": FIELD_LABELS.get(name) or label_for(name),
                "kind": kind,
                "required": bool(f.get("required")),
                "value": body.get(name),
                "display": labels.get(name),
                "options": options,
                "in_body": name in body,
                "hint": f.get("description"),
            }
        )
    return out


def _coerce(field: dict, value):
    """Formadan kelgan qiymatni maydon turiga keltiradi (None — olib tashlash)."""
    kind = field["kind"]
    if value is None or (isinstance(value, str) and not value.strip() and kind not in ("json",)):
        return None
    label = field["label"]
    try:
        if kind == "integer":
            return int(float(value))
        if kind == "number":
            n = float(value)
            return int(n) if n.is_integer() else n
        if kind == "checkbox":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "ha", "yes", "on")
            return bool(value)
        if kind == "date":
            return date.fromisoformat(str(value)[:10]).isoformat()
        if kind == "datetime":
            text = str(value).strip().replace("Z", "+00:00")
            datetime.fromisoformat(text)
            return text
        if kind == "id":
            text = str(value).strip()
            UUID(text)
            return text
        if kind == "select":
            text = str(value).strip()
            allowed = {o["value"] for o in field.get("options") or []}
            if allowed and text not in allowed:
                raise ValueError(text)
            return text
        if kind == "json":
            if isinstance(value, (list, dict)):
                return value
            raise ValueError("json")
        text = str(value)
        return text[:4000]
    except (TypeError, ValueError) as exc:
        raise ValidationException(f"«{label}» maydoni noto'g'ri", "AI_ACTION_FIELD_INVALID") from exc


def _missing_required(action: AiAction) -> list[str]:
    body = action.body or {}
    return [f["label"] for f in build_fields(action) if f["required"] and body.get(f["name"]) in (None, "", [])]


# ------------------------------------------------------------- xizmat --


class AiActionService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -------------------------------------------------------- taklif --

    def validate_proposal(self, method: str, path: str) -> tuple[catalog.ActionSpec, dict, str]:
        m = (method or "").upper()
        p = catalog.normalize_path(path)
        if m == "DELETE":
            raise ActionRejected(
                "Deleting data is not available to the assistant. Refuse politely and suggest the "
                "non-destructive alternative (cancel / void / edit).",
                dangerous=True,
            )
        found = catalog.find_action(m, p)
        if found is None:
            raise ActionRejected(
                f"{m} {p} is not an allowed assistant action. Allowed actions: "
                + "; ".join(f"{s.method} {s.pattern.strip('^$')} ({s.title})" for s in catalog.ACTIONS[:40]),
                dangerous=bool(_DANGEROUS_PATH.search(p)),
            )
        spec, groups = found
        return spec, groups, catalog.canonical_path(m, p)

    async def propose(
        self,
        *,
        ctx: dict,
        conversation_id: UUID,
        method: str,
        path: str,
        body: dict | None,
        params: dict | None,
        summary: str | None,
        field_labels: dict | None,
        continue_after: bool = False,
    ) -> AiAction:
        spec, groups, canonical = self.validate_proposal(method, path)
        if body is not None and not isinstance(body, dict):
            raise ActionRejected("body must be a JSON object")
        clean = dict(body or {})
        schema = _schema_fields(spec.method, canonical)
        if schema:
            known = {f["name"] for f in schema}
            dropped = [k for k in clean if k not in known]
            for k in dropped:
                clean.pop(k, None)
            # Avtomatik to'ldirish: filial va yo'ldagi id takrorlari
            if "branch_id" in known and not clean.get("branch_id") and ctx.get("branch_id"):
                clean["branch_id"] = str(ctx["branch_id"])
            if "invoice_id" in known and not clean.get("invoice_id") and groups.get("id"):
                clean["invoice_id"] = groups["id"]
        labels = {
            str(k): str(v)[:120] for k, v in (field_labels or {}).items() if isinstance(k, str) and v not in (None, "")
        }
        if "branch_id" in clean and "branch_id" not in labels and ctx.get("branch_name"):
            labels["branch_id"] = ctx["branch_name"]
        action = AiAction(
            hotel_id=ctx["hotel_id"],
            branch_id=ctx.get("branch_id"),
            user_id=ctx["user_id"],
            conversation_id=conversation_id,
            method=spec.method,
            path=canonical,
            params={k: v for k, v in (params or {}).items() if k != "hotel_id"} or None,
            body=clean or None,
            kind=spec.key,
            title=spec.title,
            summary=(summary or "")[:600] or None,
            status="proposed",
            result={"labels": labels, "continue_after": bool(continue_after)},
        )
        self.session.add(action)
        await self.session.flush()
        return action

    # ------------------------------------------------------ ko'rinish --

    @staticmethod
    def effective_status(a: AiAction) -> str:
        if a.status == "proposed" and _aware(a.created_at) and _now() - _aware(a.created_at) > timedelta(hours=PROPOSAL_TTL_HOURS):
            return "expired"
        if a.status in ("executing", "undoing") and _aware(a.updated_at) and (
            _now() - _aware(a.updated_at) > timedelta(minutes=STALE_EXECUTING_MINUTES)
        ):
            return "unknown"
        return a.status

    @classmethod
    def serialize(cls, a: AiAction) -> dict:
        spec = catalog.action_spec(a.kind)
        meta = a.result or {}
        status = cls.effective_status(a)
        return {
            "id": str(a.id),
            "kind": a.kind,
            "title": a.title,
            "summary": a.summary,
            "method": a.method,
            "path": a.path,
            "status": status,
            "risk": spec.risk if spec else "medium",
            "page": spec.page if spec else None,
            "note": spec.note if spec else None,
            "undo_supported": bool(spec and spec.undo),
            "can_undo": status in ("executed", "undo_failed") and bool(a.undo),
            "continue_after": bool(meta.get("continue_after")),
            "error": a.error,
            "result": meta.get("data"),
            "fields": build_fields(a),
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            "undone_at": a.undone_at.isoformat() if a.undone_at else None,
        }

    async def get_own(self, action_id: UUID, current_user: dict) -> AiAction:
        a = await self.session.get(AiAction, action_id)
        if a is None or str(a.user_id) != str(current_user.get("id")) or str(a.hotel_id) != str(
            current_user.get("hotel_id")
        ):
            raise NotFoundException("Amal topilmadi", "AI_ACTION_NOT_FOUND")
        return a

    async def by_ids(self, ids: list[str]) -> dict[str, dict]:
        valid = []
        for i in ids:
            try:
                valid.append(UUID(str(i)))
            except (TypeError, ValueError):
                continue
        if not valid:
            return {}
        rows = (await self.session.execute(select(AiAction).where(AiAction.id.in_(valid)))).scalars().all()
        return {str(a.id): self.serialize(a) for a in rows}

    async def recent_for_user(self, user_id, hotel_id, limit: int = 15) -> list[AiAction]:
        return list(
            (
                await self.session.execute(
                    select(AiAction)
                    .where(AiAction.user_id == user_id, AiAction.hotel_id == hotel_id)
                    .order_by(AiAction.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )

    # ------------------------------------------------------- holatlar --

    async def _transition(self, a: AiAction, from_states: tuple[str, ...], to_state: str) -> None:
        """Atomik holat o'tishi va darhol commit (ikki marta bosishdan himoya)."""
        res = await self.session.execute(
            update(AiAction)
            .where(AiAction.id == a.id, AiAction.status.in_(from_states))
            .values(status=to_state, updated_at=_now())
            .execution_options(synchronize_session=False)
        )
        if (res.rowcount or 0) != 1:
            await self.session.rollback()
            raise ConflictException("Bu amal allaqachon bajarilmoqda yoki yakunlangan", "AI_ACTION_BUSY")
        await self.session.commit()
        await self.session.refresh(a)

    async def reject(self, a: AiAction) -> AiAction:
        if a.status not in ("proposed", "failed"):
            raise ConflictException("Bu amalni endi rad etib bo'lmaydi", "AI_ACTION_LOCKED")
        a.status = "rejected"
        await self.session.flush()
        return a

    # ------------------------------------------------------ bajarish --

    async def confirm(self, a: AiAction, auth: Auth, values: dict | None) -> AiAction:
        status = self.effective_status(a)
        if status == "expired":
            a.status = "expired"
            await self.session.flush()
            raise ValidationException("Taklif muddati o'tgan — yordamchidan qayta so'rang", "AI_ACTION_EXPIRED")
        if a.status not in ("proposed", "failed"):
            raise ConflictException("Bu amal allaqachon bajarilgan yoki yopilgan", "AI_ACTION_LOCKED")

        # Formadagi tahrirlar
        if values:
            fields = {f["name"]: f for f in build_fields(a)}
            body = dict(a.body or {})
            for name, raw in values.items():
                field = fields.get(name)
                if field is None:
                    continue
                coerced = _coerce(field, raw)
                if coerced is None:
                    body.pop(name, None)
                else:
                    body[name] = coerced
            a.body = body or None
            await self.session.flush()
        missing = _missing_required(a)
        if missing:
            raise ValidationException(
                "To'ldirilmagan majburiy maydonlar: " + ", ".join(missing), "AI_ACTION_FIELDS_REQUIRED"
            )

        spec = catalog.action_spec(a.kind)
        if spec is None:
            raise ValidationException("Amal turi endi mavjud emas", "AI_ACTION_UNKNOWN")
        groups = catalog.find_action(a.method, a.path)
        path_id = (groups[1] if groups else {}).get("id")

        await self._transition(a, ("proposed", "failed"), "executing")

        # Qaytarish uchun oldingi holat (bajarishdan OLDIN)
        snapshot = None
        if spec.undo == "restore" and spec.snapshot_path and path_id:
            try:
                snap = await call_api("GET", spec.snapshot_path.format(id=path_id), auth)
                snapshot = snap.data if snap.ok and isinstance(snap.data, dict) else None
            except Exception:
                logger.warning("AI amali uchun snapshot olinmadi (%s)", a.id, exc_info=True)

        try:
            res = await call_api(a.method, a.path, auth, params=a.params, body=a.body)
        except Exception as exc:
            logger.exception("AI amali bajarilmadi (%s)", a.id)
            a.status = "failed"
            a.error = f"Ichki xato: {type(exc).__name__}"
            await self.session.flush()
            return a

        meta = dict(a.result or {})
        if not res.ok:
            a.status = "failed"
            a.error = _error_text(res.status, res.detail)
            meta["http_status"] = res.status
            a.result = meta
            await self.session.flush()
            return a

        meta["data"] = compact_result(res.data)
        a.result = meta
        a.status = "executed"
        a.error = None
        a.executed_at = _now()
        a.undo = self._undo_plan(spec, path_id, a.path, res.data, a.body or {}, snapshot)
        await self.session.flush()
        return a

    @staticmethod
    def _undo_plan(
        spec: catalog.ActionSpec, path_id: str | None, action_path: str, data, body: dict, snapshot: dict | None
    ) -> dict | None:
        if not spec.undo:
            return None
        created = _dig(data, spec.result_key) if isinstance(data, dict) else None
        created = str(created) if created else None
        if spec.undo == "cancel_reservation":
            return {"type": "cancel_reservation", "reservation_id": created} if created else None
        if spec.undo == "delete_created":
            if not created or (isinstance(data, dict) and data.get("existing")):
                return None
            return {"type": "request", "method": "DELETE",
                    "path": spec.undo_path.format(id=path_id, result_id=created)}
        if spec.undo == "post_created":
            if not created:
                return None
            return {"type": "request", "method": "POST",
                    "path": spec.undo_path.format(id=path_id, result_id=created)}
        if spec.undo == "status_cancelled":
            if not created:
                return None
            return {"type": "request", "method": "PATCH",
                    "path": spec.undo_path.format(id=path_id, result_id=created), "body": {"status": "CANCELLED"}}
        if spec.undo == "restore":
            if not snapshot or not body:
                return None
            mapping = dict(spec.snapshot_map)
            restore = {}
            for key in body:
                src = mapping.get(key, key)
                if src in snapshot:
                    restore[key] = snapshot[src]
            if not restore:
                return None
            return {"type": "request", "method": spec.method, "path": action_path, "body": restore}
        return None

    # ------------------------------------------------------- qaytarish --

    async def undo(self, a: AiAction, auth: Auth, current_user: dict) -> AiAction:
        if a.status not in ("executed", "undo_failed") or not a.undo:
            raise ConflictException("Bu amalni qaytarib bo'lmaydi", "AI_ACTION_NOT_UNDOABLE")
        plan = dict(a.undo)
        await self._transition(a, ("executed", "undo_failed"), "undoing")
        try:
            if plan.get("type") == "cancel_reservation":
                ok, error = await self._undo_reservation(a, plan, auth, current_user)
            else:
                res = await call_api(plan["method"], plan["path"], auth, params=plan.get("params"), body=plan.get("body"))
                ok, error = res.ok, (None if res.ok else _error_text(res.status, res.detail))
        except Exception as exc:
            logger.exception("AI amalini qaytarib bo'lmadi (%s)", a.id)
            ok, error = False, f"Ichki xato: {type(exc).__name__}"
        if ok:
            a.status = "undone"
            a.undone_at = _now()
            a.error = None
        else:
            a.status = "undo_failed"
            a.error = "Qaytarib bo'lmadi: " + (error or "noma'lum xato")
        await self.session.flush()
        return a

    async def _undo_reservation(self, a: AiAction, plan: dict, auth: Auth, current_user: dict) -> tuple[bool, str | None]:
        rid = plan.get("reservation_id")
        if not rid:
            return False, "bron topilmadi"
        trusted = await self._trusted_cancel(a, UUID(rid), current_user)
        if trusted is True:
            return True, None
        res = await call_api(
            "POST", f"/reservations/{rid}/cancel", auth,
            body={"reason": "AI yordamchi orqali yaratilgan bron qaytarildi"},
        )
        if res.ok:
            return True, None
        if res.status == 403 and trusted is not None:
            return False, trusted
        return False, _error_text(res.status, res.detail)

    async def _trusted_cancel(self, a: AiAction, reservation_id: UUID, current_user: dict) -> bool | str | None:
        """True — qaytarildi; matn — nega ishonchli yo'l mos emas; None — tekshirilmadi."""
        from app.application.services.audit_service import AuditService
        from app.application.services.reservation_service import ReservationService
        from app.core.database import _get_session_factory
        from app.infrastructure.database.models.reservation import Reservation
        from app.infrastructure.tenant.branch_scope import (
            acquire_branch_write_lock,
            set_branch_scope,
        )

        if str(a.user_id) != str(current_user.get("id")):
            return None
        executed = _aware(a.executed_at)
        if executed is None or _now() - executed > timedelta(minutes=TRUSTED_UNDO_MINUTES):
            return (
                f"bron {TRUSTED_UNDO_MINUTES} daqiqadan oldin yaratilgan — bekor qilish uchun "
                "«bronni bekor qilish» ruxsati kerak"
            )
        factory = _get_session_factory()
        async with factory() as s:
            set_branch_scope(s, a.hotel_id, a.branch_id)
            await acquire_branch_write_lock(s, a.hotel_id, a.branch_id)
            res = await s.get(Reservation, reservation_id)
            if res is None or str(res.hotel_id) != str(a.hotel_id):
                return "bron topilmadi"
            if res.status not in ("PENDING", "CONFIRMED"):
                return "mehmon allaqachon joylashgan yoki bron yopilgan"
            if float(res.paid_amount or 0) > 0.009:
                return "bronga to'lov qabul qilingan — bekor qilish uchun ruxsat kerak"
            await ReservationService(s).cancel_reservation(
                reservation_id, a.hotel_id, a.user_id,
                reason="AI yordamchi: xato yaratilgan bron qaytarildi",
            )
            try:
                async with s.begin_nested():
                    await AuditService(s).log_action(
                        hotel_id=a.hotel_id,
                        user_id=a.user_id,
                        action="AI_UNDO",
                        entity_type="reservation",
                        entity_id=reservation_id,
                        old_values={"status": "CONFIRMED", "ai_action_id": str(a.id)},
                        new_values={"status": "CANCELLED", "via": "ai_assistant_undo"},
                    )
            except Exception:
                logger.exception("AI qaytarish audit yozuvi saqlanmadi (%s)", a.id)
            await s.commit()
        return True


def _error_text(status: int, detail: str) -> str:
    if status == 403:
        return f"Ruxsat yo'q: {detail}"
    if status == 404:
        return f"Topilmadi: {detail}"
    if status in (409, 423):
        return f"Ziddiyat: {detail}"
    if status == 422:
        return f"Ma'lumot noto'g'ri: {detail}"
    return detail or f"HTTP {status}"


__all__ = [
    "ActionRejected", "AiActionService", "FIELD_LABELS", "build_fields", "compact_result",
    "TRUSTED_UNDO_MINUTES",
]
