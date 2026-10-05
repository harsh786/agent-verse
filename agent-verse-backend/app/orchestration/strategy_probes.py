"""Readiness probes for every dependency a registered strategy declares.

``ReadinessEvaluator()`` was constructed with no ``register()`` calls, so in production
every strategy reported ``missing_probe:*`` for every dependency — readiness said nothing
about the running system. Each probe here inspects what the app actually wires on
``app.state`` (read at probe time, so the lifespan's DB/Redis swap is picked up):

* ready     — the dependency is wired and (where it can be pinged) reachable;
* degraded  — wired but not production-grade (in-memory / simulated);
* not ready — not wired, not configured, or failing its ping.

A dependency with no runtime implementation in this app gets an explicit
``not_wired`` probe rather than being left unprobed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from app.orchestration.strategy_readiness import DependencyProbeResult, Probe, ReadinessEvaluator

# Dependency id -> app.state attribute holding its (repository / service) implementation.
_STATE_BACKED: dict[str, str] = {
    "transcript_store": "transcript_service",
    "progress_ledger": "progress_ledger_repository",
    "moa_repository": "moa_repository",
    "auction_repository": "auction_repository",
    "memory_repository": "memory_repository",
    "long_term_memory": "long_term_memory",
    "graph": "knowledge_graph_store",
    "web": "safe_web_search_capability",
    "raft_service": "raft_service",
    "artifact_store": "rpa_artifact_store",
    "policy_runtime": "policy_engine",
}

# Declared by strategies but not wired into this app's runtime at all.
_NOT_WIRED = (
    "code_interpreter",
    "production_sandbox",
    "coordination_outbox",
    "lease_store",
    "governed_tool_dispatcher",
    "reasoning_example_source",
    "colbert_checkpoint",
    "colbert_library",
    "raft_model",
)


def _is_in_memory(obj: Any) -> bool:
    for candidate in (obj, getattr(obj, "_repository", None), getattr(obj, "repository", None)):
        if candidate is not None and type(candidate).__name__.startswith("InMemory"):
            return True
    return False


def _state_backed_probe(state: Any, attribute: str) -> Probe:
    def probe() -> DependencyProbeResult:
        value = getattr(state, attribute, None)
        if value is None:
            return DependencyProbeResult.not_ready("not_wired")
        if _is_in_memory(value):
            return DependencyProbeResult.degraded("in_memory_not_durable")
        return DependencyProbeResult.ready()

    return probe


def _not_wired_probe() -> DependencyProbeResult:
    return DependencyProbeResult.not_ready("not_wired")


def _per_collection_probe() -> DependencyProbeResult:
    """``precomputed_index`` (RAPTOR / agentic chunking) exists per collection.

    Platform-wide readiness cannot judge it without a collection; the RAG gateway
    checks the collection's index on every request (P2-6) and refuses with
    ``collection_index_required`` / ``precomputed_index_missing``. Report
    degraded rather than claiming ready or not-wired.
    """
    return DependencyProbeResult.degraded("per_collection")


def _provider_probe(state: Any) -> Probe:
    def probe() -> DependencyProbeResult:
        from app.providers.fake import FakeProvider

        provider = getattr(state, "_app_provider", None)
        if provider is not None and not isinstance(provider, FakeProvider):
            return DependencyProbeResult.ready()
        if os.getenv("ENVIRONMENT", "development").lower() == "production":
            return DependencyProbeResult.not_ready("not_configured")
        return DependencyProbeResult.degraded("simulated_provider")

    return probe


def _embedder_probe(state: Any) -> Probe:
    def probe() -> DependencyProbeResult:
        embedder = getattr(state, "embedder", None)
        if embedder is None:
            return DependencyProbeResult.not_ready("not_configured")
        if "fake" in type(embedder).__name__.lower():
            return DependencyProbeResult.degraded("simulated_embedder")
        return DependencyProbeResult.ready()

    return probe


def _health_check_probe(state: Any, check_name: str) -> Probe:
    async def probe() -> DependencyProbeResult:
        registry = getattr(state, "health", None)
        checks = getattr(registry, "checks", None) or []
        matching = [check for check in checks if getattr(check, "name", None) == check_name]
        if not matching:
            return DependencyProbeResult.not_ready("not_configured")
        for check in matching:
            try:
                await check.check()
            except Exception:
                return DependencyProbeResult.not_ready("unreachable")
        return DependencyProbeResult.ready()

    return probe


def _strategy_runner_probe(state: Any) -> Probe:
    def probe() -> DependencyProbeResult:
        runner = getattr(state, "strategy_runner", None)
        if runner is None:
            return DependencyProbeResult.not_ready("not_wired")
        if getattr(runner, "has_real_executor", False) is not True:
            return DependencyProbeResult.degraded("inert_executor")
        return DependencyProbeResult.ready()

    return probe


def _checkpoint_probe(state: Any) -> Probe:
    def probe() -> DependencyProbeResult:
        saver = getattr(state, "langgraph_checkpointer", None)
        if saver is None or type(saver).__name__ in {"MemorySaver", "InMemorySaver"}:
            # AgentGraph falls back to an in-process MemorySaver: works, not durable.
            return DependencyProbeResult.degraded("in_memory_not_durable")
        return DependencyProbeResult.ready()

    return probe


def build_strategy_probes(state: Any) -> dict[str, Probe]:
    probes: dict[str, Probe] = {
        "strategy_runner": _strategy_runner_probe(state),
        "provider": _provider_probe(state),
        "embedder": _embedder_probe(state),
        "database": _health_check_probe(state, "postgres"),
        "redis": _health_check_probe(state, "redis"),
        "checkpoint_store": _checkpoint_probe(state),
    }
    for dependency_id, attribute in _STATE_BACKED.items():
        probes[dependency_id] = _state_backed_probe(state, attribute)
    probes["precomputed_index"] = _per_collection_probe
    not_wired: Callable[[], DependencyProbeResult] = _not_wired_probe
    for dependency_id in _NOT_WIRED:
        probes[dependency_id] = not_wired
    return probes


def register_strategy_readiness_probes(evaluator: ReadinessEvaluator, state: Any) -> None:
    """Register a probe for every dependency id strategies can declare."""
    for dependency_id, probe in build_strategy_probes(state).items():
        evaluator.register(dependency_id, probe)


__all__ = ["build_strategy_probes", "register_strategy_readiness_probes"]
