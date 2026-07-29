"""
Workflow Node Executors
=======================
Real execution semantics for workflow DAG nodes.

Supported node types:
  - action:   MCP tool call (already works in existing executor)
  - llm:      LLM completion (already works)
  - decision: LLM-or-expression condition → selects outgoing edge  (NEW)
  - loop:     Bounded iteration / map over list input              (NEW)
  - delay:    Durable timer via asyncio.sleep (or Celery ETA)     (NEW)
  - rag:      Collection picker + query + strategy                (NEW, uses engine.py)
  - skill:    Inject a skill's instructions into context           (NEW)
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from app.observability.logging import get_logger
from app.rag.contracts import RAGStrategy, resolve_rag_strategy
from app.tenancy.context import TenantContext

logger = get_logger(__name__)


async def execute_decision_node(
    node: dict[str, Any],
    context: dict[str, Any],
    *,
    llm_provider: Any = None,
) -> str:
    """
    Execute a decision node. Returns the edge label to follow.

    Node config:
      condition: str  — either a Python-safe expression or "llm" for LLM eval
      true_edge: str  — edge label when condition is True
      false_edge: str — edge label when condition is False
      options: list[str] — multi-way branch edge labels (for LLM classifier)

    Expression eval:
      condition = "{{context.score}} > 0.8"  → evaluate with context vars
    Expression safety: only simple comparisons and logical operators allowed.
    """
    condition = node.get("condition", "false")
    true_edge: str = str(node.get("true_edge", "yes"))
    false_edge: str = str(node.get("false_edge", "no"))
    options: list[str] = node.get("options", [true_edge, false_edge])

    # LLM-based decision
    if condition == "llm" and llm_provider is not None:
        prompt = node.get("prompt", "Based on the context, which path should we take?")
        context_summary = str(context)[:1000]

        from app.providers.base import CompletionRequest, Message

        req = CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        f"You are a decision node. Choose exactly one option from: {options}. "
                        f"Reply with ONLY the option name, nothing else."
                    ),
                ),
                Message(
                    role="user",
                    content=f"{prompt}\n\nContext: {context_summary}",
                ),
            ],
            model="",
        )
        try:
            resp = await llm_provider.complete(req)
            llm_chosen = resp.content.strip().lower()
            # Match to closest option
            for opt in options:
                if opt.lower() in llm_chosen or llm_chosen in opt.lower():
                    logger.info("decision_node_llm", chosen=opt)
                    return str(opt)
        except Exception as e:
            logger.warning("decision_node_llm_failed", error=str(e)[:60])
        return str(options[0]) if options else true_edge

    # Expression-based decision (safe eval)
    # Replace {{var}} with context values
    expr = condition
    for key, value in context.items():
        expr = expr.replace(f"{{{{{key}}}}}", str(value))

    # Safety check — two layers:
    # 1. Reject any dunder pattern (__import__, __class__, __builtins__, etc.)
    if "__" in expr:
        logger.warning("decision_node_dunder_blocked", expr=expr[:100])
        return false_edge

    # 2. Allowlist: only digits, whitespace, arithmetic/comparison/logical operators,
    #    quotes, parentheses, and simple identifiers (no brackets for subscript attacks).
    safe_pattern = re.compile(r'^[\d\s\.\+\-\*\/\<\>\=\!\&\|\(\)\'\"a-zA-Z_\.]*$')
    if not safe_pattern.match(
        expr.replace("context.", "").replace("True", "").replace("False", "")
    ):
        logger.warning("decision_node_unsafe_expr", expr=expr[:100])
        return false_edge

    try:
        result = bool(eval(expr, {"__builtins__": {}}, {"context": context}))
        edge: str = true_edge if result else false_edge
        logger.info("decision_node_expr", result=result, chosen=edge)
        return edge
    except Exception as e:
        logger.warning("decision_node_eval_failed", error=str(e)[:60])
        return false_edge


async def execute_loop_node(
    node: dict[str, Any],
    context: dict[str, Any],
    *,
    execute_subgraph: Any = None,
    max_iterations: int = 50,
) -> list[Any]:
    """
    Execute a loop node. Returns list of per-iteration outputs.

    Node config:
      items_key: str  — context key containing list to iterate over
      max_iter: int   — hard iteration cap (default 50)
      body_node_id: str — node to execute per iteration
    """
    items_key = node.get("items_key", "items")
    max_iter = min(node.get("max_iter", 10), max_iterations)

    items = context.get(items_key, [])
    if not isinstance(items, list):
        items = [items]

    results = []
    for i, item in enumerate(items[:max_iter]):
        iter_context = {**context, "item": item, "index": i}
        if execute_subgraph is not None:
            try:
                result = await execute_subgraph(iter_context)
                results.append(result)
            except Exception as e:
                logger.warning("loop_iteration_failed", index=i, error=str(e)[:60])
                results.append({"error": str(e)})
        else:
            results.append({"item": item, "index": i})

    logger.info("loop_node_complete", iterations=len(results))
    return results


async def execute_delay_node(
    node: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a delay node. Waits for the specified duration.

    Node config:
      seconds: int | float — delay in seconds (max 300 in non-production)
      reason: str          — human-readable reason for the delay
    """
    import os

    max_delay = 300 if os.getenv("ENVIRONMENT") != "production" else 3600
    delay = min(float(node.get("seconds", 1)), max_delay)
    reason = node.get("reason", "scheduled delay")

    logger.info("delay_node_start", seconds=delay, reason=reason)
    await asyncio.sleep(delay)
    logger.info("delay_node_complete", seconds=delay)
    return {"delayed_seconds": delay, "reason": reason}


async def execute_rag_node(
    node: dict[str, Any],
    context: dict[str, Any],
    *,
    retrieval_gateway: Any,
    tenant_ctx: TenantContext,
) -> dict[str, Any]:
    """
    Execute a RAG retrieval node.

    Node config:
      collection_id: str   — knowledge collection to query
      query_template: str  — query with {{var}} context substitution
      top_k: int           — number of chunks to retrieve
      strategy: str        — one canonical RAG strategy ID
    """
    collection_id = node.get("collection_id", "")
    query_template = node.get("query_template", "{{goal}}")
    top_k = min(int(node.get("top_k", 5)), 20)
    requested_strategy_id = str(
        node.get(
            "requested_strategy_id",
            node.get("strategy", RAGStrategy.HYBRID.value),
        )
    )
    resolve_rag_strategy(requested_strategy_id)
    filters = node.get("filters", {})
    if not isinstance(filters, dict):
        raise TypeError("RAG workflow filters must be an object")

    # Substitute context variables
    query = query_template
    for key, value in context.items():
        query = query.replace(f"{{{{{key}}}}}", str(value)[:200])

    if not collection_id:
        raise ValueError("RAG workflow collection_id is required")
    if retrieval_gateway is None:
        raise RuntimeError("Retrieval gateway is not configured")

    result = await retrieval_gateway.execute(
        tenant_ctx,
        collection_id=str(collection_id),
        query=query,
        strategy_id=requested_strategy_id,
        top_k=top_k,
        filters=filters,
    )
    citations = [citation.model_dump(mode="json") for citation in result.citations]
    chunks = [
        {
            "content": citation.content,
            "score": citation.score,
            "chunk_id": citation.chunk_id,
            "source": citation.source,
        }
        for citation in result.citations
    ]

    logger.info("rag_node_complete", chunks=len(chunks), collection=collection_id)
    return {
        "chunks": chunks,
        "query": query,
        "collection_id": collection_id,
        "context_text": "\n\n".join(str(c["content"]) for c in chunks[:top_k]),
        "citations": citations,
        "requested_strategy_id": result.requested_strategy_id,
        "resolved_strategy_id": result.resolved_strategy_id.value,
        "retrieval_legs": [leg.model_dump(mode="json") for leg in result.retrieval_legs],
        "strategy_trace": [trace.model_dump(mode="json") for trace in result.strategy_trace],
    }


async def execute_skill_node(
    node: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a skill node — injects skill instructions into workflow context.

    Node config:
      skill_id: str | goal: str — skill to select
    """
    skill_id = node.get("skill_id", "")
    goal = node.get("goal", context.get("goal", ""))

    from app.agent.skill_selector import PLATFORM_SKILLS, SkillSelector

    skills_to_inject: list[dict[str, Any]] = []

    if skill_id:
        # Specific skill by ID
        skill = next((s for s in PLATFORM_SKILLS if s["id"] == skill_id), None)
        if skill:
            skills_to_inject = [skill]
    elif goal:
        # Select by goal
        selector = SkillSelector()
        selected = selector.select(goal, max_skills=2)
        skills_to_inject = [
            {"name": s.name, "instructions": s.instructions, "allowed_tools": s.allowed_tools}
            for s in selected
        ]

    instructions = "\n\n".join(s.get("instructions", "") for s in skills_to_inject)

    logger.info(
        "skill_node_complete", skills=[s.get("name") for s in skills_to_inject]
    )
    return {
        "skill_instructions": instructions,
        "skills_loaded": [s.get("name") for s in skills_to_inject],
    }
