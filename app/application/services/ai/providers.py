"""AI providerlari — bitta interfeys.

* ChatGPT (OpenAI), DeepSeek, Deploy.com va "Boshqa" — OpenAI bilan mos
  Chat Completions API (`openai` SDK, `base_url` bilan).
* Claude (Anthropic) — rasmiy `anthropic` SDK (Messages API).

Har adapter bitta "qadam" bajaradi: model javobi oqim bo'lib keladi
(matn bo'laklari `on_text` ga), oxirida — matn, asbob chaqiruvlari va
token sarfi. Agent (agent.py) qadamlarni asboblar bilan aylantiradi.

Xatolar `AiProviderError` ga o'giriladi: xodimga tushunarli matn
(kalit noto'g'ri, limit, model topilmadi, tarmoq...).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import anthropic
import openai

TextCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    #: openai | anthropic — qaysi adapter
    kind: str
    base_url: str | None
    default_model: str | None
    models: tuple[str, ...] = ()
    #: Manzilni sozlovchi kiritadimi (Boshqa)
    needs_base_url: bool = False


PROVIDERS: dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        "openai", "ChatGPT (OpenAI)", "openai", None, "gpt-5-mini",
        ("gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"),
    ),
    "anthropic": ProviderInfo(
        "anthropic", "Claude (Anthropic)", "anthropic", None, "claude-opus-5-5",
        ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"),
    ),
    "deepseek": ProviderInfo(
        "deepseek", "DeepSeek", "openai", "https://api.deepseek.com", "deepseek-chat",
        ("deepseek-chat", "deepseek-reasoner"),
    ),
    "deploy": ProviderInfo(
        "deploy", "Deploy.com", "openai", "https://api.deploy.com/api/v1",
        "deepseek-ai/deepseek-v4.1-flash-1017",
        ("deepseek-ai/deepseek-v4.1-flash-1017", "zai-org/glm-5.3-flash"),
    ),
    "custom": ProviderInfo(
        "custom", "Boshqa (OpenAI bilan mos API)", "openai", None, None, (), needs_base_url=True,
    ),
}

#: Claude modellari: effort parametri va server-side fallbacks qo'llanadiganlari
_CLAUDE_EFFORT_MODELS = (
    "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5",
    "claude-fable-5-2", "claude-fable-5-1", "claude-fable-5", "claude-opus-4-8", "claude-opus-4-7",
)
_CLAUDE_FALLBACK_MODELS = (
    "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1",
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AiProviderError(Exception):
    """Provider xatosi — `code` bo'yicha xodimga tushunarli matn."""

    MESSAGES = {
        "auth": "AI xizmati kalitni qabul qilmadi — sozlovchi kalitni tekshirsin.",
        "rate": "AI xizmati hozir band yoki hisob limiti tugagan — birozdan keyin urinib ko'ring.",
        "model": "Sozlamadagi model topilmadi yoki bu kalitga ochiq emas — sozlovchi modelni tekshirsin.",
        "network": "AI xizmatiga ulanib bo'lmadi — internet yoki xizmat holatini tekshiring.",
        "bad_request": "AI xizmati so'rovni qabul qilmadi.",
        "provider": "AI xizmatida xatolik yuz berdi — birozdan keyin urinib ko'ring.",
        "config": "AI yordamchisi to'liq sozlanmagan — sozlovchiga murojaat qiling.",
    }

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = (detail or "")[:500]
        super().__init__(self.MESSAGES.get(code, self.MESSAGES["provider"]))

    @property
    def user_message(self) -> str:
        return self.MESSAGES.get(self.code, self.MESSAGES["provider"])


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    #: Argumentlarni o'qib bo'lmasa (buzilgan JSON) — sababi
    error: str | None = None


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cached: int = 0

    def add(self, other: "Usage") -> None:
        self.input += other.input
        self.output += other.output
        self.cached += other.cached


@dataclass
class StepResult:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    #: end | tool | length | refusal
    stop: str
    #: Provayder formatidagi assistant xabari — keyingi qadamga qo'shiladi
    assistant_message: Any = None
    model: str | None = None


@dataclass
class ToolResult:
    call: ToolCall
    content: str
    is_error: bool = False


@dataclass
class ProviderSettings:
    provider: str
    model: str
    api_key: str
    base_url: str | None = None
    extra: dict = field(default_factory=dict)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _system_text(system) -> str:
    """Yo'riqnoma: bitta matn yoki [statik, dinamik] qismlar. Statik qism
    keshlanadi (har so'rovda bir xil), dinamik (vaqt, sahifa) — undan keyin."""
    if isinstance(system, str):
        return system
    return "\n\n".join(p for p in system if p)


#: GPT-5 / o-seriya — fikrlovchi modellar: fikrlash darajasi past (tez va
#: arzon); asbob chaqirish sifati uchun yetarli
_OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


# ------------------------------------------------------------- OpenAI --


class OpenAICompatibleAdapter:
    """OpenAI Chat Completions (va unga mos xizmatlar)."""

    def __init__(self, cfg: ProviderSettings, timeout: float = 120.0):
        self.cfg = cfg
        self.client = openai.AsyncOpenAI(
            api_key=cfg.api_key, base_url=cfg.base_url or None, timeout=timeout, max_retries=2
        )
        # OpenAI o'zi: max_completion_tokens; mos xizmatlar odatda max_tokens
        self.max_param = "max_completion_tokens" if cfg.provider == "openai" else "max_tokens"
        self.stream_usage = True
        #: Oqimni qo'llamaydigan xizmat uchun — oddiy so'rovga o'tiladi
        self.streaming = True
        self.reasoning_effort = (
            "low" if cfg.provider == "openai" and cfg.model.startswith(_OPENAI_REASONING_PREFIXES) else None
        )

    # --- xabar shakllari ---

    @staticmethod
    def user_message(text: str) -> dict:
        return {"role": "user", "content": text}

    @staticmethod
    def assistant_text(text: str) -> dict:
        return {"role": "assistant", "content": text}

    @staticmethod
    def tool_results(results: list[ToolResult]) -> list[dict]:
        return [
            {"role": "tool", "tool_call_id": r.call.id, "content": r.content}
            for r in results
        ]

    @staticmethod
    def _tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    async def _create(self, kwargs: dict):
        """So'rov; mos xizmat ba'zi parametrni bilmasa — usiz qayta urinadi."""
        for _attempt in range(5):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except (openai.BadRequestError, openai.UnprocessableEntityError) as exc:
                text = str(exc).lower()
                if "stream_options" in kwargs and "stream_options" in text:
                    kwargs.pop("stream_options", None)
                    self.stream_usage = False
                    continue
                if "reasoning_effort" in kwargs and "reasoning" in text:
                    kwargs.pop("reasoning_effort", None)
                    self.reasoning_effort = None
                    continue
                if self.max_param in kwargs and self.max_param in text:
                    other = "max_tokens" if self.max_param == "max_completion_tokens" else "max_completion_tokens"
                    kwargs[other] = kwargs.pop(self.max_param)
                    self.max_param = other
                    continue
                if kwargs.get("stream") and "stream" in text:
                    kwargs["stream"] = False
                    kwargs.pop("stream_options", None)
                    self.streaming = False
                    continue
                raise
        return await self.client.chat.completions.create(**kwargs)

    async def step(
        self, system, messages: list, tools: list[dict], on_text: TextCallback,
        max_output: int = 8000,
    ) -> StepResult:
        system = _system_text(system)
        kwargs: dict = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": self.streaming,
            self.max_param: max_output,
        }
        if tools:
            kwargs["tools"] = self._tools(tools)
            kwargs["tool_choice"] = "auto"
        if self.streaming and self.stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            response = await self._create(kwargs)
            text_parts: list[str] = []
            slots: dict[int, dict] = {}
            usage = None
            finish = None
            if not kwargs.get("stream"):
                # Oqimsiz javob (xizmat oqimni qo'llamaydi)
                usage = getattr(response, "usage", None)
                choice = (response.choices or [None])[0]
                if choice is not None:
                    msg = choice.message
                    if msg.content:
                        text_parts.append(msg.content)
                        await on_text(msg.content)
                    for i, tc in enumerate(msg.tool_calls or []):
                        fn = getattr(tc, "function", None)
                        slots[i] = {
                            "id": tc.id, "name": getattr(fn, "name", "") or "",
                            "args": getattr(fn, "arguments", "") or "",
                        }
                    finish = choice.finish_reason
            else:
                async for chunk in response:
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    for choice in chunk.choices or []:
                        delta = choice.delta
                        if delta is not None:
                            if delta.content:
                                text_parts.append(delta.content)
                                await on_text(delta.content)
                            for tc in delta.tool_calls or []:
                                slot = slots.setdefault(tc.index or 0, {"id": None, "name": "", "args": ""})
                                if tc.id:
                                    slot["id"] = tc.id
                                fn = tc.function
                                if fn is not None:
                                    if fn.name and not slot["name"]:
                                        slot["name"] = fn.name
                                    if fn.arguments:
                                        slot["args"] += fn.arguments
                        if choice.finish_reason:
                            finish = choice.finish_reason
        except openai.AuthenticationError as exc:
            raise AiProviderError("auth", str(exc)) from exc
        except openai.PermissionDeniedError as exc:
            raise AiProviderError("auth", str(exc)) from exc
        except openai.RateLimitError as exc:
            raise AiProviderError("rate", str(exc)) from exc
        except openai.NotFoundError as exc:
            raise AiProviderError("model", str(exc)) from exc
        except openai.BadRequestError as exc:
            raise AiProviderError("bad_request", str(exc)) from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise AiProviderError("network", str(exc)) from exc
        except openai.APIError as exc:
            raise AiProviderError("provider", str(exc)) from exc

        text = "".join(text_parts)
        calls: list[ToolCall] = []
        raw_calls: list[dict] = []
        for index in sorted(slots):
            slot = slots[index]
            call_id = slot["id"] or f"call_{index}"
            args, error = None, None
            try:
                parsed = json.loads(slot["args"] or "{}")
                args = parsed if isinstance(parsed, dict) else None
                if args is None:
                    error = "Arguments must be a JSON object"
            except json.JSONDecodeError as exc:
                error = f"Invalid JSON arguments: {exc.msg}"
            calls.append(ToolCall(call_id, slot["name"], args, error))
            raw_calls.append(
                {"id": call_id, "type": "function",
                 "function": {"name": slot["name"], "arguments": slot["args"] or "{}"}}
            )
        assistant: dict = {"role": "assistant", "content": text or None}
        if raw_calls:
            assistant["tool_calls"] = raw_calls

        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
            used = Usage(int(usage.prompt_tokens or 0), int(usage.completion_tokens or 0), cached)
        else:
            # Xizmat sarfni bermadi — taxminiy (limit hisobi uchun)
            prompt_chars = len(system) + len(json.dumps(messages, ensure_ascii=False, default=str))
            used = Usage(prompt_chars // 4, _estimate_tokens(text) + sum(_estimate_tokens(c["function"]["arguments"]) for c in raw_calls))

        if calls:
            stop = "tool"
        elif finish == "length":
            stop = "length"
        elif finish == "content_filter":
            stop = "refusal"
        else:
            stop = "end"
        return StepResult(text, calls, used, stop, assistant, self.cfg.model)

    async def ping(self) -> tuple[str, Usage]:
        result = await self.step(
            "Reply with the single word OK.", [self.user_message("ping")], [],
            on_text=_noop, max_output=400,
        )
        return result.text.strip(), result.usage

    async def list_models(self) -> list[str]:
        try:
            ids = [m.id async for m in self.client.models.list()]
        except openai.AuthenticationError as exc:
            raise AiProviderError("auth", str(exc)) from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise AiProviderError("network", str(exc)) from exc
        except openai.APIError as exc:
            raise AiProviderError("provider", str(exc)) from exc
        return sorted(set(ids))


# ---------------------------------------------------------- Anthropic --


class AnthropicAdapter:
    """Claude — rasmiy SDK, Messages API (oqim + asboblar)."""

    def __init__(self, cfg: ProviderSettings, timeout: float = 600.0):
        self.cfg = cfg
        self.client = anthropic.AsyncAnthropic(api_key=cfg.api_key, timeout=timeout, max_retries=2)

    @staticmethod
    def user_message(text: str) -> dict:
        return {"role": "user", "content": text}

    @staticmethod
    def assistant_text(text: str) -> dict:
        return {"role": "assistant", "content": text}

    @staticmethod
    def tool_results(results: list[ToolResult]) -> list[dict]:
        # Hammasi BITTA user xabarida (parallel chaqiruvlar uchun shart)
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        ]

    @staticmethod
    def _cached_messages(messages: list) -> list:
        """Oxirgi xabarga kesh belgisi: asboblar aylanishida har qadam
        oldingi qadamlar prefiksini keshdan o'qiydi (asl ro'yxat o'zgarmaydi)."""
        if not messages:
            return messages
        last = dict(messages[-1])
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(content, list) and content and isinstance(content[-1], dict):
            blocks = [dict(b) if isinstance(b, dict) else b for b in content]
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            last["content"] = blocks
        return [*messages[:-1], last]

    def _params(self, system, messages: list, tools: list[dict], max_output: int) -> dict:
        tool_defs = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
                # Oqimda asbob argumentlari ham bo'lib-bo'lib kelsin; ularni
                # agent o'zi tekshiradi (tools.validate)
                "eager_input_streaming": True,
            }
            for t in tools
        ]
        parts = [system] if isinstance(system, str) else [p for p in system if p]
        # Statik qism (asboblar + qoidalar) keshlanadi; dinamik qism keyin
        system_blocks = [{"type": "text", "text": parts[0], "cache_control": {"type": "ephemeral"}}]
        system_blocks += [{"type": "text", "text": p} for p in parts[1:]]
        params: dict = {
            "model": self.cfg.model,
            "max_tokens": max_output,
            "system": system_blocks,
            "messages": self._cached_messages(messages),
        }
        if tool_defs:
            params["tools"] = tool_defs
        if self.cfg.model in _CLAUDE_EFFORT_MODELS:
            params["output_config"] = {"effort": "medium"}
        if self.cfg.model in _CLAUDE_FALLBACK_MODELS:
            # Xavfsizlik klassifikatori rad etsa — server boshqa modelda davom etadi
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def step(
        self, system, messages: list, tools: list[dict], on_text: TextCallback,
        max_output: int = 16000,
    ) -> StepResult:
        params = self._params(system, messages, tools, max_output)
        try:
            async with self.client.beta.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    await on_text(text)
                final = await stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise AiProviderError("auth", str(exc)) from exc
        except anthropic.PermissionDeniedError as exc:
            raise AiProviderError("auth", str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise AiProviderError("rate", str(exc)) from exc
        except anthropic.NotFoundError as exc:
            raise AiProviderError("model", str(exc)) from exc
        except anthropic.BadRequestError as exc:
            raise AiProviderError("bad_request", str(exc)) from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise AiProviderError("network", str(exc)) from exc
        except anthropic.APIError as exc:
            raise AiProviderError("provider", str(exc)) from exc

        text = "".join(b.text for b in final.content if getattr(b, "type", None) == "text")
        calls = []
        for block in final.content:
            if getattr(block, "type", None) == "tool_use":
                args = block.input if isinstance(block.input, dict) else None
                calls.append(
                    ToolCall(block.id, block.name, args, None if args is not None else "Arguments must be an object")
                )
        u = final.usage
        cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        used = Usage(int(u.input_tokens or 0) + cache_read + cache_write, int(u.output_tokens or 0), cache_read)

        reason = final.stop_reason
        if reason == "refusal":
            stop = "refusal"
        elif reason == "max_tokens":
            # Kesilgan asbob argumentlari ishlatilmaydi
            stop = "length"
            calls = []
        elif calls:
            stop = "tool"
        elif reason == "pause_turn":
            stop = "tool"
        else:
            stop = "end"
        return StepResult(
            text, calls, used, stop, {"role": "assistant", "content": final.content}, getattr(final, "model", None)
        )

    async def ping(self) -> tuple[str, Usage]:
        result = await self.step(
            "Reply with the single word OK.", [self.user_message("ping")], [],
            on_text=_noop, max_output=1024,
        )
        return result.text.strip(), result.usage

    async def list_models(self) -> list[str]:
        try:
            ids = [m.id async for m in self.client.models.list()]
        except anthropic.AuthenticationError as exc:
            raise AiProviderError("auth", str(exc)) from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise AiProviderError("network", str(exc)) from exc
        except anthropic.APIError as exc:
            raise AiProviderError("provider", str(exc)) from exc
        return sorted(set(ids))


async def _noop(_text: str) -> None:
    return None


def make_adapter(cfg: ProviderSettings):
    info = PROVIDERS.get(cfg.provider)
    if info is None or not cfg.api_key or not cfg.model:
        raise AiProviderError("config")
    if info.needs_base_url and not cfg.base_url:
        raise AiProviderError("config")
    if info.kind == "anthropic":
        return AnthropicAdapter(cfg)
    return OpenAICompatibleAdapter(
        ProviderSettings(cfg.provider, cfg.model, cfg.api_key, cfg.base_url or info.base_url, cfg.extra)
    )


async def test_connection(cfg: ProviderSettings) -> dict:
    """Sozlovchining "Ulanishni tekshirish" tugmasi: kichik so'rov."""
    started = time.monotonic()
    adapter = make_adapter(cfg)
    reply, usage = await adapter.ping()
    return {
        "ok": True,
        "reply": reply[:200],
        "latency_ms": int((time.monotonic() - started) * 1000),
        "usage": {"input": usage.input, "output": usage.output},
    }


__all__ = [
    "AiProviderError", "PROVIDERS", "ProviderInfo", "ProviderSettings", "StepResult",
    "ToolCall", "ToolResult", "Usage", "make_adapter", "test_connection",
]
