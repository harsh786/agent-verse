"""Charge, batch and meter embedding spend — one helper for every embed path.

Embedding requests cost money. Each path used to decide on its own whether to
reserve budget, how big a request to send and whether to record usage, and
several did none of it (orchestrated collection ingestion, ``POST
/embeddings/embed``, whole-collection re-embeds). :func:`embed_metered` is the
single loop they share:

* texts go out in bounded batches (:data:`EMBED_BATCH_SIZE`), never one
  unbounded request per document;
* every batch is reserved against the tenant's budget BEFORE the provider is
  called — a refusal raises :class:`EmbeddingBudgetExceededError` (a
  ``DecisionBudgetExceededError``, so the API answers 429), an unknown budget
  raises :class:`EmbeddingBudgetUnverifiableError` (fail closed, 503);
* every embedded batch is recorded with :func:`record_embedding_usage`.

The controller is the caller's (``request.app.state``) or, when not passed, the
process-wide one the API lifespan / Celery worker registers
(:func:`app.providers.guarded_completion.platform_cost_controller`). With no
controller anywhere (bare library / unit-test use) batches are still bounded
and metered, just not reserved.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.providers.guarded_completion import DecisionBudgetExceededError

# Texts per embedding request.
EMBED_BATCH_SIZE = 64
# Reserved against the tenant budget per embedding request — the same per-call
# estimate the RAG cost guard reserves for a retrieval embedding.
EMBED_BATCH_COST_USD = 0.0001


class EmbeddingBudgetExceededError(DecisionBudgetExceededError):
    """The tenant's budget refused an embedding request (HTTP 429)."""


class EmbeddingBudgetUnverifiableError(RuntimeError):
    """The budget could not be checked, so the embedding was refused (HTTP 503)."""


def resolve_cost_controller(state: Any = None) -> Any:
    """``state``'s cost controller (Redis-backed first), else the process one."""
    if state is not None:
        controller = getattr(state, "redis_cost_controller", None) or getattr(
            state, "cost_controller", None
        )
        if controller is not None:
            return controller
    from app.providers.guarded_completion import platform_cost_controller

    try:
        return platform_cost_controller()
    except DecisionBudgetExceededError as exc:
        raise EmbeddingBudgetUnverifiableError(str(exc)) from exc


async def charge_embedding_batch(
    controller: Any,
    *,
    tenant_ctx: Any,
    operation_id: str,
    batch_index: int,
    label: str = "embedding",
    cost_usd: float = EMBED_BATCH_COST_USD,
) -> None:
    """Reserve one embedding request against ``tenant_ctx``'s budget.

    Idempotent per ``(operation_id, batch_index)`` for controllers that honour
    ``attempt_id`` (a retried batch is not charged twice).
    """
    if controller is None:
        return
    try:
        allowed = await controller.check_and_record(
            goal_id=f"{label}:{operation_id}",
            cost_usd=cost_usd,
            tenant_ctx=tenant_ctx,
            tool_name="knowledge_embedding",
            attempt_id=f"{label}:{operation_id}:{batch_index}",
        )
    except Exception as exc:
        raise EmbeddingBudgetUnverifiableError(
            f"embedding budget could not be verified: {type(exc).__name__}"
        ) from exc
    if not allowed:
        raise EmbeddingBudgetExceededError("tenant embedding budget exhausted")


async def embed_metered(
    texts: list[str],
    embed_batch: Callable[[list[str]], Awaitable[list[list[float]]]],
    *,
    tenant_ctx: Any,
    model: str,
    controller: Any = None,
    resolve_controller: bool = True,
    operation_id: str | None = None,
    label: str = "embedding",
    batch_size: int = EMBED_BATCH_SIZE,
) -> list[list[float]]:
    """Embed ``texts`` in charged, metered batches; returns one vector per text.

    ``embed_batch`` performs ONE provider request. Its errors propagate
    unchanged; a batch that returns the wrong number of vectors raises
    ``RuntimeError``. ``resolve_controller=False`` with ``controller=None``
    skips the reservation (for callers that already charged elsewhere).
    """
    if not texts:
        return []
    if controller is None and resolve_controller:
        controller = resolve_cost_controller()
    tenant_id = str(getattr(tenant_ctx, "tenant_id", "") or "")
    if controller is not None and not tenant_id:
        raise EmbeddingBudgetExceededError("no tenant to charge this embedding to")
    from app.embedding.usage import approx_tokens, record_embedding_usage

    op = operation_id or uuid.uuid4().hex
    size = max(1, int(batch_size))
    vectors: list[list[float]] = []
    for batch_index, start in enumerate(range(0, len(texts), size)):
        batch = texts[start : start + size]
        await charge_embedding_batch(
            controller,
            tenant_ctx=tenant_ctx,
            operation_id=op,
            batch_index=batch_index,
            label=label,
        )
        result = await embed_batch(batch)
        if len(result) != len(batch):
            raise RuntimeError("Embedding provider returned an incomplete batch")
        vectors.extend(result)
        if any(result):  # an unavailable provider's empty sentinels cost nothing
            await record_embedding_usage(tenant_id, model, approx_tokens(batch))
    return vectors
