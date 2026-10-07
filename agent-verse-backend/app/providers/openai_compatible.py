"""OpenAI-compatible provider — covers OpenAI, Ollama, Groq, Together, Azure, vLLM.

Any service that speaks the OpenAI Chat Completions + Embeddings API works here.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
    TokenUsage,
)


def _extract_json_objects(text: str) -> list[dict]:
    """Extract top-level JSON objects from free text via a balanced-brace scan.

    Tolerates a leading reasoning block (everything up to the last ``</think>``
    is dropped) and surrounding prose / ```json fences. Returns parsed dict
    objects in order; malformed candidates are skipped.
    """
    if not text:
        return []
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    objects: list[dict] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        escape = False
        for j in range(i, n):
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[i : j + 1]
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, dict):
                                objects.append(parsed)
                        except (json.JSONDecodeError, ValueError):
                            pass
                        i = j
                        break
        i += 1
    return objects


def parse_prompted_tool_calls(content: str, tool_names: set[str]) -> list[dict]:
    """Parse prompted-format tool calls from a model's text response.

    Recognises ``{"tool"|"name": <name>, "arguments"|"input"|"parameters": {...}}``
    for any name in ``tool_names``. Returns provider tool_calls
    (``{"name", "input", "id"}``); empty when the model answered without a tool.
    """
    calls: list[dict] = []
    for obj in _extract_json_objects(content):
        name = obj.get("tool") or obj.get("name") or obj.get("function")
        if not isinstance(name, str) or name not in tool_names:
            continue
        args = obj.get("arguments")
        if args is None:
            args = obj.get("input")
        if args is None:
            args = obj.get("parameters")
        if not isinstance(args, dict):
            args = {}
        calls.append({"name": name, "input": args, "id": f"call_{uuid.uuid4().hex[:12]}"})
    return calls


# ── Thinking (reasoning) models ──────────────────────────────────────────────
# A thinking model served by vLLM / SGLang with a reasoning parser (Qwen3.x,
# DeepSeek-R1 distills, ...) can spend its whole ``max_tokens`` reasoning:
# ``finish_reason=length``, ``content=None``, the text in ``reasoning`` /
# ``reasoning_content`` and ``completion_tokens_details.reasoning_tokens ==
# completion_tokens``. Their chat templates take
# ``chat_template_kwargs={"enable_thinking": false}`` to answer directly.
# The per-model mode comes from the Model Registry (``thinking``: auto / off /
# on, see ``app.ai_router.model_endpoints.registry_thinking_settings``).

_log = logging.getLogger(__name__)

# Endpoints (normalised base URL) whose server refused ``chat_template_kwargs``
# with HTTP 400: never sent there again by this process.
_TEMPLATE_KWARGS_UNSUPPORTED: set[str] = set()
# (endpoint, model) pairs where "auto" saw a thinking model answer only with
# thinking off: later calls send thinking off from the first attempt instead of
# burning a full reasoning budget every time.
_THINKING_AUTO_OFF: set[tuple[str, str]] = set()

# Hosted APIs that reject (HTTP 400) or ignore vLLM's ``chat_template_kwargs``.
# It is never sent there; api.openai.com in particular never gets it.
_NO_TEMPLATE_KWARGS_HOSTS = (
    "api.openai.com",
    "openai.azure.com",
    "api.groq.com",
    "api.x.ai",
    "openrouter.ai",
    "generativelanguage.googleapis.com",
    "api.deepseek.com",
    "api.mistral.ai",
    "api.anthropic.com",
    "api.together.xyz",
    "api.fireworks.ai",
    "api.perplexity.ai",
    "api.cohere.ai",
    "api.cohere.com",
    # NVIDIA's hosted API is handled per model (system-prompt switch) instead.
    "nvidia.com",
)


def _endpoint_key_of(base_url: str | None) -> str:
    return (base_url or "").strip().rstrip("/") or "https://api.openai.com/v1"


def endpoint_accepts_template_kwargs(base_url: str | None) -> bool:
    """Whether ``chat_template_kwargs`` may be sent to the endpoint at *base_url*.

    Self-hosted OpenAI-compatible servers (vLLM / SGLang / an on-prem
    base_url) take it; the canonical OpenAI API (no base_url), the hosted APIs
    in ``_NO_TEMPLATE_KWARGS_HOSTS`` and an endpoint that already refused it in
    this process never get it.
    """
    if not (base_url or "").strip():
        return False
    key = _endpoint_key_of(base_url)
    host = (urlparse(key).hostname or "").lower()
    if not host or any(host == h or host.endswith("." + h) for h in _NO_TEMPLATE_KWARGS_HOSTS):
        return False
    return key not in _TEMPLATE_KWARGS_UNSUPPORTED


def mark_template_kwargs_unsupported(base_url: str | None) -> None:
    """Remember that the endpoint at *base_url* refused ``chat_template_kwargs``."""
    _TEMPLATE_KWARGS_UNSUPPORTED.add(_endpoint_key_of(base_url))


def is_template_kwargs_rejection(status_code: int | None, text: str) -> bool:
    """An HTTP 400 that names ``chat_template_kwargs`` / ``enable_thinking``."""
    low = (text or "").lower()
    is_400 = status_code == 400 or (status_code is None and "400" in low)
    return is_400 and ("chat_template_kwargs" in low or "enable_thinking" in low)


def thinking_off_prompt(base_url: str | None, model: str) -> str | None:
    """The system-prompt thinking switch for *model* at *base_url* (NVIDIA), or None."""
    host = (urlparse(_endpoint_key_of(base_url)).hostname or "").lower()
    if host == "nvidia.com" or host.endswith(".nvidia.com"):
        return _nvidia_thinking_off_prompt(model)
    return None


def thinking_off_body(
    base_url: str | None, model: str, body: dict[str, Any]
) -> dict[str, Any] | None:
    """A chat-completions JSON *body* with thinking turned off for this endpoint,
    or ``None`` when the endpoint has no known safe switch."""
    if endpoint_accepts_template_kwargs(base_url):
        ctk = {**(body.get("chat_template_kwargs") or {}), "enable_thinking": False}
        return {**body, "chat_template_kwargs": ctk}
    prompt = thinking_off_prompt(base_url, model)
    if prompt:
        return {**body, "messages": _with_system_prefix(list(body.get("messages") or []), prompt)}
    return None


class _ThinkingKwargRejectedError(Exception):
    """The endpoint refused ``chat_template_kwargs`` (HTTP 400)."""


def strip_reasoning(text: str) -> str:
    """Drop a leading reasoning block from model text.

    Handles ``<think>...</think>answer`` and a chat template that opens the
    block in the prompt (``...reasoning...</think>answer``). An unterminated
    ``<think>`` (the budget ran out mid-thought) leaves nothing: reasoning is
    never returned as the answer.
    """
    if not text:
        return text
    head = text.lstrip()
    if head.startswith("<think>"):
        _, sep, rest = head.partition("</think>")
        return rest.lstrip() if sep else ""
    if "</think>" in text and "<think>" not in text:
        return text.partition("</think>")[2].lstrip()
    return text


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _reasoning_tokens(usage: Any) -> int:
    details = getattr(usage, "completion_tokens_details", None) if usage is not None else None
    return _int_or_zero(getattr(details, "reasoning_tokens", None)) if details is not None else 0


def _has_reasoning_text(obj: Any) -> bool:
    """Whether a message / stream delta carries reasoning (vLLM / SGLang / DeepSeek)."""
    for attr in ("reasoning_content", "reasoning"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _nvidia_thinking_off_prompt(model: str) -> str | None:
    """The system-prompt switch that turns reasoning off on NVIDIA's hosted API.

    NVIDIA's API (integrate.api.nvidia.com) documents no generic request field
    for it; the Nemotron reasoning models are switched in the system prompt
    (model cards): Llama-3.x-Nemotron v1 (Nano / Super / Ultra) take
    ``detailed thinking off``; the v1.5 / Nano-v2 generation take ``/no_think``.
    Other NVIDIA-hosted models get nothing (no known safe switch): ``None``.
    """
    m = (model or "").lower()
    if "nemotron" not in m:
        return None
    if "v1.5" in m or "v1_5" in m or "-v2" in m:
        return "/no_think"
    if "llama" in m and ("nano" in m or "super" in m or "ultra" in m):
        return "detailed thinking off"
    return None


def _with_system_prefix(messages: list[dict[str, Any]], line: str) -> list[dict[str, Any]]:
    """Put *line* first in the (single, leading) system message."""
    if messages and messages[0].get("role") == "system" and isinstance(
        messages[0].get("content"), str
    ):
        return [{**messages[0], "content": f"{line}\n\n{messages[0]['content']}"}, *messages[1:]]
    return [{"role": "system", "content": line}, *messages]


class _ThinkStreamFilter:
    """Drops a leading ``<think>...</think>`` block from a token stream.

    Tokens pass through untouched once the stream is known not to start with
    one; while a block is open nothing is emitted (only a short tail is kept to
    find the closing tag across chunk boundaries).
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._state = "undecided"  # undecided | think | lstrip | pass
        self.dropped_reasoning = False

    def feed(self, delta: str) -> str:
        if self._state == "pass":
            return delta
        if self._state == "lstrip":
            delta = delta.lstrip()
            if delta:
                self._state = "pass"
            return delta
        self._buf += delta
        if self._state == "undecided":
            head = self._buf.lstrip()
            if not head or (len(head) < len(self._OPEN) and self._OPEN.startswith(head)):
                return ""  # cannot tell yet
            if not head.startswith(self._OPEN):
                self._state = "pass"
                out, self._buf = self._buf, ""
                return out
            self._state = "think"
            self.dropped_reasoning = True
            self._buf = head[len(self._OPEN):]
        idx = self._buf.find(self._CLOSE)
        if idx < 0:
            self._buf = self._buf[-(len(self._CLOSE) - 1):]
            return ""
        rest = self._buf[idx + len(self._CLOSE):].lstrip()
        self._buf = ""
        self._state = "pass" if rest else "lstrip"
        return rest

    def flush(self) -> str:
        """Whatever was held back while undecided (a short non-reasoning prefix)."""
        out = self._buf if self._state == "undecided" else ""
        self._buf = ""
        return out


def _as_image_data_uri(image_data: str) -> str:
    """Return an ``data:image/...;base64,`` URI for a message's base64 image.

    Accepts an already-formed data URI (returned as-is) or raw base64, whose
    leading bytes are sniffed to pick the right MIME (PNG/JPEG/GIF/WebP), so the
    OpenAI-compatible ``image_url`` block is well-formed for vision models.
    """
    if image_data.startswith("data:"):
        return image_data
    mime = "image/png"
    try:
        import base64 as _b64

        head = _b64.b64decode(image_data[:24] + "===", validate=False)
        if head[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif head[:6] in (b"GIF87a", b"GIF89a"):
            mime = "image/gif"
        elif head[:4] == b"RIFF":
            mime = "image/webp"
    except Exception:
        pass
    return f"data:{mime};base64,{image_data}"


class OpenAICompatibleProvider:
    """Provider for OpenAI and any OpenAI-compatible API.

    Args:
        api_key: API key. Uses OPENAI_API_KEY env var if not provided.
        base_url: Override for Ollama/Groq/Together/Azure/vLLM endpoints.
        default_model: Default model to use.
        supports_vision_flag: Whether this endpoint supports image inputs.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        default_model: str = "gpt-5.2",
        embed_model: str | None = None,
        supports_vision_flag: bool = True,
        extra_body: dict[str, Any] | None = None,
        http_client: Any | None = None,
        thinking: str | None = None,
        thinking_budget_tokens: int | None = None,
        embed_dimensions: int | None = None,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError("Install 'openai' to use OpenAICompatibleProvider") from exc

        from app.providers.sdk_options import sdk_client_options

        # ``http_client``: a tenant-supplied base_url passes the SSRF-pinned
        # client (ssrf_guard.public_async_client) so every connection is
        # re-checked at connect time instead of re-resolved by the SDK.
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            **sdk_client_options(),
        )
        # Vendor-specific chat body extensions (e.g. vLLM
        # {"chat_template_kwargs": {"enable_thinking": false}} to suppress a
        # reasoning model's chain-of-thought). Passed verbatim to chat completions.
        self._extra_body = extra_body or {}
        self._base_url = base_url or ""
        # Strict ``json_schema`` guided decoding is only reliable on the canonical
        # OpenAI API. Third-party OpenAI-compatible endpoints (NVIDIA, Groq, vLLM,
        # Together, …) advertise the parameter but can silently emit malformed /
        # truncated output under it — so for those we downgrade a response_schema to
        # plain ``json_object`` mode (see ``complete``). base_url=None ⇒ api.openai.com.
        self._is_canonical_openai = (not self._base_url) or (
            "api.openai.com" in self._base_url
        )
        self._default_model = default_model
        # Embedding model is tracked separately from the chat model: a chat
        # default like "gpt-5.2" must never be sent to the embeddings endpoint.
        self._embed_model_name = embed_model
        # Requested output width of embed_model (OpenAI ``dimensions``; e.g. a
        # Gemini / text-embedding-3 model shortened to the vector index width).
        # Sent only for embed_model, never for another model a caller names.
        self._embed_dimensions = (
            embed_dimensions
            if isinstance(embed_dimensions, int)
            and not isinstance(embed_dimensions, bool)
            and embed_dimensions > 0
            else None
        )
        self._vision = supports_vision_flag
        # Thinking-model default for models the Model Registry sets nothing for:
        # "auto" (None), "off" or "on" (see ``_thinking_settings``).
        self._thinking = thinking
        self._thinking_budget = thinking_budget_tokens

    # Models in the gpt-5.x series use max_completion_tokens; older models use max_tokens.
    _MAX_COMPLETION_TOKENS_MODELS = frozenset(
        {
            "gpt-5.2",
            "gpt-5.2-pro",
            "gpt-5.1",
            "gpt-5.1-codex",
            "gpt-5",
            "gpt-5-pro",
            "gpt-5-mini",
            "gpt-5-nano",
            "gpt-5.3-chat-latest",
            "gpt-5.2-chat-latest",
            "gpt-5.1-chat-latest",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-pro",
            "gpt-5.5",
            "gpt-5.5-pro",
            "o1",
            "o1-pro",
            "o1-preview",
            "o3",
            "o3-pro",
            "o3-mini",
            "o4-mini",
        }
    )

    # Upper bound when retrying a reasoning model that spent its budget thinking.
    _EMPTY_RETRY_MAX_TOKENS = 8192

    # ── Thinking-model control ────────────────────────────────────────────────

    @property
    def _endpoint_key(self) -> str:
        return _endpoint_key_of(getattr(self, "_base_url", "") or None)

    @property
    def _endpoint_host(self) -> str:
        return (urlparse(self._endpoint_key).hostname or "").lower()

    def _thinking_settings(self, model: str) -> tuple[str, int | None]:
        """``(mode, budget_tokens)`` for *model*: the Model Registry's setting,
        else this provider's default, else ``("auto", None)``."""
        mode: str | None = None
        budget: int | None = None
        try:
            from app.ai_router.model_endpoints import (
                parse_thinking_mode,
                registry_thinking_settings,
            )

            mode, budget = registry_thinking_settings(
                model,
                getattr(self, "_base_url", "") or None,
                getattr(self, "_agentverse_provider_type", None),
            )
            if mode is None:
                mode = parse_thinking_mode(getattr(self, "_thinking", None))
        except Exception:  # pragma: no cover - never block a call
            pass
        if budget is None:
            default_budget = getattr(self, "_thinking_budget", None)
            budget = default_budget if _int_or_zero(default_budget) > 0 else None
        return mode or "auto", budget

    def _accepts_template_kwargs(self) -> bool:
        """Whether ``chat_template_kwargs`` may be sent here (see
        :func:`endpoint_accepts_template_kwargs`)."""
        if getattr(self, "_is_canonical_openai", not getattr(self, "_base_url", "")):
            return False
        return endpoint_accepts_template_kwargs(getattr(self, "_base_url", "") or None)

    def _thinking_off_prompt(self, model: str) -> str | None:
        return thinking_off_prompt(getattr(self, "_base_url", "") or None, model)

    def _can_disable_thinking(self, model: str) -> bool:
        return self._accepts_template_kwargs() or self._thinking_off_prompt(model) is not None

    def _first_disable(self, mode: str, model: str) -> bool | None:
        """Thinking switch for the first attempt: True = off, False = on, None = as served."""
        if mode == "off":
            return True
        if mode == "on":
            return False
        return True if (self._endpoint_key, model) in _THINKING_AUTO_OFF else None

    def _apply_thinking(
        self, kwargs: dict[str, Any], model: str, disable: bool | None
    ) -> str | None:
        """Set the thinking switch on chat-completion *kwargs* (mutated).

        Returns how thinking is turned off for this call: ``"kwarg"``
        (``chat_template_kwargs.enable_thinking=false``), ``"prompt"`` (NVIDIA
        Nemotron system switch) or ``None`` (thinking left as served).
        """
        extra = dict(getattr(self, "_extra_body", None) or {})
        ctk = dict(extra.get("chat_template_kwargs") or {})
        how: str | None = None
        if disable is True:
            if self._accepts_template_kwargs():
                ctk["enable_thinking"] = False
                how = "kwarg"
            elif prompt := self._thinking_off_prompt(model):
                kwargs["messages"] = _with_system_prefix(list(kwargs["messages"]), prompt)
                how = "prompt"
        elif disable is False and ctk.get("enable_thinking") is False:
            # The endpoint's default turns thinking off (on-prem cluster); the
            # registry asks for it on.
            ctk["enable_thinking"] = True
        if ctk and self._endpoint_key not in _TEMPLATE_KWARGS_UNSUPPORTED:
            extra["chat_template_kwargs"] = ctk
        else:
            extra.pop("chat_template_kwargs", None)
        if extra:
            kwargs["extra_body"] = extra
        else:
            kwargs.pop("extra_body", None)
        if how is None and ctk.get("enable_thinking") is False and "chat_template_kwargs" in extra:
            how = "kwarg"  # off by the endpoint's static default
        return how

    @staticmethod
    def _template_kwargs_rejected(exc: Exception, kwargs: dict[str, Any]) -> bool:
        if "chat_template_kwargs" not in (kwargs.get("extra_body") or {}):
            return False
        return is_template_kwargs_rejection(getattr(exc, "status_code", None), str(exc))

    async def _create_chat(self, kwargs: dict[str, Any], *, raise_on_reject: bool = False) -> Any:
        """``chat.completions.create``; an endpoint that refuses
        ``chat_template_kwargs`` is remembered and the call is re-sent without
        it (or :class:`_ThinkingKwargRejectedError` raised when *raise_on_reject*)."""
        try:
            return await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not self._template_kwargs_rejected(exc, kwargs):
                raise
            mark_template_kwargs_unsupported(getattr(self, "_base_url", "") or None)
            _log.warning(
                "llm_chat_template_kwargs_unsupported model=%s endpoint_host=%s",
                kwargs.get("model"), self._endpoint_host,
            )
            if raise_on_reject:
                raise _ThinkingKwargRejectedError(str(exc)[:200]) from exc
            extra = {
                k: v for k, v in (kwargs.get("extra_body") or {}).items()
                if k != "chat_template_kwargs"
            }
            if extra:
                kwargs["extra_body"] = extra
            else:
                kwargs.pop("extra_body", None)
            return await self._client.chat.completions.create(**kwargs)

    @staticmethod
    def _still_off(how: str | None, kwargs: dict[str, Any]) -> bool:
        """Whether thinking was really off for the call (a rejected kwarg was dropped)."""
        if how == "kwarg":
            ctk = (kwargs.get("extra_body") or {}).get("chat_template_kwargs") or {}
            return ctk.get("enable_thinking") is False
        return how == "prompt"

    @staticmethod
    def _answered(response: CompletionResponse) -> bool:
        return bool(response.content.strip() or response.tool_calls)

    def _with_thinking_budget(
        self, request: CompletionRequest, mode: str, budget: int | None
    ) -> CompletionRequest:
        """"on" with a budget: reserve *budget* reasoning tokens on top of the
        answer's ``max_tokens`` (a thinking model's reasoning counts against it)."""
        if mode == "on" and budget:
            return dataclasses.replace(request, max_tokens=request.max_tokens + budget)
        return request

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Complete, never returning an empty answer as if it were one.

        A real endpoint can return no text and no tool call: a reasoning model
        that spent its whole ``max_tokens`` thinking (``finish_reason=length``,
        everything in ``reasoning`` / ``reasoning_content``), or a degenerate
        generation. That used to come back as ``content=""``, which the agent
        took as the answer.

        Thinking mode (Model Registry ``thinking``): "off" sends thinking off
        from the start; "on" keeps it (``thinking_budget_tokens`` added to the
        budget); "auto" — when the empty answer is a thinking model's
        (reasoning tokens / reasoning text), first retry ONCE with thinking off
        (remembered per endpoint+model when it works). Then retry once more as
        before (double the budget when truncated) and finally raise
        ``EmptyCompletionError`` so fallback routing / circuit breakers engage.
        """
        model = request.model or self._default_model
        mode, budget = self._thinking_settings(model)
        request = self._with_thinking_budget(request, mode, budget)
        first_disable = self._first_disable(mode, model)
        response = await self._complete_once(request, disable=first_disable)
        if self._answered(response):
            return response
        observed = response.thinking_observed

        if (
            mode == "auto"
            and observed
            and not response.thinking_disabled
            and self._can_disable_thinking(model)
        ):
            from app.observability.metrics import record_thinking_disabled

            try:
                off = await self._complete_once(request, disable=True, raise_on_reject=True)
            except _ThinkingKwargRejectedError:
                record_thinking_disabled("unsupported")
            else:
                if self._answered(off):
                    _THINKING_AUTO_OFF.add((self._endpoint_key, model))
                    record_thinking_disabled("recovered")
                    _log.warning(
                        "llm_thinking_auto_disabled model=%s endpoint_host=%s "
                        "reasoning_tokens=%s max_tokens=%s",
                        model, self._endpoint_host, response.reasoning_tokens,
                        request.max_tokens,
                    )
                    off.thinking_observed = True
                    return off
                record_thinking_disabled("still_empty")

        retry = request
        if response.stop_reason == "length":
            retry = dataclasses.replace(
                request,
                max_tokens=min(max(request.max_tokens, 256) * 2, self._EMPTY_RETRY_MAX_TOKENS),
            )
        _log.warning(
            "llm_empty_completion_retrying model=%s stop_reason=%s max_tokens=%s",
            response.model, response.stop_reason, retry.max_tokens,
        )
        response = await self._complete_once(retry, disable=first_disable)
        if self._answered(response):
            response.thinking_observed = response.thinking_observed or observed
            return response
        from app.core.errors import EmptyCompletionError

        raise EmptyCompletionError(
            f"LLM returned an empty completion twice (model={response.model}, "
            f"stop_reason={response.stop_reason}"
            + (", reasoning only" if observed or response.thinking_observed else "")
            + ")"
        )

    async def _complete_once(
        self,
        request: CompletionRequest,
        *,
        disable: bool | None = None,
        raise_on_reject: bool = False,
    ) -> CompletionResponse:
        model = request.model or self._default_model
        messages = self._normalize_messages(request)

        # gpt-5.x and o-series require max_completion_tokens instead of max_tokens
        _use_completion_tokens = (
            model in self._MAX_COMPLETION_TOKENS_MODELS
            or model.startswith("gpt-5")
            or model.startswith("o1")
            or model.startswith("o3")
            or model.startswith("o4")
        )
        _token_key = "max_completion_tokens" if _use_completion_tokens else "max_tokens"

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            _token_key: request.max_tokens,
            "temperature": request.temperature,
        }
        thinking_how = self._apply_thinking(kwargs, model, disable)
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
            # Force the model to use one of the provided tools rather than responding
            # with plain text. This ensures structured tool_calls are returned when
            # tools are available, enabling proper tool dispatch in the agent graph.
            # A caller can override (e.g. "auto") when a step may legitimately answer
            # without a tool call — such as a final synthesis step that still has a
            # delivery tool on hand.
            kwargs["tool_choice"] = request.tool_choice or "required"
        if request.response_schema is not None and self._is_canonical_openai:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        elif request.response_schema is not None:
            # Third-party OpenAI-compatible endpoint: strict json_schema guided
            # decoding is unreliable here (can return HTTP 200 with malformed /
            # truncated JSON, which no error-path fallback can catch). Force plain
            # json_object mode instead — the schema shape is already described in
            # the prompt, and this reliably yields a single parseable JSON object.
            kwargs["response_format"] = {"type": "json_object"}
            # json_object mode enforces "some JSON", not the schema: without the
            # schema in the prompt a model invents its own keys (the on-prem Qwen
            # answered {"is_entailed": true} to the citation verifier, so every
            # grounded RAG answer was rejected). State the contract explicitly.
            kwargs["messages"] = _with_schema_instruction(
                kwargs["messages"], request.response_schema
            )
        elif request.json_object:
            # Plain JSON-object mode: no schema to enforce, just force the model to
            # emit a single JSON object (suppresses prose / chain-of-thought that a
            # reasoning model otherwise wraps around the answer).
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self._create_chat(kwargs, raise_on_reject=raise_on_reject)
        except _ThinkingKwargRejectedError:
            raise
        except Exception as _strict_err:
            # OpenAI strict mode rejects schemas that don't list every property
            # in `required`. If we get a 400 schema validation error, fall back
            # to non-strict json_object mode so the goal can still proceed.
            _err_str = str(_strict_err).lower()
            if request.response_schema is not None and (
                "400" in _err_str
                or "invalid_request_error" in _err_str
                or "invalid schema" in _err_str
                or "response_format" in _err_str
            ):
                import logging as _log

                _log.getLogger(__name__).warning(
                    "openai_strict_schema_rejected_falling_back_to_json_object: %s",
                    str(_strict_err)[:200],
                )
                kwargs["response_format"] = {"type": "json_object"}
                response = await self._create_chat(kwargs)
            elif "tool_choice" in kwargs and (
                "400" in _err_str
                or "invalid_request_error" in _err_str
                or "tool_choice" in _err_str
            ):
                # tool_choice="required" rejected — fall back to auto
                import logging as _log2

                _log2.getLogger(__name__).warning(
                    "tool_choice_required_rejected_falling_back_to_auto: %s",
                    str(_strict_err)[:200],
                )
                kwargs["tool_choice"] = "auto"
                try:
                    response = await self._create_chat(kwargs)
                except Exception as _auto_err:
                    # The server has no native tool parser at all (e.g. a vLLM
                    # started without --enable-auto-tool-choice/--tool-call-parser).
                    # Last resort: prompted tool-calling — describe the tools in the
                    # prompt and parse the tool call out of the plain text response.
                    _auto_str = str(_auto_err).lower()
                    if request.tools and (
                        "tool" in _auto_str
                        or "400" in _auto_str
                        or "auto" in _auto_str
                        or "parser" in _auto_str
                    ):
                        _log2.getLogger(__name__).warning(
                            "native_tool_calling_unavailable_using_prompted_fallback: %s",
                            str(_auto_err)[:200],
                        )
                        return await self._prompted_tool_complete(
                            request, model, _token_key, disable=disable
                        )
                    raise
            else:
                raise

        # Record token and cost metrics (never let this break the main path)
        try:
            from app.intelligence.cost_tracker import calculate_cost
            from app.observability.metrics import record_cost_usd, record_llm_tokens

            usage = getattr(response, "usage", None)
            if usage:
                _label = self._metrics_label()
                record_llm_tokens(
                    _label, response.model or "", "prompt", getattr(usage, "prompt_tokens", 0)
                )
                record_llm_tokens(
                    _label,
                    response.model or "",
                    "completion",
                    getattr(usage, "completion_tokens", 0),
                )
                cost = calculate_cost(
                    response.model or "",
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                )
                if cost > 0:
                    record_cost_usd("llm", cost)
        except Exception:
            pass

        choice = response.choices[0]
        raw_content = choice.message.content or ""
        # A reasoning block left in the text (no reasoning parser on the
        # server) is never part of the answer; reasoning alone is no answer.
        content = strip_reasoning(raw_content) if isinstance(raw_content, str) else raw_content
        reasoning_tokens = _reasoning_tokens(getattr(response, "usage", None))
        thinking_observed = (
            reasoning_tokens > 0
            or _has_reasoning_text(choice.message)
            or content != raw_content
        )
        tool_calls = []
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "name": tc.function.name,
                    "input": (json.loads(tc.function.arguments) if tc.function.arguments else {})
                    if isinstance(tc.function.arguments, str)
                    else (tc.function.arguments or {}),
                    "id": tc.id,
                }
                for tc in choice.message.tool_calls
            ]

        _prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        _completion_tokens = response.usage.completion_tokens if response.usage else 0
        return CompletionResponse(
            content=content,
            model=response.model,
            input_tokens=_prompt_tokens,
            output_tokens=_completion_tokens,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "stop",
            usage=TokenUsage(
                prompt_tokens=_prompt_tokens,
                completion_tokens=_completion_tokens,
                total_tokens=_prompt_tokens + _completion_tokens,
            ),
            reasoning_tokens=reasoning_tokens,
            thinking_observed=thinking_observed,
            thinking_disabled=self._still_off(thinking_how, kwargs),
        )

    async def _prompted_tool_complete(
        self,
        request: CompletionRequest,
        model: str,
        token_key: str,
        *,
        disable: bool | None = None,
    ) -> CompletionResponse:
        """Prompted tool-calling fallback for servers without a native tool parser.

        Describes the available tools in a system instruction, calls plain chat
        (no ``tools=``/``tool_choice``), and parses the tool call out of the text.
        The synthesized ``tool_calls`` are identical in shape to the native path,
        so the agent loop is unaffected. When the model answers without a tool,
        ``tool_calls`` is empty and the text is returned as-is.
        """
        tool_lines = []
        for t in request.tools:
            props = ""
            if isinstance(t.input_schema, dict):
                props = ", ".join((t.input_schema.get("properties") or {}).keys())
            tool_lines.append(f"- {t.name}({props}): {t.description}")
        instruction = (
            "You have access to these tools:\n"
            + "\n".join(tool_lines)
            + "\n\nWhen a tool is needed, respond with ONLY a JSON object and nothing else:\n"
            '{"tool": "<tool_name>", "arguments": { <arg>: <value>, ... }}\n'
            "If no tool is needed, answer the user normally in plain text."
        )
        # Normalize first (merges any original system content into one leading
        # system message), then fold the tool instruction into that single leading
        # system message — so the request still has exactly one system message at
        # index 0 (strict chat templates reject a second/mid-array system message).
        normalized = self._normalize_messages(request)
        if normalized and normalized[0].get("role") == "system":
            normalized[0] = {
                "role": "system",
                "content": f"{instruction}\n\n{normalized[0]['content']}",
            }
            messages = normalized
        else:
            messages = [{"role": "system", "content": instruction}, *normalized]
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            token_key: request.max_tokens,
            "temperature": request.temperature,
        }
        thinking_how = self._apply_thinking(kwargs, model, disable)
        response = await self._create_chat(kwargs)
        choice = response.choices[0]
        raw_content = choice.message.content or ""
        content = strip_reasoning(raw_content) if isinstance(raw_content, str) else raw_content
        reasoning_tokens = _reasoning_tokens(getattr(response, "usage", None))
        tool_calls = parse_prompted_tool_calls(content, {t.name for t in request.tools})

        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0
        return CompletionResponse(
            content="" if tool_calls else content,
            model=response.model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else (choice.finish_reason or "stop"),
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            reasoning_tokens=reasoning_tokens,
            thinking_observed=(
                reasoning_tokens > 0
                or _has_reasoning_text(choice.message)
                or content != raw_content
            ),
            thinking_disabled=self._still_off(thinking_how, kwargs),
        )

    def _token_key(self, model: str) -> str:
        use_completion_tokens = (
            model in self._MAX_COMPLETION_TOKENS_MODELS
            or model.startswith("gpt-5")
            or model.startswith("o1")
            or model.startswith("o3")
            or model.startswith("o4")
        )
        return "max_completion_tokens" if use_completion_tokens else "max_tokens"

    def _stream_kwargs(self, request: CompletionRequest) -> tuple[dict[str, Any], str | None]:
        """Streaming chat kwargs with the model's thinking mode applied, and how
        thinking is turned off (see ``_apply_thinking``)."""
        model = request.model or self._default_model
        mode, budget = self._thinking_settings(model)
        request = self._with_thinking_budget(request, mode, budget)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._normalize_messages(request),
            self._token_key(model): request.max_tokens,
            "stream": True,
        }
        how = self._apply_thinking(kwargs, model, self._first_disable(mode, model))
        return kwargs, how

    @staticmethod
    def _chunk_reasoning(chunk: Any) -> bool:
        """Whether a stream chunk carries reasoning (delta text or usage count)."""
        if _reasoning_tokens(getattr(chunk, "usage", None)) > 0:
            return True
        choices = getattr(chunk, "choices", None)
        if not choices:
            return False
        return _has_reasoning_text(getattr(choices[0], "delta", None))

    async def stream_complete(self, request: CompletionRequest):
        """Stream completion tokens one by one via the OpenAI streaming API.

        A leading reasoning block is dropped from the stream. A stream that
        carried only reasoning (nothing emitted) is answered by ``complete()``
        instead, which retries a thinking model with thinking off ("auto") or
        raises ``EmptyCompletionError`` — reasoning is never streamed as the answer.
        """
        kwargs, _how = self._stream_kwargs(request)
        think = _ThinkStreamFilter()
        emitted = False
        reasoning = False
        # Never yield the provider error AS model output (it used to emit
        # "[stream error: ...]" as content, which callers rendered/saved as
        # the answer): errors propagate so callers can fail over or report it.
        stream = await self._create_chat(kwargs)
        async for chunk in stream:
            reasoning = reasoning or self._chunk_reasoning(chunk)
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta and isinstance(delta, str):
                out = think.feed(delta)
                if out:
                    emitted = True
                    yield out
        tail = think.flush()
        if tail:
            emitted = True
            yield tail
        if not emitted and (reasoning or think.dropped_reasoning):
            response = await self.complete(request)
            if response.content:
                yield response.content

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        """Stream tokens from the OpenAI-compatible API, calling on_token for each delta.

        Falls back to a non-streaming complete() call if the streaming API raises,
        or when tools are provided (tool calls require non-streaming to parse correctly).
        A leading reasoning block is never emitted; a stream that carried only
        reasoning is answered by ``complete()`` (thinking-model retry / raise).
        """
        # When tools are provided, use the non-streaming complete() path which
        # correctly handles OpenAI function calling / tool_calls responses.
        # Streaming API returns tool call deltas that are complex to reassemble.
        if request.tools:
            return await self.complete(request)

        model = request.model or self._default_model
        kwargs, how = self._stream_kwargs(request)
        kwargs["temperature"] = request.temperature
        # Ask for the final usage chunk: without it a streamed completion carries
        # no token counts, so the call was never charged to the cost ledger.
        kwargs["stream_options"] = {"include_usage": True}

        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0
        reasoning_tokens = 0
        reasoning = False
        served_model: str = model
        think = _ThinkStreamFilter()

        try:
            stream = await self._create_chat(kwargs)
            async for chunk in stream:
                reasoning = reasoning or self._chunk_reasoning(chunk)
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta and isinstance(delta, str):
                    out = think.feed(delta)
                    if out:
                        full_text += out
                        await on_token(out)
                # Pick up usage from the final chunk when available
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = int(getattr(usage, "prompt_tokens", prompt_tokens) or 0)
                    completion_tokens = int(
                        getattr(usage, "completion_tokens", completion_tokens) or 0
                    )
                    reasoning_tokens = _reasoning_tokens(usage) or reasoning_tokens
                    served_model = getattr(chunk, "model", None) or served_model
            tail = think.flush()
            if tail:
                full_text += tail
                await on_token(tail)
        except Exception as exc:
            if full_text:
                # Tokens already reached the caller: re-running complete() would
                # emit a second, different answer after the partial one. Propagate
                # so the executor can send token_reset and fail over.
                raise
            _log.warning("openai_stream_tokens_failed error=%s fallback=True", str(exc))
            return await self.complete(request)

        if not full_text.strip() and (reasoning or think.dropped_reasoning):
            # Only reasoning arrived and nothing reached the caller: answer
            # through complete() (thinking off on "auto", else EmptyCompletionError).
            return await self.complete(request)

        return CompletionResponse(
            content=full_text,
            model=served_model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            usage=(
                TokenUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                )
                if (prompt_tokens or completion_tokens)
                else None
            ),
            reasoning_tokens=reasoning_tokens,
            thinking_observed=reasoning or think.dropped_reasoning,
            thinking_disabled=self._still_off(how, kwargs),
        )

    @staticmethod
    def _normalize_messages(request: CompletionRequest) -> list[dict[str, Any]]:
        """Build the messages list with all system content merged into ONE leading
        message.

        Many self-hosted chat templates (vLLM/Qwen, etc.) reject a request whose
        system message is not first, or that has several system messages
        ("System message must be at the beginning"). OpenAI is lenient; these are
        not. Merge ``request.system`` and every ``role == "system"`` message into a
        single system message at index 0, then the rest in order — compatible with
        both strict and lenient backends.
        """
        system_parts: list[str] = []
        if request.system:
            system_parts.append(str(request.system))
        others: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                # System content is expected to be text; ignore non-str (multimodal).
                if isinstance(m.content, str) and m.content:
                    system_parts.append(m.content)
            elif getattr(m, "image_data", None) and isinstance(m.content, str):
                # Vision: a base64 image on the message becomes the OpenAI
                # multimodal content array (text + image_url data URI). Without
                # this the image was silently dropped and the model answered as if
                # blind — so provider-path OCR/vision produced hallucinated text.
                others.append(
                    {
                        "role": m.role,
                        "content": [
                            {"type": "text", "text": m.content},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": _as_image_data_uri(str(m.image_data)),
                                },
                            },
                        ],
                    }
                )
            else:
                others.append({"role": m.role, "content": m.content})
        messages: list[dict[str, Any]] = []
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})
        messages.extend(others)
        return messages

    def _embed_model(self, requested: str | None = None) -> str:
        """Resolve the embedding model: explicit request → configured embed_model.

        Falls back to ``text-embedding-3-small`` only when neither is set, so a
        self-hosted endpoint configured with its own embed_model (e.g. a
        Qwen3-Embedding on vLLM) is used instead of a hardcoded OpenAI name — and
        the chat default_model (e.g. "gpt-5.2") is NEVER sent to /embeddings.
        """
        return requested or self._embed_model_name or "text-embedding-3-small"

    def _embed_kwargs(self, input_type: str, model: str | None = None) -> dict[str, Any]:
        """Optional /embeddings fields.

        ``dimensions``: the requested output width of the configured embed_model
        (only for that model, only when set).

        NVIDIA's retrieval embedders are asymmetric: ``input_type`` selects the
        query vs passage encoder (``nv-embedqa-*`` rejects requests without it).
        The OpenAI API has no such field, so it is only sent to NVIDIA endpoints.
        """
        kwargs: dict[str, Any] = {}
        if self._embed_dimensions and (model is None or model == self._embed_model_name):
            kwargs["dimensions"] = self._embed_dimensions
        if "nvidia.com" in self._base_url:
            kwargs["extra_body"] = {
                "input_type": "query" if input_type == "query" else "passage",
                "truncate": "END",
            }
        return kwargs

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        model = self._embed_model(request.model)
        response = await self._client.embeddings.create(
            model=model,
            input=request.texts,
            **self._embed_kwargs(request.input_type, model),
        )
        data = sorted(response.data, key=lambda e: getattr(e, "index", 0))
        return EmbedResponse(
            embeddings=[item.embedding for item in data],
            model=response.model,
            total_tokens=response.usage.total_tokens if response.usage else 0,
        )

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed up to 2048 texts in one OpenAI API call (OpenAI batch limit).

        Uses ``text-embedding-3-small`` by default (same as ``embed()``).
        Splits into batches of 2048 automatically when *texts* is longer.
        """
        if not texts:
            return []

        model = self._embed_model()
        all_embeddings: list[list[float]] = []
        batch_size = 2048  # OpenAI API limit per request

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await self._client.embeddings.create(
                model=model,
                input=batch,
                **self._embed_kwargs("document", model),
            )
            # Sort by index to preserve input order (OpenAI may reorder)
            sorted_data = sorted(response.data, key=lambda e: e.index)
            all_embeddings.extend(e.embedding for e in sorted_data)

        return all_embeddings

    def supports_vision(self) -> bool:
        return self._vision

    def supports_tool_use(self) -> bool:
        return True

    def _metrics_label(self) -> str:
        """Provider label for token metrics: the configured provider name.

        Every OpenAI-compatible backend (Groq, DeepSeek, vLLM, NVIDIA, ...) used to
        be recorded as "openai", so per-provider token/cost dashboards merged them.
        """
        configured = getattr(self, "_agentverse_provider_type", None)
        if isinstance(configured, str) and configured:
            return configured
        cls_name = getattr(type(self), "provider_name", None)
        return cls_name if isinstance(cls_name, str) and cls_name else "openai"

    def supports_structured_output(self) -> bool:
        return True


def _with_schema_instruction(
    messages: list[dict[str, Any]], schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Put the JSON-schema contract in the (leading) system message.

    Merged into an existing first system message or prepended as one — several
    chat templates (Qwen's among them) reject a system message after the first turn.
    """
    instruction = (
        "Respond with a single JSON object that validates against this JSON Schema, "
        "using exactly these property names:\n" + json.dumps(schema)
    )
    if messages and messages[0].get("role") == "system" and isinstance(
        messages[0].get("content"), str
    ):
        first = {**messages[0], "content": f"{messages[0]['content']}\n\n{instruction}"}
        return [first, *messages[1:]]
    return [{"role": "system", "content": instruction}, *messages]
