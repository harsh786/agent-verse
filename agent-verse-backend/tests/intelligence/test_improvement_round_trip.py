"""D-3 hardening: optimizer → executor → observable-effect round-trip.

These tests prove the self-improvement components are REAL and callable in
isolation (no DB, no live graph wiring):

  * ``build_default_improvement_handlers`` returns handlers that produce a
    real, idempotent effect against the relevant store.
  * ``SelfOptimizerV2.plan_improvement_actions`` turns low-scoring eval signals
    into concrete ``ImprovementActionRecord`` objects.
  * The ``ImprovementActionExecutor`` executes those actions with the real
    handlers and the effect is observable (prompt variant created, tool
    pattern blacklisted, model routing overridden, etc.).
  * Handlers are idempotent and an unsupported action type is handled safely.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.intelligence.improvement_action_executor import ImprovementActionExecutor
from app.intelligence.improvement_handlers import (
    LessonStore,
    ModelRoutingPolicyStore,
    RagStrategyStore,
    RegressionCaseStore,
    ToolBlacklistStore,
    build_default_improvement_handlers,
)
from app.intelligence.prompt_optimizer import PromptOptimizer
from app.intelligence.self_optimizer_v2 import SelfOptimizerV2
from app.memory.contracts import ImprovementActionRecord


def _record(action_type: str, payload: dict, *, idem: str) -> ImprovementActionRecord:
    return ImprovementActionRecord(
        action_id=f"action:{action_type}:{idem}",
        tenant_id=str(payload["tenant_id"]),
        goal_id="goal-1",
        action_type=action_type,  # type: ignore[arg-type]
        payload=payload,
        state="pending",
        idempotency_key=idem,
        attempts=0,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Default handler set — real effects against real stores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_variant_handler_creates_real_variant() -> None:
    optimizer = PromptOptimizer()
    handlers = build_default_improvement_handlers(prompt_optimizer=optimizer)
    executor = ImprovementActionExecutor(handlers=handlers)

    record = _record(
        "update_prompt_variant",
        {
            "tenant_id": "t1",
            "prompt_key": "system_prompt",
            "variant_name": "candidate-a",
            "prompt_text": "You are a careful, thorough agent.",
        },
        idem="update_prompt_variant:t1:goal-1",
    )
    terminal = await executor.execute(record, policy_allowed=True)

    assert terminal.state == "completed"
    assert terminal.result is not None
    variant_id = terminal.result["variant_id"]
    # Observable effect: the variant now exists in the optimizer.
    assert optimizer.get_variant(variant_id, tenant_id="t1") is not None
    report = optimizer.get_report("system_prompt", tenant_id="t1")
    assert any(v["variant_id"] == variant_id for v in report["variants"])


@pytest.mark.asyncio
async def test_blacklist_and_model_routing_handlers_apply_effects() -> None:
    blacklist = ToolBlacklistStore()
    routing = ModelRoutingPolicyStore()
    handlers = build_default_improvement_handlers(
        tool_blacklist_store=blacklist, model_routing_store=routing
    )
    executor = ImprovementActionExecutor(handlers=handlers)

    bl = await executor.execute(
        _record(
            "blacklist_tool_pattern",
            {"tenant_id": "t1", "pattern": "flaky_*"},
            idem="blacklist_tool_pattern:t1:goal-1",
        ),
        policy_allowed=True,
    )
    assert bl.state == "completed"
    assert blacklist.is_blacklisted("t1", "flaky_*")

    mr = await executor.execute(
        _record(
            "update_model_routing",
            {"tenant_id": "t1", "task_type": "execution", "model": "claude-haiku-3-5"},
            idem="update_model_routing:t1:goal-1",
        ),
        policy_allowed=True,
    )
    assert mr.state == "completed"
    assert routing.get_override("t1", "execution") == "claude-haiku-3-5"


@pytest.mark.asyncio
async def test_reflexion_regression_and_rag_handlers_record_effects() -> None:
    lessons = LessonStore()
    regressions = RegressionCaseStore()
    rag = RagStrategyStore()
    handlers = build_default_improvement_handlers(
        lesson_store=lessons, regression_store=regressions, rag_strategy_store=rag
    )
    executor = ImprovementActionExecutor(handlers=handlers)

    await executor.execute(
        _record(
            "store_reflexion_lesson",
            {"tenant_id": "t1", "goal_id": "g", "lesson": "verify outputs before finishing"},
            idem="store_reflexion_lesson:t1:g",
        ),
        policy_allowed=True,
    )
    await executor.execute(
        _record(
            "create_regression_case",
            {"tenant_id": "t1", "goal_id": "g", "reason": "low overall score"},
            idem="create_regression_case:t1:g",
        ),
        policy_allowed=True,
    )
    await executor.execute(
        _record(
            "update_rag_strategy",
            {"tenant_id": "t1", "strategy": "rerank_hybrid"},
            idem="update_rag_strategy:t1:g",
        ),
        policy_allowed=True,
    )

    assert len(lessons.lessons_for("t1")) == 1
    assert len(regressions.cases_for("t1")) == 1
    assert rag.get_strategy("t1") == "rerank_hybrid"


@pytest.mark.asyncio
async def test_handlers_are_idempotent() -> None:
    blacklist = ToolBlacklistStore()
    optimizer = PromptOptimizer()
    handlers = build_default_improvement_handlers(
        tool_blacklist_store=blacklist, prompt_optimizer=optimizer
    )

    payload = {"tenant_id": "t1", "pattern": "flaky_*"}
    # Two *fresh* executors so the executor-level idempotency cache does not mask
    # a non-idempotent handler.
    r1 = await ImprovementActionExecutor(handlers=handlers).execute(
        _record("blacklist_tool_pattern", payload, idem="k"), policy_allowed=True
    )
    r2 = await ImprovementActionExecutor(handlers=handlers).execute(
        _record("blacklist_tool_pattern", payload, idem="k"), policy_allowed=True
    )
    assert r1.state == "completed" and r2.state == "completed"
    assert r1.result is not None and r1.result["newly_added"] is True
    assert r2.result is not None and r2.result["newly_added"] is False
    # Effect applied exactly once.
    assert blacklist.patterns_for("t1") == frozenset({"flaky_*"})

    prompt_payload = {
        "tenant_id": "t1",
        "prompt_key": "system_prompt",
        "variant_name": "cand",
        "prompt_text": "text",
    }
    p1 = await ImprovementActionExecutor(handlers=handlers).execute(
        _record("update_prompt_variant", prompt_payload, idem="p"), policy_allowed=True
    )
    p2 = await ImprovementActionExecutor(handlers=handlers).execute(
        _record("update_prompt_variant", prompt_payload, idem="p"), policy_allowed=True
    )
    assert p1.result is not None and p2.result is not None
    assert p1.result["variant_id"] == p2.result["variant_id"]
    # Only one variant registered despite two applications.
    report = optimizer.get_report("system_prompt", tenant_id="t1")
    assert len(report["variants"]) == 1


@pytest.mark.asyncio
async def test_unsupported_action_type_is_handled_safely() -> None:
    blacklist = ToolBlacklistStore()
    handlers = build_default_improvement_handlers(tool_blacklist_store=blacklist)
    executor = ImprovementActionExecutor(handlers=handlers)

    # ``publish_skill`` is a valid contract literal but intentionally not in the
    # default handler set — it must fail loudly without mutating any store.
    record = _record(
        "publish_skill",
        {"tenant_id": "t1", "skill": "x"},
        idem="publish_skill:t1:goal-1",
    )
    with pytest.raises(RuntimeError, match="missing improvement handler"):
        await executor.execute(record, policy_allowed=True)
    assert blacklist.patterns_for("t1") == frozenset()


# ---------------------------------------------------------------------------
# SelfOptimizerV2.plan_improvement_actions — signal → actions
# ---------------------------------------------------------------------------


def _optimizer() -> SelfOptimizerV2:
    # No DB/Redis/LLM needed for the pure planning method.
    return SelfOptimizerV2(redis=None, db_factory=None, llm_provider_factory=None)


def test_plan_improvement_actions_empty_when_above_threshold() -> None:
    opt = _optimizer()
    actions = opt.plan_improvement_actions(
        tenant_id="t1",
        goal_id="g1",
        scores={"goal_success": 0.9, "tool_success_rate": 0.9, "cost_efficiency": 0.8},
    )
    assert actions == []


def test_plan_improvement_actions_produces_prompt_and_reflexion() -> None:
    opt = _optimizer()
    actions = opt.plan_improvement_actions(
        tenant_id="t1",
        goal_id="g1",
        scores={"goal_success": 0.3, "tool_success_rate": 0.6},
        verification_feedback="planner produced vague steps",
        agent_config={"system_prompt": "You are an assistant."},
    )
    kinds = {a.action_type for a in actions}
    assert "update_prompt_variant" in kinds
    assert "store_reflexion_lesson" in kinds
    # Every produced record is executable: non-empty payload with tenant_id.
    for a in actions:
        assert a.payload and a.payload["tenant_id"] == "t1"
        assert a.state == "pending"


def test_plan_improvement_actions_blacklist_and_routing_and_regression() -> None:
    opt = _optimizer()
    actions = opt.plan_improvement_actions(
        tenant_id="t1",
        goal_id="g1",
        scores={
            "goal_success": 0.2,
            "tool_success_rate": 0.2,
            "cost_efficiency": 0.2,
            "latency": 0.2,
            "rag_quality": 0.3,
        },
        failing_tool_pattern="broken_tool_*",
    )
    kinds = {a.action_type for a in actions}
    assert {
        "blacklist_tool_pattern",
        "update_model_routing",
        "update_rag_strategy",
        "create_regression_case",
    } <= kinds


# ---------------------------------------------------------------------------
# Full round-trip: optimizer → executor → observable effect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimizer_to_executor_to_effect_round_trip() -> None:
    optimizer = SelfOptimizerV2(redis=None, db_factory=None, llm_provider_factory=None)
    prompt_opt = PromptOptimizer()
    blacklist = ToolBlacklistStore()
    routing = ModelRoutingPolicyStore()
    handlers = build_default_improvement_handlers(
        prompt_optimizer=prompt_opt,
        tool_blacklist_store=blacklist,
        model_routing_store=routing,
    )
    executor = ImprovementActionExecutor(handlers=handlers)

    actions = optimizer.plan_improvement_actions(
        tenant_id="t1",
        goal_id="g1",
        scores={
            "goal_success": 0.2,
            "tool_success_rate": 0.2,
            "cost_efficiency": 0.2,
        },
        failing_tool_pattern="broken_tool_*",
        verification_feedback="tool calls kept failing",
        agent_config={"system_prompt": "You are an assistant."},
    )
    assert actions, "optimizer must produce actions for low scores"

    results = [await executor.execute(a, policy_allowed=True) for a in actions]
    assert all(r.state == "completed" for r in results)

    # Observable effects across the real stores.
    assert blacklist.is_blacklisted("t1", "broken_tool_*")
    assert routing.get_override("t1", "execution") is not None
    assert prompt_opt.get_report("system_prompt", tenant_id="t1")["variants"]
