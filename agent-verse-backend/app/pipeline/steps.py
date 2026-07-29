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
from app.rag.contracts import RAGStrategy
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
    return await controller.check_and_record(
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
    return memory.recall(goal_hint=goal, tenant_ctx=tenant_ctx)


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
    goal: str = "",
    step: str,
    tenant_ctx: TenantContext,
    retrieval_gateway: Any = None,
    collection_ids: list[str] | None = None,
    strategy: RAGStrategy = RAGStrategy.HYBRID,
    top_k: int = 3,
    filters: dict[str, Any] | None = None,
    execution_id: str = "",
) -> str:
    """Fetch per-step context only through the tenant-aware retrieval gateway."""

    if not collection_ids:
        return ""
    if retrieval_gateway is None:
        raise RuntimeError("Retrieval gateway is not configured")
    if not isinstance(strategy, RAGStrategy):
        raise TypeError("strategy must be a canonical RAGStrategy")

    citations = []
    query_text = step or goal
    for collection_id in collection_ids[:3]:
        execute_kwargs: dict[str, Any] = {
            "collection_id": collection_id,
            "query": query_text,
            "strategy_id": strategy,
            "top_k": top_k,
            "filters": filters or {},
        }
        if execution_id:
            execute_kwargs["execution_id"] = execution_id
        result = await retrieval_gateway.execute(tenant_ctx, **execute_kwargs)
        citations.extend(result.citations)
    citations.sort(key=lambda citation: (-citation.score, citation.citation_id))
    return "\n".join(
        f"[Context {index} (score={citation.score:.2f})]: {citation.content[:300]}"
        for index, citation in enumerate(citations[:top_k], start=1)
    )
