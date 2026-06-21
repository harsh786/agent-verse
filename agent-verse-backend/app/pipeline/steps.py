"""12-step tool-call pipeline — stub implementations.

Real implementations replace these stubs as later phases land:
  Phase 6: cost_check, governance_check, hitl_gate, record_usage
  Phase 7: circuit_breaker, rollback_record, dedup_check, result_processor
  Phase 5: exec_memory_lookup
  Phase 3 (this file): wiring only, all stubs permissive

Each step is an async callable so the executor can await them uniformly and
later phases can drop-in replace them without touching the agent loop.
"""

from __future__ import annotations

from app.tenancy.context import TenantContext


async def cost_check(*, step: str, tenant_ctx: TenantContext) -> bool:
    """Return True if the step is within budget (stub: always True)."""
    return True


async def governance_check(*, tool_name: str, tenant_ctx: TenantContext) -> bool:
    """Return True if the tool is permitted by policy (stub: always True)."""
    return True


async def dedup_check(*, content_hash: str, tenant_ctx: TenantContext) -> bool:
    """Return True if this content is a duplicate (stub: always False = not duplicate)."""
    return False


async def circuit_breaker_check(*, tool_name: str, tenant_ctx: TenantContext) -> bool:
    """Return True if the circuit is open/blocked (stub: always False = circuit closed)."""
    return False


async def hitl_gate(
    *, action: str, risk_level: str, tenant_ctx: TenantContext
) -> bool:
    """Return True if human approval is required (stub: always False = auto-proceed)."""
    return False


async def record_usage(
    *, tool_name: str, tokens_used: int, tenant_ctx: TenantContext
) -> None:
    """Record token/cost usage for billing (stub: no-op)."""


async def exec_memory_lookup(
    *, goal: str, tenant_ctx: TenantContext
) -> list[dict[str, str]]:
    """Return relevant past execution memories (stub: empty list)."""
    return []


async def record_rollback_point(
    *, action: str, inverse_action: str, tenant_ctx: TenantContext
) -> str:
    """Register a rollback checkpoint, return checkpoint ID (stub: empty string)."""
    return ""


async def result_processor(*, raw_output: str, tenant_ctx: TenantContext) -> str:
    """Redact secrets, truncate, and normalize result (stub: pass-through)."""
    return raw_output


async def stream_step_event(
    *, event: dict[str, object], tenant_ctx: TenantContext
) -> None:
    """Publish a step event to SSE subscribers (stub: no-op)."""


async def smart_context_fetch(
    *, goal: str, step: str, tenant_ctx: TenantContext
) -> str:
    """Fetch and rank relevant context from RAG/memory (stub: empty string)."""
    return ""
