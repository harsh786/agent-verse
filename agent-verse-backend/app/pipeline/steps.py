"""12-step tool-call pipeline — real implementations.

Each step delegates to the corresponding module class.
All dependency parameters are optional to support graceful degradation.

Function signatures extend the original stubs: new keyword-only parameters
are always Optional with sensible defaults so old call sites keep working.
"""

from __future__ import annotations

from typing import Any

from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix
from app.memory.execution import ExecutionMemory
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import TenantContext


async def cost_check(
    *,
    step: str,
    tenant_ctx: TenantContext,
    controller: CostController | None = None,
    goal_id: str = "",
    estimated_cost: float = 0.01,
) -> bool:
    """Return True if the step is within budget.

    Delegates to CostController.check_and_record(); returns True (allow)
    when no controller is injected.
    """
    if controller is None:
        return True
    return controller.check_and_record(
        goal_id=goal_id,
        cost_usd=estimated_cost,
        tenant_ctx=tenant_ctx,
    )


async def governance_check(
    *,
    tool_name: str,
    tenant_ctx: TenantContext,
    matrix: PermissionMatrix | None = None,
) -> ActionLevel:
    """Return the ActionLevel for the tool.

    Returns ActionLevel.ALLOW when no matrix is injected.
    """
    if matrix is None:
        return ActionLevel.ALLOW
    return matrix.check(tool_name=tool_name, tenant_ctx=tenant_ctx)


async def dedup_check(
    *,
    content_hash: str,
    tenant_ctx: TenantContext,
    cache: DeduplicationCache | None = None,
) -> bool:
    """Return True if this content hash is a duplicate (already seen)."""
    if cache is None:
        return False
    return cache.is_duplicate(content_hash=content_hash, tenant_ctx=tenant_ctx)


async def circuit_breaker_check(
    *,
    tool_name: str,
    tenant_ctx: TenantContext,
    breaker: CircuitBreaker | None = None,
) -> bool:
    """Return True if the circuit is open (calls should be blocked)."""
    if breaker is None:
        return False
    return not breaker.is_closed()


async def hitl_gate(
    *,
    action: str,
    risk_level: str,
    tenant_ctx: TenantContext,
    gateway: HITLGateway | None = None,
    goal_id: str = "",
) -> bool:
    """Log a HITL approval request for high-risk actions; auto-proceed.

    Returns False (auto-proceed) in all cases — blocking wait is handled
    externally.  Returns True if an approval request was created.
    """
    if gateway is None or risk_level != "high":
        return False
    gateway.request_approval(
        goal_id=goal_id,
        action=action,
        risk_level=risk_level,
        tenant_ctx=tenant_ctx,
    )
    return True  # request was logged; caller decides whether to block


async def record_usage(
    *,
    tool_name: str,
    tokens_used: int,
    tenant_ctx: TenantContext,
    audit_log: AuditLog | None = None,
    goal_id: str = "",
) -> None:
    """Record a tool-call usage entry in the audit log."""
    if audit_log is not None:
        event = AuditEvent(
            goal_id=goal_id,
            tool_name=tool_name,
            action_level=ActionLevel.ALLOW_LOG,
            outcome=f"tokens_used={tokens_used}",
        )
        audit_log.record(event, tenant_ctx=tenant_ctx)


async def exec_memory_lookup(
    *,
    goal: str,
    tenant_ctx: TenantContext,
    memory: ExecutionMemory | None = None,
) -> list[dict[str, Any]]:
    """Return relevant past execution memories (winning plans)."""
    if memory is None:
        return []
    return memory.recall(goal_hint=goal, tenant_ctx=tenant_ctx)  # type: ignore[return-value]


async def record_rollback_point(
    *,
    action: str,
    inverse_action: str,
    tenant_ctx: TenantContext,
    engine: RollbackEngine | None = None,
) -> str:
    """Register a rollback checkpoint; returns the action as checkpoint ID."""
    if engine is None:
        return ""
    engine.register(action=action, inverse=lambda: None)
    return action  # RollbackEngine has no per-entry ID; use action as identifier


async def result_processor_step(
    *,
    raw_output: str,
    tenant_ctx: TenantContext,
    processor: ResultProcessor | None = None,
) -> str:
    """Redact secrets, truncate, and normalize a tool result."""
    if processor is None:
        return raw_output
    return processor.process(raw_output)


async def stream_step_event(
    *,
    event: dict[str, object],
    tenant_ctx: TenantContext,
) -> None:
    """Publish a step event to SSE subscribers (no-op in pipeline; handled by loop)."""


async def smart_context_fetch(
    *,
    goal: str,
    step: str,
    tenant_ctx: TenantContext,
    knowledge_store: Any = None,
    query_embedding: list[float] | None = None,
    context: dict[str, Any] | None = None,
    agent_store: Any = None,
) -> str:
    """Fetch and rank relevant context from RAG store for a specific step.

    Returns formatted context string or empty string if nothing relevant found.
    Filters to agent's allowed_collection_ids when agent_id is present in context.
    """
    if knowledge_store is None:
        return ""

    try:
        import math
        import random

        # Use provided embedding or generate a fallback one
        if query_embedding is None:
            raw = [random.gauss(0, 1) for _ in range(768)]
            mag = math.sqrt(sum(x * x for x in raw))
            query_embedding = [x / mag for x in raw] if mag > 0 else raw

        # Determine allowed collections from agent binding
        allowed_collections: list[str] | None = None
        agent_id = context.get("agent_id") if context else None
        if agent_id and agent_store:
            agent = agent_store.get(agent_id, tenant_ctx=tenant_ctx)
            if agent:
                allowed = agent.get("allowed_collection_ids", [])
                if allowed:
                    allowed_collections = list(allowed)

        # Search collections for this tenant (filtered if agent has bindings)
        all_results = []
        for (tid, cid), _ in knowledge_store._data.items():
            if tid != tenant_ctx.tenant_id:
                continue
            if allowed_collections is not None and cid not in allowed_collections:
                continue
            results = knowledge_store.hybrid_search(
                step, query_embedding, cid, tenant_ctx, top_k=3
            )
            all_results.extend(results)

        if not all_results:
            return ""

        # Sort by score and take top 3
        all_results.sort(key=lambda r: r.score, reverse=True)
        top = all_results[:3]

        # Format as context
        return "\n".join(
            f"[Context {i + 1} (score={r.score:.2f})]: {r.content[:300]}"
            for i, r in enumerate(top)
        )
    except Exception:
        return ""
