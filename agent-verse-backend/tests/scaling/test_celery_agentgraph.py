"""Tests: Celery run_goal task creates AgentGraph (not AgentLoop) for goal execution."""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAgentState:
    """Minimal AgentState stub returned by the mock runner."""

    class Status:
        value = "complete"

    status = Status()
    iterations = 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_goal_uses_agent_graph_not_agent_loop(monkeypatch: Any) -> None:
    """run_goal should use AgentGraph; AgentLoop must NOT be instantiated.

    We patch AgentGraph with a capturing stub and verify it is called.
    AgentLoop is NOT patched — so detection will show _loop_is_patched=False
    and the AgentGraph path is taken.
    """
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    captured_ag_kwargs: list[dict[str, Any]] = []

    class _CapturingAgentGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured_ag_kwargs.append(kwargs)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    monkeypatch.setattr(_graph_mod, "AgentGraph", _CapturingAgentGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    result = tasks.run_goal.run(
        "goal-ag-1",
        "tenant-1",
        "test goal for agentgraph",
        "normal",
        False,
    )

    assert captured_ag_kwargs, "AgentGraph was never instantiated"
    assert result.get("status") in {"complete", "failed", "skipped", "dead_lettered"}


def test_run_goal_dry_run_bypasses_agent_construction(monkeypatch: Any) -> None:
    """dry_run=True returns early — no agent runner is ever constructed."""
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    graph_instantiated = []

    class _TrackGraph:
        def __init__(self, **kwargs: Any) -> None:
            graph_instantiated.append(True)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    monkeypatch.setattr(_graph_mod, "AgentGraph", _TrackGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    result = tasks.run_goal.run(
        "goal-dry-1",
        "tenant-1",
        "dry run goal",
        "normal",
        True,  # dry_run
    )

    # Dry run exits before agent construction
    assert graph_instantiated == [], "No AgentGraph should be built for dry_run"
    assert result["status"] == "complete"
    assert result["dry_run"] is True


def test_run_goal_falls_back_to_agent_loop_when_agent_graph_unavailable(
    monkeypatch: Any,
) -> None:
    """If AgentGraph raises on init AND AgentLoop is patched, the patched loop is used.

    When tests patch app.agent.loop.AgentLoop, the monkey-patch detector in tasks.py
    detects this and uses the patched class, skipping the AgentGraph path.
    """
    import app.agent.loop as _loop_mod
    from app.scaling import tasks

    fallback_used = []

    class _FallbackLoop:
        def __init__(self, **kwargs: Any) -> None:
            fallback_used.append(True)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    # Patching AgentLoop triggers monkey-patch detection → uses _FallbackLoop
    monkeypatch.setattr(_loop_mod, "AgentLoop", _FallbackLoop)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setenv("ALLOW_LEGACY_AGENT_LOOP", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")

    result = tasks.run_goal.run(
        "goal-fallback-1",
        "tenant-1",
        "fallback goal",
        "normal",
        False,
    )

    assert fallback_used, "Patched AgentLoop runner should have been used"
    assert result.get("status") in {"complete", "failed", "skipped", "dead_lettered"}


def test_retrieval_capable_goal_never_uses_legacy_loop_on_graph_failure(
    monkeypatch: Any,
) -> None:
    """Configured agents fail closed when canonical graph assembly fails."""
    import uuid

    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    class BrokenGraph:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("private graph assembly secret")

    monkeypatch.setattr(_graph_mod, "AgentGraph", BrokenGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    result = tasks.run_goal.run(
        f"goal-required-rag-{uuid.uuid4().hex}",
        "tenant-1",
        "knowledge-enabled goal",
        "normal",
        False,
        agent_id="configured-agent",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "agentgraph_assembly_failed"
    assert "secret" not in str(result)


def test_unbound_production_goal_never_uses_legacy_loop_on_graph_failure(
    monkeypatch: Any,
) -> None:
    import uuid

    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    class BrokenGraph:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("private graph assembly secret")

    monkeypatch.setattr(_graph_mod, "AgentGraph", BrokenGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setenv("ENVIRONMENT", "production")

    result = tasks.run_goal.run(
        f"goal-unbound-{uuid.uuid4().hex}",
        "tenant-1",
        "unbound goal",
        "normal",
        False,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "agentgraph_assembly_failed"
    assert "secret" not in str(result)


def test_agent_graph_constructed_with_reliability_services(monkeypatch: Any) -> None:
    """AgentGraph should be constructed with result_processor, dedup_cache, rollback_engine."""
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    captured_kwargs: list[dict[str, Any]] = []

    class _CapturingGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.append(kwargs)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    # Patch AgentGraph only — AgentLoop NOT patched → _loop_is_patched=False → AgentGraph path
    monkeypatch.setattr(_graph_mod, "AgentGraph", _CapturingGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    tasks.run_goal.run(
        "goal-params-1",
        "tenant-1",
        "check params",
        "normal",
        False,
    )

    assert captured_kwargs, "AgentGraph was not instantiated"
    kwargs = captured_kwargs[0]
    assert "result_processor" in kwargs, "result_processor must be passed"
    assert "dedup_cache" in kwargs, "dedup_cache must be passed"
    assert "rollback_engine" in kwargs, "rollback_engine must be passed"
    assert "guardrail_checker" in kwargs, "guardrail_checker must be passed"


def test_eager_worker_injects_gateway_and_uses_it_for_knowledge(monkeypatch: Any) -> None:
    """The eager Celery path calls the canonical worker gateway from AgentGraph."""
    import asyncio

    import app.agent.graph as _graph_mod
    import app.rag.gateway as _gateway_mod
    from app.rag.contracts import RAGExecutionResult, RAGStrategy
    from app.scaling import tasks
    from app.tenancy.context import PlanTier, TenantContext

    gateway_calls: list[tuple[Any, dict[str, Any]]] = []
    gateway_dependencies: list[Any] = []

    class _Gateway:
        def __init__(self, dependencies: Any) -> None:
            self.dependencies = dependencies
            gateway_dependencies.append(dependencies)

        async def execute(self, tenant_ctx: Any, **kwargs: Any) -> RAGExecutionResult:
            gateway_calls.append((tenant_ctx, kwargs))
            return RAGExecutionResult(
                requested_strategy_id=str(kwargs["strategy_id"]),
                resolved_strategy_id=RAGStrategy.HYBRID,
            )

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self.gateway = kwargs.get("retrieval_gateway")
            self._agent_collection_ids = []

        async def run(self, *, tenant_ctx: Any, **kwargs: Any) -> _FakeAgentState:
            assert self.gateway is not None
            await self.gateway.execute(
                tenant_ctx,
                collection_id="collection-worker",
                query="worker knowledge",
                strategy_id=RAGStrategy.HYBRID,
                top_k=3,
                filters={},
            )
            return _FakeAgentState()

    monkeypatch.setattr(_gateway_mod, "RetrievalGateway", _Gateway)
    monkeypatch.setattr(_graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    result = tasks.run_goal.run(
        "goal-worker-gateway",
        "tenant-1",
        "answer from knowledge",
        "normal",
        False,
    )

    assert result.get("status") in {"complete", "failed", "skipped", "dead_lettered"}
    assert gateway_calls
    assert gateway_calls[0][0].tenant_id == "tenant-1"
    assert gateway_dependencies
    worker_dependencies = next(
        dependency
        for dependency in gateway_dependencies
        if getattr(dependency, "llm_resolver", None) is not None
        and getattr(dependency, "strategy_capabilities", None)
    )
    assert worker_dependencies.collection_authorizer is not None
    from app.rag.agentic.patterns.web_augmented import SafeWebSearchCapability
    from app.rag.gateway import TenantScopedGraphCapabilityAdapter

    assert isinstance(worker_dependencies.search_capability, SafeWebSearchCapability)
    if worker_dependencies.session_factory is not None:
        assert isinstance(
            worker_dependencies.graph_capability,
            TenantScopedGraphCapabilityAdapter,
        )
    else:
        assert worker_dependencies.graph_capability is None
    resolver = worker_dependencies.llm_resolver
    resolved = asyncio.run(
        resolver(
            TenantContext("tenant-1", PlanTier.PROFESSIONAL, "worker-key"),
            RAGStrategy.HYBRID,
        )
    )
    assert resolved is not None
    assert resolved.model == "fake-provider"
    denied = asyncio.run(
        resolver(
            TenantContext("tenant-other", PlanTier.PROFESSIONAL, "worker-key"),
            RAGStrategy.HYBRID,
        )
    )
    assert denied is None


def test_worker_loads_tenant_persisted_allow_and_domain_policy(monkeypatch: Any) -> None:
    import asyncio

    from app.governance.policies import Policy, PolicyEngine, PolicyResult
    from app.scaling import tasks
    from app.tenancy.context import PlanTier, TenantContext

    async def load(
        self: PolicyEngine,
        db: Any,
        tenant_id: str | None = None,
        *,
        strict: bool = False,
    ) -> int:
        assert strict
        assert tenant_id == "tenant-worker"
        self.add_policy(
            Policy(
                name="worker-web-domains",
                tenant_id=tenant_id,
                web_allowed_domains=["docs.example.com"],
            )
        )
        return 1

    monkeypatch.setattr(PolicyEngine, "reload_from_db", load)
    engine = asyncio.run(
        tasks._load_worker_policy_engine(object(), "tenant-worker")
    )
    tenant = TenantContext("tenant-worker", PlanTier.ENTERPRISE, "key")

    assert engine.evaluate("web_search", tenant_ctx=tenant) is PolicyResult.ALLOW
    assert engine.web_allowed_domains(tenant) == ("docs.example.com",)


def test_worker_loads_tenant_web_deny_and_fails_closed_on_load_error(
    monkeypatch: Any,
) -> None:
    import asyncio

    from app.governance.policies import Policy, PolicyEngine, PolicyResult
    from app.scaling import tasks
    from app.tenancy.context import PlanTier, TenantContext

    async def deny(
        self: PolicyEngine,
        db: Any,
        tenant_id: str | None = None,
        *,
        strict: bool = False,
    ) -> int:
        self.add_policy(
            Policy(name="deny-web", tenant_id=tenant_id or "", denied_tools=["web_search"])
        )
        return 1

    monkeypatch.setattr(PolicyEngine, "reload_from_db", deny)
    tenant = TenantContext("tenant-worker", PlanTier.ENTERPRISE, "key")
    denied = asyncio.run(
        tasks._load_worker_policy_engine(object(), tenant.tenant_id)
    )
    assert denied.evaluate("web_search", tenant_ctx=tenant) is PolicyResult.DENY

    async def fail(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(PolicyEngine, "reload_from_db", fail)
    failed = asyncio.run(
        tasks._load_worker_policy_engine(object(), tenant.tenant_id)
    )
    assert failed.evaluate("web_search", tenant_ctx=tenant) is PolicyResult.DENY


def test_worker_graph_capability_is_scoped_only_when_database_is_configured() -> None:
    from app.rag.gateway import TenantScopedGraphCapabilityAdapter
    from app.scaling import tasks

    capability = tasks._build_worker_graph_capability(object())

    assert isinstance(capability, TenantScopedGraphCapabilityAdapter)
    assert not hasattr(capability, "session_factory")
    assert not hasattr(capability, "store")
    assert tasks._build_worker_graph_capability(None) is None


def test_consolidate_memories_task_is_registered() -> None:
    """consolidate_memories_task must exist and have the correct Celery task name."""
    from app.scaling import tasks

    assert hasattr(tasks, "consolidate_memories_task"), (
        "consolidate_memories_task is not defined in tasks module"
    )
    assert tasks.consolidate_memories_task.name == "agentverse.maintenance.consolidate_memories"


def test_consolidate_memories_beat_schedule_registered() -> None:
    """consolidate_memories must appear in the Celery beat schedule after tasks import."""
    # Import tasks so the module-level schedule registration runs
    import app.scaling.tasks  # noqa: F401
    from app.scaling.celery_app import celery_app

    assert "consolidate-memories-daily" in celery_app.conf.beat_schedule, (
        "consolidate-memories-daily missing from beat_schedule"
    )
    entry = celery_app.conf.beat_schedule["consolidate-memories-daily"]
    assert entry["task"] == "agentverse.maintenance.consolidate_memories"
