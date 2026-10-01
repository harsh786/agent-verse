"""VOYAGER-PERSIST: voyager runs through the StrategyRunner with its gates.

* admitted (it used to be ``strategy_execution_not_implemented``);
* a fake-provider run publishes one validated skill (with the goal's
  curriculum as provenance) into the injected persistent library;
* no persistent library -> the run fails closed (nothing published);
* budget: every LLM call is charged; a denied spend stops the run;
* HITL: a high-risk goal needs a persisted approval (no gateway / rejection
  -> failed, approval -> runs).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.governance.hitl import ApprovalStatus
from app.orchestration.strategy_context_store import StrategyGoalContext, StrategyGoalContextStore
from app.orchestration.strategy_contracts import (
    ExecutionTerminalState,
    PatternLimits,
    StrategyExecutionRequest,
)
from app.orchestration.strategy_executor import (
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="tenant-v", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_PLAN = '{"steps": [{"id": "s1", "summary": "collect facts"}, {"id": "s2", "summary": "write"}]}'


def _request() -> StrategyExecutionRequest:
    return StrategyExecutionRequest.model_validate(
        {
            "tenant_id": CTX.tenant_id,
            "goal_id": "goal-v1",
            "strategy_id": "voyager",
            "adapter_version": "1.0.0",
            "state_schema_version": 1,
            "agent_id": "agent-1",
            "runtime_profile_ref": "profile-1",
            "context_snapshot_ref": "context-v",
            "policy_ref": "policy-v1",
            "budget_ref": "budget-1",
            "cancellation_token": "goal-v1",
            "deadline": datetime.now(UTC) + timedelta(minutes=1),
            "idempotency_key": "idem-v1",
        }
    )


def _limits() -> PatternLimits:
    return PatternLimits.model_validate(
        {
            "calls": 32, "nodes": 32, "edges": 32, "depth": 8, "fan_out": 8,
            "rounds": 16, "tokens": 20_000, "duration_seconds": 30, "cost_usd": 5.0,
        }
    )


class _Library:
    def __init__(self) -> None:
        self.published: list[tuple[Any, dict[str, Any]]] = []

    async def publish(self, skill: Any, *, provenance: Any = None, **ctx: Any) -> Any:
        from app.memory.procedural_validator import validate_procedure

        validate_procedure(skill, tenant_id=skill.tenant_id, **ctx)
        self.published.append((skill, dict(provenance or {})))
        return skill


class _Gateway:
    def __init__(self, status: ApprovalStatus) -> None:
        self.status = status
        self.requests: list[dict[str, Any]] = []

    async def request_approval_async(self, **kwargs: Any) -> str:
        self.requests.append(kwargs)
        return "req-1"

    async def wait_for_approval(self, request_id: str, **_: Any) -> ApprovalStatus:
        return self.status


class _Budget:
    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.charges = 0

    async def check_and_record(self, **_: Any) -> bool:
        self.charges += 1
        return self.allow


async def _run(
    goal: str,
    responses: list[str],
    *,
    library: Any = None,
    gateway: Any = None,
    budget: Any = None,
) -> Any:
    store = StrategyGoalContextStore()
    await store.put(
        "context-v",
        StrategyGoalContext(
            goal_text=goal, provider=FakeProvider(responses=responses), tenant_ctx=CTX
        ),
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(
            context_store=store,
            skill_store=library,
            hitl_gateway=gateway,
            cost_controller=budget,
        ),
        admission=default_distributed_admission,
    )
    return await runner.run(_request(), _limits())


_OK = [_PLAN, "fact: the sky is blue", "summary written", "Final: the sky is blue."]


def test_app_wires_the_postgres_library_once_the_db_exists() -> None:
    from types import SimpleNamespace

    from app.main import _voyager_skill_store
    from app.memory.voyager_skills_pg import PostgresVoyagerSkillStore

    state = SimpleNamespace(db_session_factory=None)
    assert _voyager_skill_store(state) is None  # pre-lifespan: voyager refused
    state.db_session_factory = object()
    store = _voyager_skill_store(state)
    assert isinstance(store, PostgresVoyagerSkillStore)
    assert _voyager_skill_store(state) is store


def test_voyager_is_admitted() -> None:
    admitted, reason = default_distributed_admission(_request())
    assert admitted, reason


@pytest.mark.asyncio
async def test_fake_provider_run_publishes_one_skill() -> None:
    library = _Library()
    result = await _run("Explain why the sky is blue", _OK, library=library)
    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
    assert result.answer == "Final: the sky is blue."
    assert len(library.published) == 1
    skill, provenance = library.published[0]
    assert skill.tenant_id == CTX.tenant_id
    assert skill.procedure_id.startswith("voyager-")
    assert skill.policy_fingerprint == "policy-v1"
    assert provenance["goal_id"] == "goal-v1"
    assert provenance["steps"] == ["collect facts", "write"]


@pytest.mark.asyncio
async def test_without_a_persistent_library_the_run_fails_closed() -> None:
    result = await _run("Explain why the sky is blue", _OK, library=None)
    assert result.terminal_state is ExecutionTerminalState.FAILED


@pytest.mark.asyncio
async def test_denied_spend_stops_the_run_before_publishing() -> None:
    library, budget = _Library(), _Budget(allow=False)
    result = await _run("Explain why the sky is blue", _OK, library=library, budget=budget)
    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert budget.charges >= 1
    assert library.published == []


@pytest.mark.asyncio
async def test_high_risk_goal_needs_an_approval_gateway() -> None:
    library = _Library()
    result = await _run("Delete the production database backups", _OK, library=library)
    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert library.published == []


@pytest.mark.asyncio
async def test_rejected_approval_stops_the_run() -> None:
    library, gateway = _Library(), _Gateway(ApprovalStatus.REJECTED)
    result = await _run(
        "Delete the production database backups", _OK, library=library, gateway=gateway
    )
    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert gateway.requests and gateway.requests[0]["require_persisted"] is True
    assert library.published == []


@pytest.mark.asyncio
async def test_approved_high_risk_goal_runs() -> None:
    library, gateway = _Library(), _Gateway(ApprovalStatus.APPROVED)
    result = await _run(
        "Delete the production database backups", _OK, library=library, gateway=gateway
    )
    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
    assert len(gateway.requests) == 1
    assert len(library.published) == 1
