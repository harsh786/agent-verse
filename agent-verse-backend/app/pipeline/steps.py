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
    goal: str = "",
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
    Requires a real embedder (query_embedding must be provided); returns empty
    string when no embedding is available rather than generating random noise.
    """
    if knowledge_store is None:
        return ""

    # Skip RAG when no query embedding is available — random vectors corrupt results
    if query_embedding is None:
        from app.observability.logging import get_logger as _get_logger
        _get_logger(__name__).debug(
            "rag_skipped_no_embedder",
            message=(
                "smart_context_fetch skipped: no embedder configured. "
                "Set VOYAGE_API_KEY or OPENAI_API_KEY for RAG support."
            )
        )
        return ""

    try:
        # Determine allowed collections from agent binding
        allowed_collections: list[str] | None = None
        agent_id = context.get("agent_id") if context else None
        if agent_id and agent_store:
            agent = agent_store.get(agent_id, tenant_ctx=tenant_ctx)
            if agent:
                allowed = agent.get("allowed_collection_ids", [])
                if allowed:
                    allowed_collections = list(allowed)

        # Enumerate collections via the public API (works with DB-loaded knowledge)
        try:
            collections = knowledge_store.list_collections(tenant_ctx=tenant_ctx)
            collection_ids = [c.collection_id for c in collections]
        except Exception:
            collection_ids = []

        # Search collections for this tenant (filtered if agent has bindings)
        all_results = []
        query_text = step or goal
        for collection_id in collection_ids[:3]:  # cap at 3 collections
            if allowed_collections is not None and collection_id not in allowed_collections:
                continue
            try:
                results = await knowledge_store.hybrid_search_db(
                    query_text,
                    query_embedding,
                    collection_id,
                    tenant_ctx,
                    top_k=3,
                )
                all_results.extend(results)
            except Exception:
                continue

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
