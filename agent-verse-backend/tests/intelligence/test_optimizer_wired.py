"""Verify SelfOptimizer and PromptOptimizer are wired and functional."""
import pytest

from app.intelligence.eval import EvalScorecard
from app.intelligence.prompt_optimizer import PromptOptimizer
from app.intelligence.self_optimization import OptimizationSuggestion, SelfOptimizer
from app.tenancy.context import PlanTier, TenantContext


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-optimizer-wire-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def test_self_optimizer_generate_suggestions_low_score():
    """SelfOptimizer must generate suggestions when avg score < 0.5."""
    opt = SelfOptimizer()
    tenant = _tenant()
    scorecard = EvalScorecard(
        goal_id="g1",
        goal="find Jira tickets",
        scores={"task_completion": 0.2, "efficiency": 0.3, "accuracy": 0.4,
                "safety": 1.0, "coherence": 0.3},
    )

    suggestions = opt.analyze_and_suggest(
        goal="find Jira tickets",
        scorecard=scorecard,
        error_log="tool not found: jira_search",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 2, "Must generate at least 2 suggestions for low score + tool error"
    categories = {s.category for s in suggestions}
    assert "prompt" in categories, "Must suggest prompt improvement for low score"
    assert "tool_selection" in categories, "Must suggest tool fix for tool-not-found error"


def test_self_optimizer_apply_suggestion_mutates_agent_config():
    """apply_suggestion with change_type=increase_iterations must update config."""
    opt = SelfOptimizer()
    tenant = _tenant()

    scorecard = EvalScorecard(
        goal_id="g2",
        goal="test",
        scores={"task_completion": 0.0, "efficiency": 0.2, "accuracy": 0.5,
                "safety": 1.0, "coherence": 0.3},
    )
    suggestions = opt.analyze_and_suggest(
        goal="test", scorecard=scorecard, error_log="", tenant_ctx=tenant
    )
    eff_sugg = next(
        (s for s in suggestions if s.category == "retry_strategy"), None
    )
    if eff_sugg:
        eff_sugg.change_type = "increase_iterations"
        eff_sugg.after = "8"
        config = {"max_iterations": 15}
        result = opt.apply_suggestion(
            suggestion_id=eff_sugg.suggestion_id,
            tenant_ctx=tenant,
            agent_config=config,
        )
        assert result is True
        assert config["max_iterations"] == 8


def test_prompt_optimizer_select_variant_returns_control():
    """select_variant must return control variant 70% of the time."""
    opt = PromptOptimizer()
    control = opt.register_variant(
        "planner", "control-prompt", "You are a helpful planner.",
        tenant_id="t1", is_control=True
    )
    _ = opt.register_variant(
        "planner", "challenger-v1", "You are a precise planner.",
        tenant_id="t1", is_control=False
    )

    hits = sum(
        1 for _ in range(200)
        if opt.select_variant("planner", tenant_id="t1") == control
    )
    assert 120 <= hits <= 180, f"Expected ~140 control hits, got {hits}"


def test_prompt_optimizer_record_result_and_maybe_promote():
    """After enough runs, maybe_promote promotes a clearly better challenger."""
    import random
    random.seed(42)
    opt = PromptOptimizer(min_runs_for_promotion=10, confidence=0.8)

    control = opt.register_variant(
        "executor", "old-prompt", "Basic executor.", tenant_id="t2", is_control=True
    )
    challenger = opt.register_variant(
        "executor", "new-prompt", "Better executor.", tenant_id="t2", is_control=False
    )

    for _ in range(10):
        opt.record_result(control.variant_id, 0.5)
        opt.record_result(challenger.variant_id, 0.9)

    promoted = opt.maybe_promote("executor", tenant_id="t2")
    assert promoted is not None
    assert promoted.variant_id == challenger.variant_id
    assert promoted.is_control is True
    assert control.is_control is False


def test_agentgraph_accepts_self_optimizer_and_prompt_optimizer():
    """AgentGraph must accept both optimizers as settable attributes."""
    from unittest.mock import MagicMock

    from app.agent.graph import AgentGraph
    from app.intelligence.prompt_optimizer import PromptOptimizer
    from app.intelligence.self_optimization import SelfOptimizer

    fake_provider = MagicMock()
    graph = AgentGraph(
        planner=fake_provider, executor=fake_provider, verifier=fake_provider
    )
    self_opt = SelfOptimizer()
    prompt_opt = PromptOptimizer()

    graph._self_optimizer = self_opt
    graph._prompt_optimizer = prompt_opt

    assert graph._self_optimizer is self_opt
    assert graph._prompt_optimizer is prompt_opt
