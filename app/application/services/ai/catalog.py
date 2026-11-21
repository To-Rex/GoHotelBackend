"""AI chaqira oladigan API — o'qish yo'llari va YOZISH registri.

O'QISH (GET): ruxsat etilgan bo'limlar (bron, xona, moliya, xarajat,
do'kon, smena, xo'jalik, hisobot, ...). Kirish, boshqaruv paneli, fayl
yuklab olish, yuz/surat, qurilma va xizmat sozlamalari — yopiq. Har
so'rov xodimning o'z huquqi bilan bajariladi: ruxsati bo'lmasa API o'zi
403 qaytaradi.

YOZISH: faqat shu registrdagi amallar va har biri XODIM TASDIG'I bilan.
O'chirish (DELETE), sozlamalar, foydalanuvchilar, ruxsatlar, ma'lumotni
tozalash umuman registrda yo'q — AI ularni hatto taklif ham qila olmaydi.
Har amalda: sarlavha, xavf darajasi va orqaga qaytarish usuli.

Endpoint tavsifi va maydonlari FastAPI OpenAPI sxemasidan olinadi —
yangi maydon qo'shilsa AI uni o'zi ko'radi.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

API_PREFIX = "/api/v1"

#: O'qish uchun ochiq bo'limlar (yo'l boshi)
READ_PREFIXES = (
    "/reservations", "/rooms", "/room-types", "/floors", "/guests", "/finance", "/expenses",
    "/shop", "/shifts", "/housekeeping", "/tasks", "/problems", "/reports", "/employees",
    "/services", "/hotel-services", "/amenities", "/messages", "/notifications", "/feedback",
    "/reception", "/audit-logs", "/hotels/",
)
#: O'qishda ham yopiq (rasm, fayl, yuz, maxfiy va texnik yo'llar)
READ_DENY = re.compile(
    r"(/auth|/superadmin|/download|/document-images|/image|/thumbnail|/face|/photos|/push|"
    r"register-device|/files|/devices|/apps|/maintenance|/api-logs|/vision|/permissions|"
    r"/scan-document|/scans)",
)


#: O'qish uchun QO'SHIMCHA rol siyosati — frontend sahifa ruxsatlariga mos.
#:
#: Ba'zi API'lar faqat kirish bilan himoyalangan (masalan, moliya xulosasi):
#: sahifani frontend yashiradi. Yordamchi esa ma'lumotni "so'rab olish"ni
#: osonlashtiradi, shuning uchun xodim o'z sahifalarida ko'rmaydigan bo'limni
#: AI ham o'qimaydi. Administrator va tizim ma'muri — cheklovsiz. Birinchi mos
#: kelgan qoida ishlaydi (aniqrog'i yuqorida); bo'sh ro'yxat — faqat
#: administrator. Qolgan yo'llarni API'ning o'zi tekshiradi.
READ_POLICY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/reports/my-summary", ("reservation.create", "reservation.view")),
    ("/reports", ("report.view", "report.generate")),
    ("/finance/invoices", ("finance.view", "finance.payment.create")),
    ("/finance", ("finance.view",)),
    ("/audit-logs", ()),
    ("/employees", ("employee.view", "employee.create", "employee.update", "employee.delete", "employee.manage",
                    "housekeeping.task.assign", "housekeeping.task.create")),
    ("/shifts", ("shift.force_close", "reservation.create", "reservation.view", "finance.payment.create")),
    ("/guests", ("guest.view", "guest.create", "guest.update", "shift.force_close", "reservation.create",
                 "reservation.view")),
    ("/reservations", ("reservation.view", "reservation.read", "reservation.create", "reservation.update",
                       "reservation.check_in", "reservation.check_out", "shift.force_close",
                       "finance.payment.create", "finance.view")),
    ("/shop/profit", ("finance.view",)),
    ("/shop", ("reservation.create", "reservation.view", "service.manage", "service.create", "service.update",
               "hotel_service.manage", "finance.view")),
)


def read_allowed(path: str, current: dict | None) -> bool:
    """Xodim roli bu bo'limni ko'radimi (READ_POLICY)."""
    if not current:
        return False
    if current.get("user_type") in ("ADMIN", "SUPER_ADMIN"):
        return True
    p = normalize_path(path)
    perms = set(current.get("permissions") or [])
    for prefix, codes in READ_POLICY:
        if p == prefix or p.startswith(prefix + "/"):
            return bool(perms & set(codes))
    return True


@dataclass(frozen=True)
class ActionSpec:
    key: str
    method: str
    pattern: str
    title: str
    #: low | medium | high
    risk: str = "medium"
    #: Orqaga qaytarish usuli: None — qaytarib bo'lmaydi
    #:   cancel_reservation — bron bekor qilinadi (o'z amali, qisqa vaqt ichida)
    #:   delete_created — POST natijasidagi id ni DELETE {undo_path}
    #:   post_created — POST {undo_path} (masalan jarimani bekor qilish)
    #:   restore — oldingi qiymatlarga qaytarish (snapshot GET {snapshot_path})
    #:   status_cancelled — yaratilgan vazifa holati CANCELLED
    undo: str | None = None
    undo_path: str | None = None
    snapshot_path: str | None = None
    note: str | None = None
    #: Tana maydoni -> snapshot javobidagi nomi (masalan status -> current_status)
    snapshot_map: tuple[tuple[str, str], ...] = ()
    #: Yaratilgan yozuv id si javobda qayerda ("penalty.id"); standart "id"
    result_key: str = "id"
    #: Bajarilgandan keyin ochiladigan sahifa
    page: str | None = None

    @property
    def regex(self) -> re.Pattern:
        return _compile(self.pattern)


@lru_cache(maxsize=None)
def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern)


_ID = r"(?P<id>[0-9a-fA-F-]{36})"
_SUB = r"(?P<sub>[0-9a-fA-F-]{36})"

ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("reservation.create", "POST", r"^/reservations/?$", "Yangi bron yaratish", "medium",
               undo="cancel_reservation", page="/booking"),
    ActionSpec("reservation.update", "PUT", rf"^/reservations/{_ID}$", "Bronni tahrirlash", "medium",
               undo="restore", snapshot_path="/reservations/{id}", page="/booking"),
    ActionSpec("reservation.check_in", "POST", rf"^/reservations/{_ID}/check-in$", "Mehmonni joylashtirish (check-in)", "medium",
               page="/booking"),
    ActionSpec("reservation.check_out", "POST", rf"^/reservations/{_ID}/check-out$", "Mehmonni chiqarish (check-out)", "high",
               page="/booking"),
    ActionSpec("reservation.extend", "POST", rf"^/reservations/{_ID}/extend$", "Bronni uzaytirish", "medium", page="/booking"),
    ActionSpec("reservation.move", "POST", rf"^/reservations/{_ID}/move-room$", "Mehmonni boshqa xonaga ko'chirish", "medium",
               page="/booking"),
    ActionSpec("reservation.service_add", "POST", rf"^/reservations/{_ID}/services$", "Bronga xizmat qo'shish", "low",
               undo="delete_created", undo_path="/reservations/{id}/services/{result_id}", page="/booking"),
    ActionSpec("reservation.penalty_add", "POST", rf"^/reservations/{_ID}/penalties$", "Jarima yozish", "medium",
               undo="post_created", undo_path="/reservations/{id}/penalties/{result_id}/void",
               result_key="penalty.id", page="/booking"),
    ActionSpec("reservation.cancel", "POST", rf"^/reservations/{_ID}/cancel$", "Bronni bekor qilish", "high", page="/booking"),
    ActionSpec("reservation.no_show", "POST", rf"^/reservations/{_ID}/no-show$", "Bronni \"kelmadi\" deb belgilash", "high",
               page="/booking"),
    ActionSpec("reservation.request_checkout", "POST", rf"^/reservations/{_ID}/request-checkout$", "Chiqishni so'rash", "low",
               page="/booking"),
    ActionSpec("invoice.pay", "POST", rf"^/finance/invoices/{_ID}/pay$", "To'lov qabul qilish", "high", page="/finance"),
    ActionSpec("payment.method", "PATCH", rf"^/finance/payments/{_ID}/method$", "To'lov turini tahrirlash", "high",
               page="/finance"),
    ActionSpec("expense.create", "POST", r"^/expenses/?$", "Xarajat yozish", "medium",
               undo="delete_created", undo_path="/expenses/{result_id}", page="/expenses"),
    ActionSpec("expense.update", "PUT", rf"^/expenses/{_ID}$", "Xarajatni tahrirlash", "medium",
               note="Tahrir xarajat tarixida saqlanadi", page="/expenses"),
    ActionSpec("shop.sale", "POST", r"^/shop/sales/?$", "Do'kon savdosi", "medium",
               undo="delete_created", undo_path="/shop/sales/{result_id}", page="/shop"),
    ActionSpec("shop.sale_pay", "POST", rf"^/shop/sales/{_ID}/pay$", "Do'kon qarzini to'lash", "high", page="/shop"),
    ActionSpec("task.create", "POST", r"^/housekeeping/tasks/?$", "Xo'jalik vazifasi yaratish", "low",
               undo="status_cancelled", undo_path="/housekeeping/tasks/{result_id}/status", page="/housekeeping"),
    ActionSpec("task.assign", "POST", rf"^/housekeeping/tasks/{_ID}/assign$", "Vazifani xodimga biriktirish", "low",
               page="/housekeeping"),
    ActionSpec("task.status", "PATCH", rf"^/housekeeping/tasks/{_ID}/status$", "Vazifa holatini o'zgartirish", "low",
               undo="restore", snapshot_path="/housekeeping/tasks/{id}", page="/housekeeping"),
    ActionSpec("room.status", "PATCH", rf"^/rooms/{_ID}/status$", "Xona holatini o'zgartirish", "low",
               undo="restore", snapshot_path="/rooms/{id}", snapshot_map=(("status", "current_status"),), page="/rooms"),
    ActionSpec("problem.status", "PATCH", rf"^/problems/{_ID}/status$", "Muammo holatini o'zgartirish", "low",
               undo="restore", snapshot_path="/problems/{id}", page="/housekeeping"),
    ActionSpec("guest.create", "POST", r"^/guests/?$", "Mehmon qo'shish", "low",
               undo="delete_created", undo_path="/guests/{result_id}", page="/guests",
               note="Mavjud mehmon topilsa yangisi yaratilmaydi — u holda qaytarish ham yo'q"),
    ActionSpec("guest.update", "PUT", rf"^/guests/{_ID}$", "Mehmon ma'lumotlarini tahrirlash", "medium",
               undo="restore", snapshot_path="/guests/{id}", page="/guests"),
    ActionSpec("feedback.create", "POST", r"^/feedback/?$", "Murojaat (taklif/shikoyat) yozish", "low", page="/feedback"),
    ActionSpec("feedback.status", "PATCH", rf"^/feedback/{_ID}/status$", "Murojaat holatini o'zgartirish", "low",
               undo="restore", snapshot_path="/feedback/{id}", page="/feedback"),
    ActionSpec("message.create", "POST", r"^/messages/?$", "Xodimlarga xabar yuborish", "low", page="/messages"),
    ActionSpec("message.done", "POST", rf"^/messages/{_ID}/done$", "Xabarni bajarildi deb belgilash", "low", page="/messages"),
    ActionSpec("notification.send", "POST", r"^/notifications/send$", "Xodimga bildirishnoma yuborish", "low"),
)


def normalize_path(path: str) -> str:
    """"/api/v1/rooms/" yoki "rooms" — "/rooms/" ko'rinishiga."""
    p = (path or "").strip()
    p = p.split("?", 1)[0]
    if p.startswith("http://") or p.startswith("https://"):
        p = "/" + p.split("/", 3)[-1]
    if p.startswith(API_PREFIX):
        p = p[len(API_PREFIX):]
    if not p.startswith("/"):
        p = "/" + p
    return re.sub(r"/{2,}", "/", p)


def is_readable(path: str) -> bool:
    p = normalize_path(path)
    if ".." in p or READ_DENY.search(p):
        return False
    return any(p == pre.rstrip("/") or p.startswith(pre if pre.endswith("/") else pre) for pre in READ_PREFIXES)


def find_action(method: str, path: str) -> tuple[ActionSpec, dict] | None:
    p = normalize_path(path)
    m = (method or "").upper()
    for spec in ACTIONS:
        if spec.method == m:
            match = spec.regex.match(p)
            if match:
                return spec, match.groupdict()
    return None


# ------------------------------------------------------ OpenAPI katalog --


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    summary: str
    params: tuple[dict, ...]
    body: dict | None
    tags: tuple[str, ...]


def _first_text(op: dict) -> str:
    text = (op.get("description") or op.get("summary") or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:260]


def _resolve(schema: dict | None, comps: dict, depth: int = 0) -> dict:
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _resolve(comps.get(name, {}), comps, depth)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [_resolve(s, comps, depth) for s in schema[key]]
            options = [o for o in options if o.get("type") != "null"]
            if len(options) == 1:
                merged = dict(options[0])
                merged["nullable"] = True
                return merged
            if options:
                merged = dict(options[0])
                merged["nullable"] = True
                return merged
    return schema


def _type_of(schema: dict, comps: dict, depth: int) -> str:
    s = _resolve(schema, comps, depth)
    t = s.get("type")
    if "enum" in s:
        return "enum(" + ",".join(str(v) for v in s["enum"][:12]) + ")"
    if t == "array":
        item = _resolve(s.get("items", {}), comps, depth + 1)
        if item.get("type") == "object" and depth < 2:
            return "array<" + _fields_line(item, comps, depth + 1) + ">"
        return "array<" + _type_of(item, comps, depth + 1) + ">"
    if t == "object" and depth < 2 and s.get("properties"):
        return "{" + _fields_line(s, comps, depth + 1) + "}"
    fmt = s.get("format")
    return f"{t or 'any'}" + (f":{fmt}" if fmt else "")


def _fields_line(schema: dict, comps: dict, depth: int) -> str:
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    parts = []
    for name, sub in list(props.items())[:30]:
        parts.append(f"{name}{'*' if name in required else ''}:{_type_of(sub, comps, depth)}")
    return ", ".join(parts)


def _body_schema(op: dict, comps: dict) -> dict | None:
    content = (op.get("requestBody") or {}).get("content") or {}
    js = content.get("application/json")
    if not js:
        return None
    schema = _resolve(js.get("schema"), comps)
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields = []
    for name, sub in list(props.items())[:40]:
        resolved = _resolve(sub, comps)
        fields.append(
            {
                "name": name,
                "type": _type_of(sub, comps, 0),
                "required": name in required,
                "description": (resolved.get("description") or "")[:140] or None,
                "default": resolved.get("default"),
            }
        )
    return {"fields": fields}


@lru_cache(maxsize=1)
def _catalog() -> tuple[Endpoint, ...]:
    from app.main import app  # aylanma importdan qochish uchun shu yerda

    spec = app.openapi()
    comps = (spec.get("components") or {}).get("schemas") or {}
    out = []
    for raw_path, ops in spec.get("paths", {}).items():
        path = normalize_path(raw_path)
        for method, op in ops.items():
            m = method.upper()
            if m not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            readable = m == "GET" and is_readable(path)
            if not readable and not _template_writable(m, path):
                continue
            params = []
            for prm in op.get("parameters") or []:
                if prm.get("name") == "hotel_id":
                    continue
                sch = _resolve(prm.get("schema"), comps)
                params.append(
                    {
                        "name": prm.get("name"),
                        "in": prm.get("in"),
                        "type": _type_of(prm.get("schema") or {}, comps, 0),
                        "required": bool(prm.get("required")),
                        "default": sch.get("default"),
                    }
                )
            out.append(
                Endpoint(
                    m, path, _first_text(op), tuple(params),
                    _body_schema(op, comps) if m != "GET" else None, tuple(op.get("tags") or ()),
                )
            )
    return tuple(out)


def _template_writable(method: str, path: str) -> bool:
    """Shablon yo'l ("/reservations/{reservation_id}/cancel") registrga mosmi."""
    sample = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
    return method != "GET" and find_action(method, sample) is not None


def catalog() -> tuple[Endpoint, ...]:
    return _catalog()


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9а-яёўқғҳʻʼ']+", (text or "").lower()) if len(t) > 1]


#: O'zbekcha/ruscha so'zlar — yo'l nomlari inglizcha bo'lgani uchun
SYNONYMS = {
    "bron": "reservations", "bronlar": "reservations", "бронь": "reservations", "booking": "reservations",
    "xona": "rooms", "xonalar": "rooms", "номер": "rooms", "room": "rooms",
    "mehmon": "guests", "mehmonlar": "guests", "гость": "guests",
    "tushum": "finance summary", "daromad": "finance summary", "moliya": "finance", "выручка": "finance",
    "to'lov": "payments", "tolov": "payments", "платеж": "payments",
    "faktura": "invoices", "hisob-faktura": "invoices", "счет": "invoices",
    "qarz": "debtors", "qarzdor": "debtors", "долг": "debtors",
    "xarajat": "expenses", "расход": "expenses",
    "do'kon": "shop", "dokon": "shop", "magazin": "shop", "магазин": "shop", "foyda": "profit",
    "kassa": "shifts cash", "smena": "shifts", "смена": "shifts",
    "tozalash": "housekeeping tasks", "vazifa": "tasks housekeeping", "farrosh": "housekeeping",
    "xodim": "employees", "xodimlar": "employees", "сотрудник": "employees",
    "hisobot": "reports", "отчет": "reports", "xizmat": "services", "jarima": "penalties",
    "shikoyat": "feedback", "taklif": "feedback", "xabar": "messages", "qavat": "floors",
    "bo'sh": "available", "bosh": "available", "свобод": "available", "kalendar": "calendar",
}


#: Yozuvchi amalni bildiruvchi fe'llar — POST/PUT/PATCH yo'llari ustun turadi
WRITE_VERBS = (
    "yarat", "qo'sh", "qosh", "yoz", "kirit", "o'zgartir", "ozgartir", "tahrir", "joylash", "chiqar",
    "uzaytir", "ko'chir", "bekor", "to'la", "tola", "qabul", "sot", "biriktir", "yubor", "belgila",
    "create", "add", "make", "update", "change", "cancel", "pay", "send", "assign",
    "созда", "добав", "измени", "отмен", "оплат", "отправ", "назнач",
)


#: Fe'l -> inglizcha yo'l/tavsif so'zlari ("bron yarat" -> create)
VERB_MAP = {
    "yarat": "create", "qo'sh": "add create", "qosh": "add create", "kirit": "create add", "yoz": "create add",
    "bekor": "cancel", "to'la": "pay", "tola": "pay", "qabul": "pay", "uzaytir": "extend", "ko'chir": "move",
    "joylash": "check-in", "chiqar": "check-out", "tahrir": "update", "o'zgartir": "update status",
    "ozgartir": "update status", "biriktir": "assign", "yubor": "send", "sot": "sales", "belgila": "status",
    "созда": "create", "добав": "add create", "отмен": "cancel", "оплат": "pay", "измени": "update",
}


def search(query: str, limit: int = 8, include_writes: bool = True) -> list[Endpoint]:
    words = _tokens(query)
    expanded = list(words)
    for w in words:
        for key, value in SYNONYMS.items():
            if w.startswith(key) or key.startswith(w) and len(w) >= 4:
                expanded.extend(value.split())
        for verb, value in VERB_MAP.items():
            if w.startswith(verb):
                expanded.extend(value.split())
    wants_write = any(w.startswith(v) for w in words for v in WRITE_VERBS)
    wants_create = wants_write and any(w in ("create", "add") for w in expanded)
    scored = []
    for ep in catalog():
        if ep.method != "GET" and not include_writes:
            continue
        hay_path = ep.path.lower()
        hay = hay_path + " " + ep.summary.lower() + " " + " ".join(ep.tags).lower()
        score = 0
        for w in expanded:
            if w in hay_path:
                score += 3
            elif w in hay:
                score += 1
        if score:
            # So'rov yozuvchi amal haqida bo'lsa — amallar, aks holda GET birinchi
            if wants_write:
                score += 2.5 if ep.method != "GET" else 0
                # "yarat/qo'sh" — to'plamga POST (/reservations/), ichki amal emas
                if wants_create and ep.method == "POST" and "{" not in ep.path:
                    score += 2
            else:
                score += 0.5 if ep.method == "GET" else 0
            score -= hay_path.count("{") * 0.2
            scored.append((score, ep))
    scored.sort(key=lambda x: (-x[0], x[1].path))
    return [ep for _s, ep in scored[:limit]]


def describe(method: str, path: str) -> Endpoint | None:
    m = (method or "GET").upper()
    p = normalize_path(path)
    # Aniq shablon yoki haqiqiy id bilan berilgan yo'l; oxirgi "/" farqi
    # ahamiyatsiz ("/expenses" va "/expenses/" — bitta endpoint)
    variants = (p, p.rstrip("/") or "/", p if p.endswith("/") else p + "/")
    for candidate in variants:
        for ep in catalog():
            if ep.method != m:
                continue
            if ep.path == candidate or _path_regex(ep.path).match(candidate):
                return ep
    return None


def canonical_path(method: str, path: str) -> str:
    """So'rov yo'li endpoint shabloniga mos oxirgi "/" bilan (aks holda
    FastAPI 307 qaytaradi va POST tanasi yo'qoladi)."""
    p = normalize_path(path)
    ep = describe(method, p)
    if ep is None:
        return p
    if ep.path.endswith("/") and not p.endswith("/"):
        return p + "/"
    if not ep.path.endswith("/") and p.endswith("/") and p != "/":
        return p.rstrip("/")
    return p


@lru_cache(maxsize=None)
def _path_regex(template: str) -> re.Pattern:
    """"/rooms/{room_id}/status" -> ^/rooms/[^/]+/status$"""
    parts = [
        "[^/]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg)
        for seg in template.split("/")
    ]
    return re.compile("^" + "/".join(parts) + "$")


def endpoint_line(ep: Endpoint, with_body: bool = False) -> dict:
    out = {"method": ep.method, "path": ep.path, "about": ep.summary}
    q = [
        f"{p['name']}{'*' if p['required'] else ''}:{p['type']}" + (f"={p['default']}" if p.get("default") not in (None, "") else "")
        for p in ep.params
        if p["in"] == "query"
    ]
    if q:
        out["query"] = ", ".join(q)
    if ep.method != "GET":
        sample = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", ep.path)
        found = find_action(ep.method, sample)
        if found:
            spec = found[0]
            out["action"] = {"title": spec.title, "risk": spec.risk, "undo": bool(spec.undo)}
        if with_body and ep.body:
            out["body"] = ep.body["fields"]
    return out


#: Tizim yo'riqnomasidagi qisqa ro'yxat — ko'p so'raladigan hisobot yo'llari
CHEATSHEET_PATHS = (
    ("GET", "/finance/summary"), ("GET", "/finance/daily"), ("GET", "/finance/by-staff"),
    ("GET", "/finance/payments/stats"), ("GET", "/finance/invoices/stats"), ("GET", "/finance/debtors"),
    ("GET", "/reservations/"), ("GET", "/reservations/calendar"), ("GET", "/rooms/available"),
    ("GET", "/rooms/"), ("GET", "/expenses/"), ("GET", "/shop/profit"), ("GET", "/shop/products"),
    ("GET", "/shifts/cash-overview"), ("GET", "/housekeeping/tasks/open"), ("GET", "/reports/my-summary"),
    ("GET", "/guests/"), ("GET", "/employees/"),
)


def cheatsheet(current: dict | None = None) -> list[dict]:
    """Qisqa ro'yxat; `current` berilsa — faqat xodim roli ko'radigan yo'llar."""
    out = []
    for method, path in CHEATSHEET_PATHS:
        if current is not None and not read_allowed(path, current):
            continue
        ep = describe(method, path)
        if ep is not None:
            line = endpoint_line(ep)
            line["about"] = line["about"][:140]
            out.append(line)
    return out


def action_spec(key: str) -> ActionSpec | None:
    for spec in ACTIONS:
        if spec.key == key:
            return spec
    return None


__all__ = [
    "ACTIONS", "READ_POLICY", "ActionSpec", "Endpoint", "action_spec", "canonical_path", "catalog", "cheatsheet",
    "describe", "endpoint_line", "find_action", "is_readable", "normalize_path", "read_allowed", "search",
]
