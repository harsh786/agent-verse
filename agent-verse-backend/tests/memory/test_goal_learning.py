"""Terminal goals are learned from; recalled memories get effectiveness feedback."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.memory.goal_learning import (
    RECALLED_MEMORY_IDS_KEY,
    derive_goal_lesson,
    learn_from_goal_outcome,
)
from app.memory.reflexion import ReflexionService
from app.memory.repository import InMemoryMemoryRepository
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="gl-tenant", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _service() -> tuple[ReflexionService, InMemoryMemoryRepository]:
    repo = InMemoryMemoryRepository()
    return ReflexionService(repository=repo), repo


def _state(status: GoalStatus, goal: str = "Summarise the weekly sales report") -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=T, goal_id="g-1")
    state.status = status
    state.iterations = 2
    return state


def _completed_state() -> AgentState:
    state = _state(GoalStatus.COMPLETE)
    state.steps = [
        StepResult(
            step_id="s1",
            description="Fetch the sales report",
            status=StepStatus.COMPLETE,
            tool_calls=[{"tool": "drive_read"}],
        ),
        StepResult(step_id="s2", description="Summarise totals", status=StepStatus.COMPLETE),
    ]
    return state


async def _learn(service: Any, state: Any, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "tenant_id": T.tenant_id,
        "goal_id": "g-1",
        "dry_run": False,
        "agent_id": "agent-1",
    }
    kwargs.update(overrides)
    return await learn_from_goal_outcome(service, state, **kwargs)


async def test_completed_goal_writes_an_evidence_backed_active_lesson() -> None:
    service, repo = _service()
    result = await _learn(service, _completed_state())

    assert result.status == "learned" and result.reason == "complete"
    records = await repo.list_records(T.tenant_id)
    assert len(records) == 1
    record = records[0]
    assert record.memory_id == result.memory_id
    assert record.memory_kind == "reflexion"
    assert record.lifecycle_state == "active"
    assert record.source_goal_id == "g-1"
    assert record.agent_id == "agent-1" and record.source == "goal_outcome"
    assert "goal://g-1/outcome/complete" in record.evidence_refs
    assert "goal://g-1/step/s1" in record.evidence_refs
    assert "Fetch the sales report" in record.safe_summary
    assert "drive_read" in record.safe_summary


async def test_learning_is_idempotent_per_goal_outcome() -> None:
    service, repo = _service()
    first = await _learn(service, _completed_state())
    second = await _learn(service, _completed_state())
    assert first.memory_id == second.memory_id
    assert len(await repo.list_records(T.tenant_id)) == 1


async def test_failed_goal_uses_the_llm_verifier_feedback_as_the_lesson() -> None:
    service, repo = _service()
    state = _state(GoalStatus.FAILED)
    state.verification_feedback = "The report API returned 404; the file id was stale"
    state.steps = [
        StepResult(
            step_id="s9", description="Read report", status=StepStatus.FAILED, error="404"
        )
    ]
    result = await _learn(service, state)

    assert result.status == "learned" and result.reason == "failed"
    record = (await repo.list_records(T.tenant_id))[0]
    assert "failed (context_gap)" in record.safe_summary
    assert "file id was stale" in record.safe_summary
    assert "goal://g-1/step/s9" in record.evidence_refs


async def test_failed_goal_without_llm_feedback_stores_deterministic_outcome() -> None:
    service, repo = _service()
    state = _state(GoalStatus.FAILED)
    state.error_message = "Planning unavailable: provider timeout"
    result = await _learn(service, state)

    assert result.status == "learned"
    record = (await repo.list_records(T.tenant_id))[0]
    assert record.lifecycle_state == "active"
    assert "failed (timeout)" in record.safe_summary
    assert "Planning unavailable" in record.safe_summary


async def test_dry_run_writes_nothing() -> None:
    service, repo = _service()
    state = _completed_state()
    state.context[RECALLED_MEMORY_IDS_KEY] = ["some-memory"]
    result = await _learn(service, state, dry_run=True)
    assert result.status == "skipped" and result.reason == "dry_run"
    assert await repo.list_records(T.tenant_id) == ()


async def test_non_terminal_goal_is_not_learned() -> None:
    service, repo = _service()
    result = await _learn(service, _state(GoalStatus.WAITING_HUMAN))
    assert result.status == "skipped"
    assert await repo.list_records(T.tenant_id) == ()


async def test_later_goal_recalls_the_lesson_and_feedback_updates_it() -> None:
    service, repo = _service()
    first = await _learn(service, _completed_state())

    recalled = await service.recall(
        tenant_id=T.tenant_id,
        query="Summarise the weekly sales report",
        allowed_data_classes=frozenset({"internal"}),
        agent_id="agent-1",
    )
    assert [r.memory_id for r in recalled] == [first.memory_id]

    later = _completed_state()
    later.goal_id = "g-2"
    later.context[RECALLED_MEMORY_IDS_KEY] = [first.memory_id]
    result = await _learn(service, later, goal_id="g-2")

    assert result.feedback_memory_ids == (first.memory_id,)
    updated = next(
        r for r in await repo.list_records(T.tenant_id) if r.memory_id == first.memory_id
    )
    assert updated.helpful_count == 1 and updated.recall_count == 1
    assert updated.effectiveness_score > 0


async def test_failed_goal_lowers_outcome_but_never_quarantines_the_memory() -> None:
    service, repo = _service()
    first = await _learn(service, _completed_state())
    failed = _state(GoalStatus.FAILED)
    failed.goal_id = "g-3"
    failed.error_message = "boom"
    failed.context[RECALLED_MEMORY_IDS_KEY] = [first.memory_id, "vanished-memory"]
    result = await _learn(service, failed, goal_id="g-3")

    assert result.feedback_memory_ids == (first.memory_id,)  # unknown id skipped
    updated = next(
        r for r in await repo.list_records(T.tenant_id) if r.memory_id == first.memory_id
    )
    assert updated.harmful_count == 0 and updated.helpful_count == 0
    assert updated.outcome_score < 0
    assert updated.lifecycle_state == "active"


async def test_pii_in_the_lesson_is_blocked_by_the_memory_write_guardrail() -> None:
    service, repo = _service()
    state = _state(GoalStatus.COMPLETE, goal="Email the invoice to john.doe@example.com")
    result = await _learn(service, state)
    assert result.status == "skipped" and result.reason == "blocked_by_guardrail"
    assert await repo.list_records(T.tenant_id) == ()


async def test_unvettable_content_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.memory.screening as screening

    class _Broken:
        def ensure_default_rules(self, tenant_id: str) -> int:
            raise RuntimeError("guardrail store down")

    import app.guardrails_v2.engine as engine_mod

    monkeypatch.setattr(engine_mod, "guardrails_engine", _Broken())
    service, repo = _service()
    result = await _learn(service, _completed_state())
    assert result.status == "failed" and result.reason == "screening_unavailable"
    assert await repo.list_records(T.tenant_id) == ()
    assert screening.MemoryScreeningError is not None


async def test_learning_failure_is_reported_not_raised() -> None:
    class _Failing:
        async def learn(self, **_: Any) -> Any:
            raise RuntimeError("db down")

        async def record_effectiveness(self, **_: Any) -> Any:
            raise AssertionError("no recalled memories here")

    result = await _learn(_Failing(), _completed_state())
    assert result.status == "failed" and result.reason == "RuntimeError"


async def test_learning_is_bounded_by_a_timeout() -> None:
    class _Slow:
        async def learn(self, **_: Any) -> Any:
            await asyncio.sleep(5)

    result = await _learn(_Slow(), _completed_state(), timeout_s=0.05)
    assert result.status == "failed" and result.reason == "timeout"


async def test_tenant_mismatch_fails_closed() -> None:
    service, repo = _service()
    result = await _learn(service, _completed_state(), tenant_id="another-tenant")
    assert result.status == "failed" and result.reason == "tenant_mismatch"
    assert await repo.list_records("another-tenant") == ()


async def test_no_service_is_a_logged_skip() -> None:
    result = await _learn(None, _completed_state())
    assert result.status == "skipped" and result.reason == "no_reflexion_service"


def test_lesson_derivation_is_none_for_non_terminal_states() -> None:
    assert derive_goal_lesson(_state(GoalStatus.EXECUTING), goal_id="g") is None
