"""One place that assembles a RAFTService for the API, the worker and the poller.

The API (in-memory and DB-backed gateways), the Celery worker's retrieval
gateway and the beat status poller all need the same fine-tune providers *and*
the inference providers that serve their models — building it once here keeps
the three paths from drifting (both API constructions used to omit the inference
providers, so no trained model could ever answer).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger
from app.rag.raft import InMemoryRAFTRepository, RAFTRepository, RAFTService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings

logger = get_logger(__name__)


def build_raft_service(
    settings: Settings,
    *,
    session_factory: Callable[[], AsyncSession] | None = None,
    system_session_factory: Callable[[], AsyncSession] | None = None,
) -> RAFTService:
    """RAFTService with configured fine-tune + serving providers.

    With a ``session_factory`` state is durable (tenant RLS); without one it is
    process-local (tests / no database). ``system_session_factory`` is needed
    only by the cross-tenant status poller.
    """
    from app.rag.raft_inference import build_raft_inference_providers
    from app.rag.raft_openai_provider import build_raft_providers

    providers = build_raft_providers(settings)
    inference_providers = build_raft_inference_providers(settings, providers)
    repository: RAFTRepository
    if session_factory is not None:
        from app.rag.raft_repository import SQLRAFTRepository

        repository = SQLRAFTRepository(
            session_factory,
            system_session_factory=system_session_factory,
            chunk_page_size=settings.raft_chunk_page_size,
        )
    else:
        repository = InMemoryRAFTRepository()
    return RAFTService(
        repository=repository,
        providers=providers,
        inference_providers=inference_providers,
        max_training_chunks=settings.raft_max_training_chunks,
        max_eval_examples=settings.raft_max_eval_examples,
    )


async def poll_raft_jobs_once(
    *,
    service: RAFTService | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Advance one bounded batch of in-flight fine-tune jobs across all tenants."""
    from app.core.config import get_settings

    settings = get_settings()
    if service is None:
        from app.db.session import get_session_factory, get_system_session_factory

        service = build_raft_service(
            settings,
            session_factory=get_session_factory(),
            system_session_factory=get_system_session_factory(),
        )
    if not service.has_fine_tune_providers:
        return {"status": "skipped", "reason": "no_fine_tune_provider_configured"}
    summary = await service.poll_in_flight_jobs(limit=limit or settings.raft_poll_batch_size)
    if summary.scanned:
        logger.info("raft_jobs_polled", **summary.as_dict())
    return {"status": "ok", **summary.as_dict()}


__all__ = ["build_raft_service", "poll_raft_jobs_once"]
