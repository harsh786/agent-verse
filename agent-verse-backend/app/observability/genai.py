"""Per-LLM-call observability — the GenAI "generation" span.

This is the primitive AgentVerse was missing (the Langfuse/LangSmith "generation"):
one OpenTelemetry span per LLM call, carrying GenAI semantic-convention attributes
(model, token usage, cost, latency, finish reason) plus our own role/goal context.
Parented under whatever span is active (a LangGraph node), so a goal's trace tree
shows every model call with its tokens/cost/latency — and, because the collector
fans OTLP out to Langfuse, these become Langfuse generations for free.

Design notes:
  * Provider-agnostic: wrap any ``complete``/``stream_tokens`` call site with
    ``async with record_generation(request, provider_system=..., role=...) as rec``
    and call ``rec.set_response(resp, cost_usd=..., cache_hit=...)``.
  * Prompt/completion capture is OFF by default (privacy); when enabled it is run
    through the existing secret redactor before being attached as span events.
  * Never raises into the caller — observability must not break inference.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

if TYPE_CHECKING:
    from app.providers.base import CompletionRequest, CompletionResponse

_tracer = trace.get_tracer("agentverse.genai")


def _capture_default() -> bool:
    """Whether to attach prompt/completion content, from settings (default False)."""
    try:
        from app.core.config import get_settings

        return bool(getattr(get_settings(), "otel_capture_llm_content", False))
    except Exception:
        return False


def _redact(text: str) -> str:
    try:
        from app.agent.sanitization import redact_sensitive_text

        return redact_sensitive_text(text)
    except Exception:
        return text


def _prompt_text(request: CompletionRequest) -> str:
    parts: list[str] = []
    if getattr(request, "system", None):
        parts.append(f"[system] {request.system}")
    for m in request.messages:
        # Only ever serialise TEXT content into a span. Non-text message content
        # is vision/tool payloads (e.g. base64 images) — never dump those into a
        # trace: they are large and may be sensitive. Redaction below still runs.
        if isinstance(m.content, str):
            parts.append(f"[{m.role}] {m.content}")
        else:
            parts.append(f"[{m.role}] [non-text content omitted]")
    return "\n".join(parts)


class GenerationRecorder:
    """Handle for a single in-flight generation span."""

    def __init__(self, span: trace.Span, capture_content: bool) -> None:
        self._span = span
        self._capture = capture_content
        self._responded = False

    def set_response(
        self,
        response: CompletionResponse,
        *,
        cost_usd: float | None = None,
        cache_hit: bool = False,
        first_token_latency_ms: float | None = None,
    ) -> None:
        """Attach response attributes. Safe to call once; extra calls are ignored."""
        if self._responded:
            return
        self._responded = True
        with contextlib.suppress(Exception):
            span = self._span
            span.set_attribute("gen_ai.usage.input_tokens", int(response.input_tokens))
            span.set_attribute("gen_ai.usage.output_tokens", int(response.output_tokens))
            span.set_attribute("gen_ai.response.model", response.model or "")
            span.set_attribute("gen_ai.response.finish_reason", response.stop_reason or "")
            span.set_attribute("gen_ai.cache_hit", bool(cache_hit))
            if cost_usd is not None:
                span.set_attribute("gen_ai.usage.cost_usd", float(cost_usd))
            if first_token_latency_ms is not None:
                span.set_attribute("agentverse.llm.first_token_ms", float(first_token_latency_ms))
            if self._capture and response.content:
                span.add_event(
                    "gen_ai.content.completion",
                    {"gen_ai.completion": _redact(str(response.content))[:8000]},
                )


@contextlib.asynccontextmanager
async def record_generation(
    request: CompletionRequest,
    *,
    provider_system: str,
    role: str | None = None,
    capture_content: bool | None = None,
) -> AsyncIterator[GenerationRecorder]:
    """Record one LLM call as a GenAI span. See module docstring for usage."""
    capture = _capture_default() if capture_content is None else bool(capture_content)
    name = f"gen_ai.{role}" if role else "gen_ai.chat"
    start = time.monotonic()
    with _tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
        with contextlib.suppress(Exception):
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.system", provider_system)
            span.set_attribute("gen_ai.request.model", request.model or "")
            span.set_attribute("gen_ai.request.temperature", float(request.temperature))
            span.set_attribute("gen_ai.request.max_tokens", int(request.max_tokens))
            if role:
                span.set_attribute("agentverse.role", role)
            if capture:
                span.add_event(
                    "gen_ai.content.prompt",
                    {"gen_ai.prompt": _redact(_prompt_text(request))[:8000]},
                )
        recorder = GenerationRecorder(span, capture)
        try:
            yield recorder
        except BaseException as exc:  # record then re-raise — never swallow the real error
            with contextlib.suppress(Exception):
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise
        finally:
            with contextlib.suppress(Exception):
                span.set_attribute(
                    "agentverse.llm.duration_ms", (time.monotonic() - start) * 1000.0
                )
