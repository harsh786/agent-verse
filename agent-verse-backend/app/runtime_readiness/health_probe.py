"""Real dependency health for the goal ReadinessGate.

The gate used to be fed ``DependencyHealth.all_healthy()`` — a constant — so it could
never block. This module derives each dependency's status from what the app actually has:

* ``postgres`` / ``redis`` — the app's :class:`~app.observability.health.HealthRegistry`
  checks (registered by ``ConnectionPools`` when pools are managed), run with a timeout.
  A dependency this deployment does not configure is ``UNKNOWN`` (not part of it), never
  ``UNAVAILABLE`` — only a configured dependency that fails its probe blocks.
* ``llm_provider`` — a tenant BYOK config, the app-wide resolved provider, or an env key.
  None of those in production is ``UNAVAILABLE`` (a goal would otherwise be simulated or
  fail mid-run); in development it is ``DEGRADED`` (the FakeProvider runs, flagged).
* ``embedder`` — the app's embedder, when wired.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.observability.logging import get_logger
from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus

_logger = get_logger(__name__)

_PROBE_TIMEOUT_S = 1.0


def _state(app_state: Any) -> Any:
    try:
        from starlette.applications import Starlette

        if isinstance(app_state, Starlette):
            return app_state.state
    except Exception:  # pragma: no cover - starlette is a hard dependency
        pass
    return app_state


async def _registry_status(registry: Any, name: str, timeout: float) -> DepStatus:
    checks = getattr(registry, "checks", None)
    if not isinstance(checks, list):
        return DepStatus.UNKNOWN
    matching = [check for check in checks if getattr(check, "name", None) == name]
    if not matching:
        return DepStatus.UNKNOWN
    for check in matching:
        try:
            await asyncio.wait_for(check.check(), timeout=timeout)
        except Exception as exc:
            _logger.warning(
                "readiness_dependency_down",
                dependency=name,
                error_type=type(exc).__name__,
            )
            return DepStatus.UNAVAILABLE
    return DepStatus.HEALTHY


def _llm_status(state: Any, *, tenant_llm_configured: bool) -> DepStatus:
    if tenant_llm_configured:
        return DepStatus.HEALTHY
    from app.providers.fake import FakeProvider

    provider = getattr(state, "_app_provider", None) if state is not None else None
    if provider is not None and not isinstance(provider, FakeProvider):
        return DepStatus.HEALTHY
    from app.core.config import get_provider_env

    if get_provider_env("ANTHROPIC_API_KEY") or get_provider_env("OPENAI_API_KEY"):
        return DepStatus.HEALTHY
    from app.providers.llm_resolution import fake_llm_allowed

    # BYOK-3: only development/test may run on the canned FakeProvider; any other
    # environment without a key has no LLM at all (it was only "production").
    if not fake_llm_allowed():
        return DepStatus.UNAVAILABLE
    return DepStatus.DEGRADED


def _embedder_status(state: Any) -> DepStatus:
    embedder = getattr(state, "embedder", None) if state is not None else None
    return DepStatus.HEALTHY if embedder is not None else DepStatus.UNKNOWN


async def collect_dependency_health(
    app_state: Any,
    *,
    tenant_llm_configured: bool = False,
    timeout: float = _PROBE_TIMEOUT_S,
) -> DependencyHealth:
    """Probe what this deployment actually depends on (see module docstring)."""
    state = _state(app_state)
    registry = getattr(state, "health", None) if state is not None else None
    postgres, redis = await asyncio.gather(
        _registry_status(registry, "postgres", timeout),
        _registry_status(registry, "redis", timeout),
    )
    return DependencyHealth(
        postgres=postgres,
        redis=redis,
        embedder=_embedder_status(state),
        llm_provider=_llm_status(state, tenant_llm_configured=tenant_llm_configured),
    )


__all__ = ["collect_dependency_health"]
