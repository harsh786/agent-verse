"""The shared memory-write gate (MEMORY_WRITE guardrail + injection screen).

Every durable memory store — long-term, canonical/Reflexion, episodic,
procedural, execution, department and the legacy reflexion lessons — routes
the text it is about to store through :func:`screen_memory_content` (or
:func:`screen_memory_fields` for a multi-field record) BEFORE it is stored,
in the database or in a process cache. Stored memories are later recalled into
planner prompts, so a memory write is a path for both data leakage (PII,
secrets) and stored prompt injection.

The gate:

1. refuses content carrying a prompt-injection payload (the same deterministic
   detector the Guardrails 2.0 ``prompt_injection`` rule uses, including
   base64 / rot13 / leetspeak / homoglyph obfuscation) — a memory that says
   "ignore previous instructions" must never reach a later goal's planner;
2. evaluates Guardrails 2.0 ``MEMORY_WRITE`` rules — the baseline PII/secrets
   rule plus any tenant/compliance-bundle rules. A blocked write is dropped and
   a redacting rule's output is what gets stored.

Content the gate could not vet is never stored (fail closed:
:class:`MemoryScreeningError`). Only the first ``_SCREEN_WINDOW`` characters are
vetted, so only those are ever returned for storage.
"""

from __future__ import annotations

from collections.abc import Mapping

_SCREEN_WINDOW = 4_000


class MemoryScreeningError(RuntimeError):
    """The memory-write guardrail could not vet the content; nothing may be stored."""


class MemoryWriteBlockedError(ValueError):
    """The memory-write gate refused the content (guardrail block or injection)."""


def _blocked(store: str, reason: str, tenant_id: str, goal_id: str | None) -> None:
    from app.observability.logging import get_logger
    from app.observability.metrics import record_memory_write_blocked

    record_memory_write_blocked(store, reason)
    get_logger(__name__).info(
        "memory_write_blocked", store=store, reason=reason, tenant_id=tenant_id, goal_id=goal_id
    )


def contains_prompt_injection(content: str) -> bool:
    """Whether *content* carries a prompt-injection payload (deterministic)."""
    from app.guardrails_v2.engine import guardrails_engine

    return bool(guardrails_engine._check_injection(content).get("triggered"))


async def screen_memory_content(
    content: str,
    *,
    tenant_id: str,
    goal_id: str | None = None,
    store: str = "canonical",
) -> str | None:
    """Return the content to store (possibly redacted), or None when blocked.

    Raises :class:`MemoryScreeningError` when the gate cannot vet the content.
    """
    window = content[:_SCREEN_WINDOW]
    if not tenant_id:
        raise MemoryScreeningError("memory-write guardrail needs a tenant to vet against")
    try:
        from app.guardrails_v2.engine import guardrails_engine
        from app.guardrails_v2.models import GuardrailLayer

        injected = contains_prompt_injection(window)
        result: dict[str, object] = {}
        if not injected:
            guardrails_engine.ensure_default_rules(tenant_id)
            result = await guardrails_engine.evaluate(
                content=window,
                layer=GuardrailLayer.MEMORY_WRITE,
                tenant_id=tenant_id,
                goal_id=goal_id or None,
            )
    except Exception as exc:
        from app.observability.metrics import record_memory_degraded

        record_memory_degraded(store, "screen")
        raise MemoryScreeningError(
            f"memory-write guardrail could not vet the content: {type(exc).__name__}"
        ) from exc
    if injected:
        _blocked(store, "injection", tenant_id, goal_id)
        return None
    if result.get("blocked"):
        _blocked(store, "guardrail", tenant_id, goal_id)
        return None
    redacted = result.get("redacted_content")
    if isinstance(redacted, str) and redacted != window:
        # A redacting rule's output is vetted text too — but never let a
        # redaction re-introduce an injection payload.
        if contains_prompt_injection(redacted):
            _blocked(store, "injection", tenant_id, goal_id)
            return None
        return redacted
    return window


async def screen_memory_fields(
    fields: Mapping[str, str],
    *,
    tenant_id: str,
    goal_id: str | None = None,
    store: str,
) -> dict[str, str] | None:
    """Screen every non-empty text field of one memory record.

    Returns the fields to store (redacted where a rule redacted), or None when
    ANY field is blocked — a record is stored whole or not at all. Raises
    :class:`MemoryScreeningError` when any field cannot be vetted.
    """
    screened: dict[str, str] = {}
    for name, value in fields.items():
        if not value:
            screened[name] = value
            continue
        out = await screen_memory_content(
            value, tenant_id=tenant_id, goal_id=goal_id, store=store
        )
        if out is None:
            return None
        screened[name] = out
    return screened


async def vet_canonical_write(
    content: str, *, tenant_id: str, goal_id: str | None, sensitive: bool
) -> str:
    """The gate for a canonical ``memory_records`` write; returns the text to store.

    Sensitive (confidential/restricted) content is sealed by design and never
    recalled as text, so only the injection screen applies to it; everything
    else passes the full gate. Raises :class:`MemoryWriteBlockedError` on a
    block and :class:`MemoryScreeningError` when the gate cannot vet it.
    """
    if sensitive:
        try:
            injected = contains_prompt_injection(content)
        except Exception as exc:
            raise MemoryScreeningError(
                f"memory-write guardrail could not vet the content: {type(exc).__name__}"
            ) from exc
        if injected:
            _blocked("canonical", "injection", tenant_id, goal_id)
            raise MemoryWriteBlockedError("memory content rejected by the memory-write gate")
        return content
    screened = await screen_memory_content(
        content, tenant_id=tenant_id, goal_id=goal_id, store="canonical"
    )
    if screened is None:
        raise MemoryWriteBlockedError("memory content rejected by the memory-write gate")
    return screened


__all__ = [
    "MemoryScreeningError",
    "MemoryWriteBlockedError",
    "contains_prompt_injection",
    "screen_memory_content",
    "screen_memory_fields",
    "vet_canonical_write",
]
