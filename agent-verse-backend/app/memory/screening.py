"""MEMORY_WRITE guardrail screening for canonical memory writes.

The same gate long-term memory applies (``LongTermMemoryStore.store_async``):
Guardrails 2.0 ``MEMORY_WRITE`` rules — the baseline PII/secrets rule and any
tenant/compliance-bundle rules — vet the content before it becomes a durable,
cross-session memory. A blocked write is dropped, a redacting rule's output is
what gets stored, and content the guardrail could not vet is never stored
(fail closed: :class:`MemoryScreeningError`).
"""

from __future__ import annotations

_SCREEN_WINDOW = 4_000


class MemoryScreeningError(RuntimeError):
    """The memory-write guardrail could not vet the content; nothing may be stored."""


async def screen_memory_content(
    content: str, *, tenant_id: str, goal_id: str | None = None
) -> str | None:
    """Return the content to store (possibly redacted), or None when blocked."""
    try:
        from app.guardrails_v2.engine import guardrails_engine
        from app.guardrails_v2.models import GuardrailLayer

        guardrails_engine.ensure_default_rules(tenant_id)
        result = await guardrails_engine.evaluate(
            content=content[:_SCREEN_WINDOW],
            layer=GuardrailLayer.MEMORY_WRITE,
            tenant_id=tenant_id,
            goal_id=goal_id or None,
        )
    except Exception as exc:
        raise MemoryScreeningError(
            f"memory-write guardrail could not vet the content: {type(exc).__name__}"
        ) from exc
    if result.get("blocked"):
        return None
    redacted = result.get("redacted_content")
    if isinstance(redacted, str) and redacted != content[:_SCREEN_WINDOW]:
        return redacted
    return content[:_SCREEN_WINDOW]


__all__ = ["MemoryScreeningError", "screen_memory_content"]
