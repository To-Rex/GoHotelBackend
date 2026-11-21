"""AI asboblari: ta'rif (JSON Schema) va bajaruvchi.

Har asbob natijasi — modelga QISQA matn (token tejash) va ixtiyoriy
ko'rsatiladigan blok (jadval, grafik, karta...). Bloklar xodimga darhol
oqim bilan boradi; model ularning ichini qayta yozmaydi.

Kirish argumentlari tekshiriladi (`validate`): oqimda kelgan argument
kesilgan yoki buzilgan bo'lsa — asbob bajarilmaydi, model xatoni ko'radi.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.application.services.ai import catalog, render, results, safety
from app.application.services.ai.executor import Auth, call_api
from app.application.services.ai.prompt import SITE_PATHS

logger = logging.getLogger(__name__)

MAX_API_CALLS = 24
MAX_ACTIONS_PER_TURN = 6

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}
_FILTERS = {
    "type": "array",
    "description": "Row filters: [{field, op: eq|ne|gt|gte|lt|lte|contains|in|empty|not_empty, value}]",
    "items": {
        "type": "object",
        "properties": {"field": _STR, "op": _STR, "value": {}},
        "required": ["field"],
    },
}
_LABELS = {"type": "object", "description": "Column titles for the user, e.g. {\"total_amount\": \"Jami\"}",
           "additionalProperties": {"type": "string"}}

TOOLS: list[dict] = [
    {
        "name": "api_get",
        "description": (
            "Read hotel data from the GoHotel API (GET) with the user's own permissions. Returns a compact view: "
            "result_id, total_rows, columns, sample_rows (full rows stay on the server for analyze/show_table/"
            "show_chart/export_excel). Paths are relative, e.g. /reservations/ or /finance/summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "API path with real ids, e.g. /reservations/{id}"},
                "params": {"type": "object", "description": "Query parameters", "additionalProperties": True},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_api",
        "description": "Search the API catalog (reads and allowed actions) by keywords in any language. Use when the quick reference has no fitting endpoint.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string", "enum": ["read", "action", "any"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "describe_api",
        "description": "Exact query parameters and body fields of one endpoint. Call before propose_action and before unfamiliar GETs.",
        "input_schema": {
            "type": "object",
            "properties": {"method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH"]}, "path": _STR},
            "required": ["method", "path"],
        },
    },
    {
        "name": "analyze",
        "description": (
            "Aggregate stored rows on the server: filter, group, sum/count/avg/min/max/count_distinct, sort, top-N. "
            "group_by items: \"field\" or \"field:day|week|month|year|weekday|hour\" for dates. Returns a new result_id "
            "with the aggregated rows (use it for show_chart/show_table/export_excel)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "result_id": _STR,
                "group_by": _STR_LIST,
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": _STR,
                            "op": {"type": "string", "enum": ["sum", "count", "avg", "min", "max", "count_distinct"]},
                            "as": _STR,
                        },
                        "required": ["op"],
                    },
                },
                "filters": _FILTERS,
                "sort": _STR,
                "desc": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            "required": ["result_id"],
        },
    },
    {
        "name": "show_table",
        "description": "Show stored rows to the user as a table (up to 200 rows shown, total is displayed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "result_id": _STR, "title": _STR, "columns": _STR_LIST, "labels": _LABELS, "filters": _FILTERS,
                "sort": _STR, "desc": {"type": "boolean"}, "limit": {"type": "integer"},
            },
            "required": ["result_id"],
        },
    },
    {
        "name": "show_chart",
        "description": (
            "Show a chart. Prefer result_id (from api_get/analyze) with x = category/date column and y = numeric "
            "column(s). Only for a few computed values, pass data rows inline instead of result_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["bar", "line", "area", "pie"]},
                "title": _STR,
                "result_id": _STR,
                "x": _STR,
                "y": _STR_LIST,
                "unit": {"type": "string", "description": "e.g. so'm, ta, %"},
                "labels": _LABELS,
                "data": {"type": "array", "items": {"type": "object"}, "description": "Inline rows (max 60)"},
            },
            "required": ["type", "title", "x", "y"],
        },
    },
    {
        "name": "show_stats",
        "description": "Show 2–6 KPI tiles with values taken from tool results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": _STR, "value": {}, "unit": _STR, "hint": _STR,
                            "tone": {"type": "string", "enum": ["good", "bad", "warn", "neutral"]},
                        },
                        "required": ["label", "value"],
                    },
                }
            },
            "required": ["items"],
        },
    },
    {
        "name": "export_excel",
        "description": "Create an Excel (.xlsx) file from stored rows (one sheet per result_id) and give the user a download button.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": _STR,
                "sheets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "result_id": _STR, "name": _STR, "columns": _STR_LIST, "labels": _LABELS, "filters": _FILTERS,
                        },
                        "required": ["result_id"],
                    },
                },
            },
            "required": ["title", "sheets"],
        },
    },
    {
        "name": "navigate",
        "description": "Give the user a button that opens an app page (from the page map).",
        "input_schema": {
            "type": "object",
            "properties": {"path": _STR, "label": _STR, "note": _STR},
            "required": ["path", "label"],
        },
    },
    {
        "name": "suggest_replies",
        "description": "Offer 2–4 short follow-up replies the user can tap.",
        "input_schema": {
            "type": "object",
            "properties": {"options": _STR_LIST},
            "required": ["options"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "Prepare a data-changing action as a confirmation card / form. Nothing runs until the user confirms. "
            "Only actions from the registry are possible (find_api kind=action). Missing required fields are allowed — "
            "the user fills them in. Set continue_after=true when you have a next step that needs this result "
            "(e.g. create guest, then booking)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["POST", "PUT", "PATCH"]},
                "path": {"type": "string", "description": "Path with real ids"},
                "body": {"type": "object", "additionalProperties": True},
                "params": {"type": "object", "additionalProperties": True},
                "summary": {"type": "string", "description": "One-line human summary in the user's language"},
                "field_labels": {
                    "type": "object", "additionalProperties": {"type": "string"},
                    "description": "Readable values for ids/complex fields, e.g. {\"room_id\": \"205 — Lyuks\"}",
                },
                "continue_after": {"type": "boolean"},
            },
            "required": ["method", "path", "summary"],
        },
    },
    {
        "name": "list_my_actions",
        "description": "The user's recent assistant actions (cards) with status and undo availability.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "propose_undo",
        "description": "Show the user an undo card for an executed assistant action (they confirm the undo).",
        "input_schema": {"type": "object", "properties": {"action_id": _STR}, "required": ["action_id"]},
    },
    {
        "name": "create_plan",
        "description": "Create the user's own plan: reminder (notification at due_at), task (to-do) or note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": _STR,
                "kind": {"type": "string", "enum": ["reminder", "task", "note"]},
                "due_at": {"type": "string", "description": "Hotel local time YYYY-MM-DDTHH:MM"},
                "note": _STR,
            },
            "required": ["title", "kind"],
        },
    },
    {
        "name": "list_plans",
        "description": "The user's plans (default: open).",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["open", "done", "cancelled", "recent"]}},
        },
    },
    {
        "name": "update_plan",
        "description": "Complete, cancel, reschedule or rename one of the user's plans.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": _STR,
                "status": {"type": "string", "enum": ["open", "done", "cancelled"]},
                "due_at": _STR, "title": _STR, "note": _STR,
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "flag_incident",
        "description": "Record a dangerous request for the supervisors' safety log (once per request), then answer normally.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(safety.CATEGORIES)},
                "reason": _STR,
            },
            "required": ["category", "reason"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}

#: Ekranda qadam nomi
STEP_LABELS = {
    "api_get": "Ma'lumot o'qilmoqda",
    "find_api": "Bo'lim qidirilmoqda",
    "describe_api": "Maydonlar tekshirilmoqda",
    "analyze": "Hisoblanmoqda",
    "show_table": "Jadval tayyorlanmoqda",
    "show_chart": "Grafik chizilmoqda",
    "show_stats": "Ko'rsatkichlar",
    "export_excel": "Excel fayl tayyorlanmoqda",
    "navigate": "Havola",
    "suggest_replies": "Variantlar",
    "propose_action": "Amal tayyorlanmoqda",
    "list_my_actions": "Amallar ko'rilmoqda",
    "propose_undo": "Qaytarish tayyorlanmoqda",
    "create_plan": "Reja yozilmoqda",
    "list_plans": "Rejalar ko'rilmoqda",
    "update_plan": "Reja yangilanmoqda",
    "flag_incident": "Xavfsizlik qaydi",
}


# --------------------------------------------------------- tekshiruv --


def _type_ok(value: Any, schema: dict) -> bool:
    t = schema.get("type")
    if t is None:
        return True
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    return True


def validate(name: str, args: Any) -> str | None:
    """Argumentlar sxemaga mosmi (yuqori daraja + enum). Xato matni yoki None."""
    spec = next((t for t in TOOLS if t["name"] == name), None)
    if spec is None:
        return f"Unknown tool '{name}'"
    if not isinstance(args, dict):
        return "Arguments must be a JSON object"
    if name == "propose_action" and str(args.get("method") or "").upper() == "DELETE":
        # Sxemada DELETE yo'q; bajaruvchi uni rad etadi VA xavfli urinish
        # sifatida jurnalga yozadi (oddiy sxema xatosi bo'lib yo'qolmasin)
        return None
    schema = spec["input_schema"]
    for req in schema.get("required", []):
        if req not in args or args[req] in (None, ""):
            return f"Missing required argument '{req}'"
    for key, value in args.items():
        sub = schema.get("properties", {}).get(key)
        if sub is None or value is None:
            continue
        if not _type_ok(value, sub):
            return f"Argument '{key}' must be {sub.get('type')}"
        if "enum" in sub and value not in sub["enum"]:
            return f"Argument '{key}' must be one of {sub['enum']}"
    return None


# ------------------------------------------------------------ kontekst --


@dataclass
class TurnContext:
    conversation_id: UUID
    ctx: dict
    auth: Auth
    mask_personal: bool
    pages: list[str] | None
    emit_block: Callable[[dict], Awaitable[None]]
    store: results.ResultStore = field(default_factory=results.ResultStore)
    api_calls: int = 0
    actions: list[str] = field(default_factory=list)
    plans: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    incidents: list[dict] = field(default_factory=list)


def _j(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _err(message: str) -> tuple[str, bool]:
    return _j({"error": message}), True


async def _session():
    from app.core.database import _get_session_factory

    return _get_session_factory()()


# ------------------------------------------------------------ bajaruvchi --


async def run_tool(tc: TurnContext, name: str, args: dict) -> tuple[str, bool]:
    """(modelga matn, xatomi)."""
    try:
        handler = _HANDLERS[name]
    except KeyError:
        return _err(f"Unknown tool '{name}'")
    try:
        return await handler(tc, args)
    except render.AnalyzeError as exc:
        return _err(str(exc))
    except Exception as exc:
        logger.exception("AI asbobi xatosi (%s)", name)
        detail = getattr(exc, "detail", None) or str(exc)
        return _err(f"{type(exc).__name__}: {str(detail)[:300]}")


async def _api_get(tc: TurnContext, args: dict) -> tuple[str, bool]:
    path = catalog.normalize_path(args.get("path") or "")
    if tc.api_calls >= MAX_API_CALLS:
        return _err("API call budget for this answer is used up. Answer with the data you already have.")
    if not catalog.is_readable(path):
        if catalog.READ_DENY.search(path) and any(s in path for s in ("/auth", "/superadmin", "/api-logs", "/permissions")):
            tc.incidents.append(
                {"category": "secrets" if "/auth" in path else "privilege", "source": "server",
                 "reason": f"Yopiq yo'lni o'qishga urinish: GET {path}"}
            )
        return _err(f"GET {path} is not available to the assistant. Use find_api to find an allowed endpoint.")
    ep = catalog.describe("GET", path)
    if ep is None:
        return _err(f"Unknown endpoint GET {path}. Use find_api.")
    if not catalog.read_allowed(path, tc.ctx):
        return _j({"error": "HTTP 403 — outside this user's role (their pages do not include this section); "
                            "tell the user briefly, do not look for workarounds"}), True
    params = {k: v for k, v in (args.get("params") or {}).items() if k != "hotel_id"}
    tc.api_calls += 1
    res = await call_api("GET", catalog.canonical_path("GET", path), tc.auth, params=params)
    if not res.ok:
        hint = ""
        if res.status == 403:
            hint = " — outside this user's permissions; tell the user, do not look for workarounds"
        elif res.status == 422:
            hint = " — check parameters with describe_api"
        return _j({"error": f"HTTP {res.status}{hint}", "detail": res.detail[:400]}), True
    return results.for_model(ep.path, res.data, tc.store, tc.mask_personal), False


async def _find_api(tc: TurnContext, args: dict) -> tuple[str, bool]:
    kind = args.get("kind") or "any"
    found = catalog.search(args.get("query") or "", limit=10, include_writes=kind != "read")
    if kind == "action":
        found = [ep for ep in found if ep.method != "GET"]
    lines = [catalog.endpoint_line(ep) for ep in found]
    if not lines:
        return _j({"endpoints": [], "note": "Nothing found; try other words (English path words work best)."}), False
    return _j({"endpoints": lines}), False


async def _describe_api(tc: TurnContext, args: dict) -> tuple[str, bool]:
    method = (args.get("method") or "GET").upper()
    ep = catalog.describe(method, args.get("path") or "")
    if ep is None:
        return _err("Endpoint not found or not available. Use find_api.")
    line = catalog.endpoint_line(ep, with_body=True)
    line["path_params"] = [p["name"] for p in ep.params if p["in"] == "path"]
    return _j(line), False


def _stored(tc: TurnContext, rid: str | None) -> results.StoredResult:
    item = tc.store.get(rid)
    if item is None:
        raise render.AnalyzeError(
            f"Unknown result_id '{rid}'. Available: {', '.join(tc.store.ids()) or 'none — call api_get first'}"
        )
    return item


async def _analyze(tc: TurnContext, args: dict) -> tuple[str, bool]:
    item = _stored(tc, args.get("result_id"))
    rows, cols = render.analyze(
        item,
        group_by=args.get("group_by"),
        metrics=args.get("metrics"),
        filters=args.get("filters"),
        sort=args.get("sort"),
        desc=args.get("desc", True),
        limit=args.get("limit"),
    )
    new = tc.store.add(f"analyze({item.id})", rows, {})
    new.columns = cols
    preview = rows[:30]
    payload = {"result_id": new.id, "rows": len(rows), "columns": cols, "data": preview}
    if len(rows) > 30:
        payload["note"] = f"{len(rows) - 30} more rows stored; use show_table/show_chart/export_excel with result_id."
    return _j(payload), False


async def _show_table(tc: TurnContext, args: dict) -> tuple[str, bool]:
    item = _stored(tc, args.get("result_id"))
    block = render.table_block(
        item, title=args.get("title"), columns=args.get("columns"), labels=args.get("labels"),
        filters=args.get("filters"), sort=args.get("sort"), desc=bool(args.get("desc")), limit=args.get("limit"),
    )
    await tc.emit_block(block)
    return _j({"shown": True, "rows_shown": len(block["rows"]), "total": block["total"]}), False


async def _show_chart(tc: TurnContext, args: dict) -> tuple[str, bool]:
    y = args.get("y") or []
    if isinstance(y, str):
        y = [y]
    if args.get("result_id"):
        item = _stored(tc, args.get("result_id"))
        rows = item.rows
        missing = [c for c in [args.get("x"), *y] if c not in item.columns]
        if missing:
            return _err(f"Unknown column(s) {missing}. Columns: {', '.join(item.columns[:40])}")
        source = item.id
    else:
        rows = [r for r in (args.get("data") or []) if isinstance(r, dict)][:60]
        if not rows:
            return _err("Pass result_id or inline data rows")
        source = None
    block = render.chart_block(
        rows, chart=args.get("type") or "bar", x=args.get("x"), y=y, title=args.get("title"),
        unit=args.get("unit"), labels=args.get("labels"), source=source,
    )
    await tc.emit_block(block)
    return _j({"shown": True, "points": len(block["data"]), "truncated": block["truncated"]}), False


async def _show_stats(tc: TurnContext, args: dict) -> tuple[str, bool]:
    block = render.stats_block(args.get("items") or [])
    if not block["items"]:
        return _err("items must contain label and value")
    await tc.emit_block(block)
    return _j({"shown": True}), False


async def _export_excel(tc: TurnContext, args: dict) -> tuple[str, bool]:
    from app.application.services.ai.excel import build_workbook, safe_filename
    from app.infrastructure.database.models.ai import AiFile

    sheets = []
    for sh in (args.get("sheets") or [])[:8]:
        if not isinstance(sh, dict):
            continue
        item = _stored(tc, sh.get("result_id"))
        rows = render.apply_filters(item.rows, sh.get("filters"))
        cols = [c for c in (sh.get("columns") or []) if c in item.columns] or item.columns[:40]
        sheets.append({"name": sh.get("name") or args.get("title"), "rows": rows, "columns": cols,
                       "labels": sh.get("labels") or {}})
    if not sheets:
        return _err("sheets must reference stored result_ids")
    content, total = build_workbook(sheets, args.get("title"))
    if len(content) > 8 * 1024 * 1024:
        return _err("The file would be too large; filter the data first")
    name = safe_filename(args.get("title"))
    async with await _session() as s:
        f = AiFile(
            hotel_id=tc.ctx["hotel_id"], user_id=tc.ctx["user_id"], conversation_id=tc.conversation_id,
            name=name, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size=len(content), rows=total, content=content,
        )
        s.add(f)
        await s.commit()
        file_id = str(f.id)
    tc.files.append(file_id)
    await tc.emit_block(render.file_block(file_id, name, len(content), total))
    return _j({"file": name, "rows": total, "shown": True}), False


async def _navigate(tc: TurnContext, args: dict) -> tuple[str, bool]:
    path = "/" + (args.get("path") or "").strip().lstrip("/")
    base = path.split("?", 1)[0].rstrip("/") or "/"
    if base not in SITE_PATHS:
        return _err(f"Unknown page {base}. Use a page from the map.")
    if tc.pages and base not in tc.pages and base not in ("/assistant", "/profile"):
        return _err(f"The page {base} is not available to this user.")
    await tc.emit_block(render.nav_block(path, args.get("label"), args.get("note")))
    return _j({"shown": True}), False


async def _suggest_replies(tc: TurnContext, args: dict) -> tuple[str, bool]:
    block = render.replies_block(args.get("options") or [])
    if block["options"]:
        await tc.emit_block(block)
    return _j({"shown": True}), False


async def _propose_action(tc: TurnContext, args: dict) -> tuple[str, bool]:
    from app.application.services.ai.actions_service import (
        ActionRejected,
        AiActionService,
    )

    if len(tc.actions) >= MAX_ACTIONS_PER_TURN:
        return _err("Too many action cards in one answer; ask the user to confirm the shown ones first.")
    try:
        async with await _session() as s:
            svc = AiActionService(s)
            action = await svc.propose(
                ctx=tc.ctx, conversation_id=tc.conversation_id, method=args.get("method") or "",
                path=args.get("path") or "", body=args.get("body"), params=args.get("params"),
                summary=args.get("summary"), field_labels=args.get("field_labels"),
                continue_after=bool(args.get("continue_after")),
            )
            await s.commit()
            data = svc.serialize(action)
    except ActionRejected as exc:
        if exc.dangerous:
            tc.incidents.append(
                {"category": "data_destruction" if (args.get("method") or "").upper() == "DELETE" else "privilege",
                 "source": "server",
                 "reason": f"Ruxsat etilmagan amal taklifi: {args.get('method')} {args.get('path')}"}
            )
        return _err(str(exc))
    tc.actions.append(data["id"])
    await tc.emit_block({"type": "action", "id": results.new_block_id(), "action": data})
    missing = [f["label"] for f in data["fields"] if f["required"] and f["value"] in (None, "", [])]
    payload = {"card": data["id"][:8], "status": "proposed", "title": data["title"]}
    if missing:
        payload["missing_fields"] = missing
        payload["note"] = "The user can fill missing fields in the card. Tell them briefly what to fill."
    return _j(payload), False


async def _list_my_actions(tc: TurnContext, args: dict) -> tuple[str, bool]:
    from app.application.services.ai.actions_service import AiActionService

    limit = max(1, min(int(args.get("limit") or 10), 30))
    async with await _session() as s:
        svc = AiActionService(s)
        rows = await svc.recent_for_user(tc.ctx["user_id"], tc.ctx["hotel_id"], limit)
        items = [svc.serialize(a) for a in rows]
    out = [
        {"action_id": i["id"], "title": i["title"], "summary": i["summary"], "status": i["status"],
         "can_undo": i["can_undo"], "result": i["result"], "created_at": i["created_at"]}
        for i in items
    ]
    return _j({"actions": out}), False


async def _propose_undo(tc: TurnContext, args: dict) -> tuple[str, bool]:
    from app.application.services.ai.actions_service import AiActionService
    from app.infrastructure.database.models.ai import AiAction

    try:
        aid = UUID(str(args.get("action_id")))
    except (TypeError, ValueError):
        return _err("action_id must be a full id from list_my_actions")
    async with await _session() as s:
        a = await s.get(AiAction, aid)
        if a is None or str(a.user_id) != str(tc.ctx["user_id"]):
            return _err("Action not found")
        data = AiActionService.serialize(a)
    if not data["can_undo"]:
        return _err(f"This action cannot be undone (status {data['status']}, undo plan: {'yes' if data['undo_supported'] else 'none'}).")
    await tc.emit_block({"type": "action", "id": results.new_block_id(), "action": data, "mode": "undo"})
    return _j({"shown": True, "note": "The user presses «Orqaga qaytarish» on the card to undo."}), False


async def _create_plan(tc: TurnContext, args: dict) -> tuple[str, bool]:
    from app.application.services.ai.plans_service import AiPlanService, serialize

    async with await _session() as s:
        plan = await AiPlanService(s).create(
            hotel_id=tc.ctx["hotel_id"], branch_id=tc.ctx.get("branch_id"), user_id=tc.ctx["user_id"],
            title=args.get("title") or "", kind=args.get("kind") or "task", due_at=args.get("due_at"),
            note=args.get("note"), created_via="ai", conversation_id=tc.conversation_id,
        )
        await s.commit()
        data = serialize(plan)
    tc.plans.append(data["id"])
    await tc.emit_block({"type": "plan", "id": results.new_block_id(), "plan": data})
    return _j({"created": True, "plan_id": data["id"], "due_local": data["due_local"]}), False


async def _list_plans(tc: TurnContext, args: dict) -> tuple[str, bool]:
    from app.application.services.ai.plans_service import AiPlanService, serialize

    async with await _session() as s:
        rows = await AiPlanService(s).list(tc.ctx["user_id"], status=args.get("status") or "open", limit=50)
        items = [serialize(p) for p in rows]
    out = [
        {"plan_id": i["id"], "title": i["title"], "kind": i["kind"], "status": i["status"],
         "due_local": i["due_local"], "overdue": i["overdue"], "note": (i["note"] or "")[:120] or None}
        for i in items
    ]
    return _j({"plans": out}), False


async def _update_plan(tc: TurnContext, args: dict) -> tuple[str, bool]:
    from app.application.services.ai.plans_service import AiPlanService, serialize

    try:
        pid = UUID(str(args.get("plan_id")))
    except (TypeError, ValueError):
        return _err("plan_id must be a full id from list_plans")
    data = {k: args[k] for k in ("status", "due_at", "title", "note") if k in args}
    async with await _session() as s:
        svc = AiPlanService(s)
        plan = await svc.get_own(pid, tc.ctx["user_id"])
        await svc.update(plan, data)
        await s.commit()
        out = serialize(plan)
    await tc.emit_block({"type": "plan", "id": results.new_block_id(), "plan": out})
    return _j({"updated": True, "status": out["status"], "due_local": out["due_local"]}), False


async def _flag_incident(tc: TurnContext, args: dict) -> tuple[str, bool]:
    category = safety.clean_category(args.get("category"))
    if not any(i["source"] == "model" for i in tc.incidents):
        tc.incidents.append(
            {"category": category, "source": "model", "reason": str(args.get("reason") or "")[:500]}
        )
    return _j({"recorded": True, "note": "Now answer the user calmly and safely."}), False


_HANDLERS = {
    "api_get": _api_get,
    "find_api": _find_api,
    "describe_api": _describe_api,
    "analyze": _analyze,
    "show_table": _show_table,
    "show_chart": _show_chart,
    "show_stats": _show_stats,
    "export_excel": _export_excel,
    "navigate": _navigate,
    "suggest_replies": _suggest_replies,
    "propose_action": _propose_action,
    "list_my_actions": _list_my_actions,
    "propose_undo": _propose_undo,
    "create_plan": _create_plan,
    "list_plans": _list_plans,
    "update_plan": _update_plan,
    "flag_incident": _flag_incident,
}

assert set(_HANDLERS) == TOOL_NAMES

__all__ = ["STEP_LABELS", "TOOLS", "TOOL_NAMES", "TurnContext", "run_tool", "validate"]
