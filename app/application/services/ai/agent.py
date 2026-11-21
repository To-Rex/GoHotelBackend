"""Bitta suhbat navbati: model ↔ asboblar aylanishi (SSE oqim bilan).

Oqim hodisalari (frontend: src/features/assistant/api/stream.ts):
  start  {assistant: {id}}            — javob boshlandi
  delta  {text}                       — matn bo'lagi
  block  {block}                      — jadval/grafik/karta... (tartibi bilan)
  step   {id, tool, label, status}    — "nima qilinyapti" qatori
  done   {message, usage, limits}     — yakun (saqlangan xabar)
  error  {code, message}              — xato (xabar baribir saqlanadi)

Navbat alohida asyncio vazifasida ishlaydi: xodim sahifani yopsa ham javob
oxirigacha tayyorlanib saqlanadi (tarixda ko'rinadi), taklif qilingan
kartalar yo'qolmaydi. Bitta suhbatda bir vaqtda bitta navbat.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import select

from app.application.services.ai import prompt, safety, tools
from app.application.services.ai.config_service import AiConfigService
from app.application.services.ai.executor import Auth
from app.application.services.ai.providers import (
    AiProviderError,
    ProviderSettings,
    ToolResult,
    Usage,
    make_adapter,
)
from app.core.database import _get_session_factory
from app.infrastructure.database.models.ai import (
    AiAction,
    AiConversation,
    AiIncident,
    AiMessage,
)

logger = logging.getLogger(__name__)

MAX_STEPS = 10
#: Bir vaqtda ishlayotgan navbatlar (suhbat id -> vazifa)
_RUNNING: dict[str, asyncio.Task] = {}


def is_running(conversation_id) -> bool:
    task = _RUNNING.get(str(conversation_id))
    return task is not None and not task.done()


@dataclass
class TurnInput:
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    text: str
    ctx: dict
    auth: Auth
    settings: ProviderSettings
    mask_personal: bool
    price_input: float | None
    price_output: float | None
    page: str | None = None
    pages: list[str] | None = None
    remaining: int | None = None
    #: Faqat xodim yozgan matn (xavfsizlik tekshiruvi uchun); "davom et" xabarida bo'sh
    typed_text: str = ""


@dataclass
class TurnState:
    segments: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    error: str | None = None
    error_code: str | None = None


class Emitter:
    """Hodisalar navbati: SSE generatori o'qiydi; ulanish uzilsa — yig'ilaveradi."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: str, data: dict) -> None:
        await self.queue.put((event, data))

    async def close(self) -> None:
        await self.queue.put(None)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


async def sse_stream(emitter: Emitter, heartbeat: float = 15.0) -> AsyncIterator[bytes]:
    yield b": ok\n\n"
    while True:
        try:
            item = await asyncio.wait_for(emitter.queue.get(), timeout=heartbeat)
        except asyncio.TimeoutError:
            yield b": ping\n\n"
            continue
        if item is None:
            break
        event, data = item
        yield _sse(event, data)


def start_turn(inp: TurnInput) -> Emitter:
    emitter = Emitter()
    runner = TurnRunner(inp, emitter)
    task = asyncio.create_task(runner.run())
    key = str(inp.conversation_id)
    _RUNNING[key] = task

    def _done(t: asyncio.Task) -> None:
        if _RUNNING.get(key) is t:
            _RUNNING.pop(key, None)

    task.add_done_callback(_done)
    return emitter


class TurnRunner:
    def __init__(self, inp: TurnInput, emitter: Emitter):
        self.inp = inp
        self.emitter = emitter
        self.state = TurnState()
        self.assistant_id = uuid.uuid4()
        self._new_step = False

    # ------------------------------------------------------- yordamchi --

    async def _text(self, chunk: str) -> None:
        if not chunk:
            return
        segs = self.state.segments
        if self._new_step:
            # Yangi qadam matni oldingisiga yopishmasin ("topaman.Kartani")
            self._new_step = False
            if segs and segs[-1].get("type") == "text" and not segs[-1]["text"].endswith(("\n", " ")):
                chunk = "\n\n" + chunk.lstrip()
        if segs and segs[-1].get("type") == "text":
            segs[-1]["text"] += chunk
        else:
            segs.append({"type": "text", "text": chunk})
        await self.emitter.emit("delta", {"text": chunk})

    async def _block(self, block: dict) -> None:
        self.state.segments.append(block)
        await self.emitter.emit("block", {"block": block})

    def _full_text(self) -> str:
        return "".join(s["text"] for s in self.state.segments if s.get("type") == "text").strip()

    async def _history(self) -> list[tuple[str, str]]:
        factory = _get_session_factory()
        async with factory() as s:
            rows = list(
                (
                    await s.execute(
                        select(AiMessage)
                        .where(
                            AiMessage.conversation_id == self.inp.conversation_id,
                            AiMessage.id != self.inp.user_message_id,
                        )
                        .order_by(AiMessage.created_at.desc())
                        .limit(prompt.HISTORY_MESSAGES)
                    )
                ).scalars()
            )
            rows.reverse()
            action_ids = []
            for m in rows:
                for b in m.blocks or []:
                    if b.get("type") == "action":
                        action_ids.append(str((b.get("action") or {}).get("id")))
                    elif b.get("type") == "event" and b.get("action_id"):
                        action_ids.append(str(b.get("action_id")))
            from app.application.services.ai.actions_service import AiActionService

            current = await AiActionService(s).by_ids(action_ids)
        return prompt.history_items(rows, current)

    # ------------------------------------------------------------ asosiy --

    async def run(self) -> None:
        started = time.monotonic()
        inp = self.inp
        await self.emitter.emit("start", {"assistant": {"id": str(self.assistant_id)}})
        heur = safety.scan(inp.typed_text)
        tc = tools.TurnContext(
            conversation_id=inp.conversation_id, ctx=inp.ctx, auth=inp.auth,
            mask_personal=inp.mask_personal, pages=inp.pages, emit_block=self._block,
        )
        try:
            adapter = make_adapter(inp.settings)
            history = await self._history()
            messages: list = []
            for role, text in history:
                messages.append(adapter.user_message(text) if role == "user" else adapter.assistant_text(text))
            user_text = inp.text
            if heur:
                cats = ", ".join(sorted({f.category for f in heur}))
                user_text += (
                    f"\n\n[Safety note from the system: this message matches risk patterns ({cats}). "
                    "Apply the safety rules: stay calm, never delete or bypass rules, call flag_incident once.]"
                )
            if messages and messages[-1]["role"] == "user":
                # Oldingi navbat javobsiz qolgan (xato) — bitta user xabariga birlashtiramiz
                prev = messages.pop()
                user_text = f"{prev['content']}\n\n{user_text}"
            messages.append(adapter.user_message(user_text))
            limit_note = None
            if inp.remaining is not None:
                limit_note = f"Token budget left this month: {inp.remaining}. Be economical."
            system = [prompt.static_prompt(inp.pages, inp.ctx), prompt.dynamic_prompt({**inp.ctx, "page": inp.page, "limit_note": limit_note})]

            for step_no in range(MAX_STEPS):
                last_round = step_no == MAX_STEPS - 1
                self._new_step = step_no > 0
                result = await adapter.step(system, messages, tools.TOOLS, self._text)
                self.state.usage.add(result.usage)
                self.state.model = result.model or self.state.model
                if result.stop == "refusal" and not self._full_text():
                    await self._text("Kechirasiz, bu so'rovga javob bera olmayman.")
                if result.stop != "tool" or not result.tool_calls:
                    if result.stop == "length":
                        await self._text("\n\n_(Javob juda uzun bo'lgani uchun qisqartirildi.)_")
                    break
                if inp.remaining is not None and self.state.usage.input + self.state.usage.output >= inp.remaining:
                    await self._text("\n\n_Oylik token limiti tugadi — javob shu yerda to'xtatildi._")
                    break
                messages.append(result.assistant_message)
                outputs: list[ToolResult] = []
                for call in result.tool_calls:
                    step_id = call.id or uuid.uuid4().hex[:8]
                    label = tools.STEP_LABELS.get(call.name, call.name)
                    detail = None
                    if call.name == "api_get" and isinstance(call.arguments, dict):
                        detail = str(call.arguments.get("path") or "")[:80]
                    await self.emitter.emit("step", {"id": step_id, "tool": call.name, "label": label,
                                                     "detail": detail, "status": "running"})
                    problem = call.error or tools.validate(call.name, call.arguments)
                    if problem:
                        content, is_error = json.dumps({"error": f"Invalid tool input: {problem}"}), True
                    elif last_round:
                        content, is_error = json.dumps({"error": "Tool budget used up — answer now."}), True
                    else:
                        content, is_error = await tools.run_tool(tc, call.name, call.arguments or {})
                    outputs.append(ToolResult(call, content, is_error))
                    step = {"id": step_id, "tool": call.name, "label": label, "detail": detail,
                            "status": "error" if is_error else "done"}
                    self.state.steps.append(step)
                    await self.emitter.emit("step", step)
                messages.extend(adapter.tool_results(outputs))
                if step_no == MAX_STEPS - 2:
                    note = "Tool budget almost used up: give the final answer now without more tools."
                    if _is_anthropic(adapter):
                        _append_text(messages, note)
                    else:
                        messages.append(adapter.user_message(note))
            if not self._full_text() and not any(s.get("type") != "text" for s in self.state.segments):
                await self._text("Javob tayyor bo'lmadi — savolni boshqacha so'rab ko'ring.")
        except AiProviderError as exc:
            self.state.error = exc.user_message
            self.state.error_code = exc.code
            logger.warning("AI provider xatosi (%s): %s", exc.code, exc.detail)
            await self.emitter.emit("error", {"code": exc.code, "message": exc.user_message})
        except asyncio.CancelledError:
            self.state.error = "To'xtatildi"
            raise
        except Exception:
            logger.exception("AI navbati xatosi")
            self.state.error = "Yordamchida ichki xato yuz berdi — qayta urinib ko'ring."
            self.state.error_code = "internal"
            await self.emitter.emit("error", {"code": "internal", "message": self.state.error})
        finally:
            try:
                saved = await self._save(tc, heur, time.monotonic() - started)
                await self.emitter.emit("done", saved)
            except Exception:
                logger.exception("AI javobi saqlanmadi")
                await self.emitter.emit("error", {"code": "save", "message": "Javob saqlanmadi"})
            await self.emitter.close()

    # ------------------------------------------------------------ saqlash --

    async def _save(self, tc: tools.TurnContext, heur: list[safety.Flag], seconds: float) -> dict:
        inp = self.inp
        st = self.state
        cost = None
        if inp.price_input is not None or inp.price_output is not None:
            cost = round(
                st.usage.input / 1_000_000 * float(inp.price_input or 0)
                + st.usage.output / 1_000_000 * float(inp.price_output or 0),
                6,
            )
        text = self._full_text()
        factory = _get_session_factory()
        async with factory() as s:
            msg = AiMessage(
                id=self.assistant_id,
                conversation_id=inp.conversation_id,
                hotel_id=inp.ctx["hotel_id"],
                user_id=inp.ctx["user_id"],
                role="assistant",
                text=text,
                blocks=st.segments or None,
                steps=(st.steps or None),
                provider=inp.settings.provider,
                model=st.model or inp.settings.model,
                input_tokens=st.usage.input,
                output_tokens=st.usage.output,
                cached_tokens=st.usage.cached,
                cost_usd=cost,
                error=st.error,
                created_at=datetime.now(timezone.utc),
            )
            s.add(msg)
            conv = await s.get(AiConversation, inp.conversation_id)
            if conv is not None:
                conv.updated_at = datetime.now(timezone.utc)
            for aid in tc.actions:
                a = await s.get(AiAction, uuid.UUID(aid))
                if a is not None:
                    a.message_id = self.assistant_id
            seen: set[str] = set()
            question = inp.typed_text or inp.text
            for flag in heur:
                seen.add(flag.category)
                s.add(AiIncident(
                    hotel_id=inp.ctx["hotel_id"], user_id=inp.ctx["user_id"], conversation_id=inp.conversation_id,
                    message_id=self.assistant_id, category=flag.category, source="heuristic",
                    reason=flag.reason, question=question[:4000], answer=text[:4000] or st.error,
                ))
            for inc in tc.incidents:
                if inc["source"] == "model" and inc["category"] in seen:
                    continue
                seen.add(inc["category"])
                s.add(AiIncident(
                    hotel_id=inp.ctx["hotel_id"], user_id=inp.ctx["user_id"], conversation_id=inp.conversation_id,
                    message_id=self.assistant_id, category=safety.clean_category(inc["category"]),
                    source=inc["source"], reason=(inc.get("reason") or "")[:1000], question=question[:4000],
                    answer=text[:4000] or st.error,
                ))
            await s.commit()
            limits = await AiConfigService(s).limits_for(inp.ctx["hotel_id"], inp.ctx["user_id"])
            payload = serialize_message(msg)
        return {
            "message": payload,
            "usage": {"input": st.usage.input, "output": st.usage.output, "cached": st.usage.cached,
                      "cost_usd": cost, "seconds": round(seconds, 1)},
            "limits": limits,
        }


def _is_anthropic(adapter) -> bool:
    return adapter.__class__.__name__ == "AnthropicAdapter"


def _append_text(messages: list, note: str) -> None:
    """Claude: matn eslatmasi tool_result'lar bilan BIR user xabarida (keyin)."""
    last = messages[-1]
    if isinstance(last.get("content"), list):
        last["content"] = [*last["content"], {"type": "text", "text": note}]


def serialize_message(m: AiMessage) -> dict:
    return {
        "id": str(m.id),
        "conversation_id": str(m.conversation_id),
        "role": m.role,
        "text": m.text or "",
        "blocks": m.blocks or [],
        "steps": m.steps or [],
        "provider": m.provider,
        "model": m.model,
        "input_tokens": m.input_tokens or 0,
        "output_tokens": m.output_tokens or 0,
        "cost_usd": m.cost_usd,
        "error": m.error,
        "created_at": (m.created_at or datetime.now(timezone.utc)).isoformat(),
    }


__all__ = ["Emitter", "MAX_STEPS", "TurnInput", "is_running", "serialize_message", "sse_stream", "start_turn"]
