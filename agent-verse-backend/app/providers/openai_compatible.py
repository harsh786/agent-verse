"""OpenAI-compatible provider — covers OpenAI, Ollama, Groq, Together, Azure, vLLM.

Any service that speaks the OpenAI Chat Completions + Embeddings API works here.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

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
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError("Install 'openai' to use OpenAICompatibleProvider") from exc

        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
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
        self._vision = supports_vision_flag

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

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
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

        kwargs = {
            "model": model,
            "messages": messages,
            _token_key: request.max_tokens,
            "temperature": request.temperature,
        }
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
            kwargs["tool_choice"] = "required"
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
        elif request.json_object:
            # Plain JSON-object mode: no schema to enforce, just force the model to
            # emit a single JSON object (suppresses prose / chain-of-thought that a
            # reasoning model otherwise wraps around the answer).
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.chat.completions.create(**kwargs)
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
                response = await self._client.chat.completions.create(**kwargs)
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
                    response = await self._client.chat.completions.create(**kwargs)
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
                        return await self._prompted_tool_complete(request, model, _token_key)
                    raise
            else:
                raise

        # Record token and cost metrics (never let this break the main path)
        try:
            from app.governance.pricing import estimate_cost
            from app.observability.metrics import record_cost_usd, record_llm_tokens

            usage = getattr(response, "usage", None)
            if usage:
                record_llm_tokens(
                    "openai", response.model or "", "prompt", getattr(usage, "prompt_tokens", 0)
                )
                record_llm_tokens(
                    "openai",
                    response.model or "",
                    "completion",
                    getattr(usage, "completion_tokens", 0),
                )
                cost = estimate_cost(
                    response.model or "",
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                )
                if cost > 0:
                    record_cost_usd("llm", cost)
        except Exception:
            pass

        choice = response.choices[0]
        content = choice.message.content or ""
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
        )

    async def _prompted_tool_complete(
        self, request: CompletionRequest, model: str, token_key: str
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
        kwargs: dict = {
            "model": model,
            "messages": messages,
            token_key: request.max_tokens,
            "temperature": request.temperature,
        }
        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        content = choice.message.content or ""
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
        )

    async def stream_complete(self, request: CompletionRequest):
        """Stream completion tokens one by one via the OpenAI streaming API."""
        model = request.model or self._default_model
        messages = self._normalize_messages(request)
        _use_completion_tokens = (
            model in self._MAX_COMPLETION_TOKENS_MODELS
            or model.startswith("gpt-5")
            or model.startswith("o1")
            or model.startswith("o3")
            or model.startswith("o4")
        )
        _token_key = "max_completion_tokens" if _use_completion_tokens else "max_tokens"
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                **{_token_key: request.max_tokens},
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception as exc:
            yield f"[stream error: {exc}]"

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        """Stream tokens from the OpenAI-compatible API, calling on_token for each delta.

        Falls back to a non-streaming complete() call if the streaming API raises,
        or when tools are provided (tool calls require non-streaming to parse correctly).
        """
        import logging as _logging

        # When tools are provided, use the non-streaming complete() path which
        # correctly handles OpenAI function calling / tool_calls responses.
        # Streaming API returns tool call deltas that are complex to reassemble.
        if request.tools:
            return await self.complete(request)

        model = request.model or self._default_model
        messages = self._normalize_messages(request)

        _use_ct = (
            model in self._MAX_COMPLETION_TOKENS_MODELS
            or model.startswith("gpt-5")
            or model.startswith("o1")
            or model.startswith("o3")
            or model.startswith("o4")
        )
        kwargs = {
            "model": model,
            "messages": messages,
            "max_completion_tokens" if _use_ct else "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": True,
        }

        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    full_text += delta
                    await on_token(delta)
                # Pick up usage from the final chunk when available
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens)
                    completion_tokens = getattr(usage, "completion_tokens", completion_tokens)
        except Exception as exc:
            _logging.getLogger(__name__).warning(
                "openai_stream_tokens_failed error=%s fallback=True", str(exc)
            )
            return await self.complete(request)

        return CompletionResponse(
            content=full_text,
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
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

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        model = self._embed_model(request.model)
        response = await self._client.embeddings.create(
            model=model,
            input=request.texts,
        )
        return EmbedResponse(
            embeddings=[item.embedding for item in response.data],
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
            )
            # Sort by index to preserve input order (OpenAI may reorder)
            sorted_data = sorted(response.data, key=lambda e: e.index)
            all_embeddings.extend(e.embedding for e in sorted_data)

        return all_embeddings

    def supports_vision(self) -> bool:
        return self._vision

    def supports_tool_use(self) -> bool:
        return True

    def supports_structured_output(self) -> bool:
        return True
