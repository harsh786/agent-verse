"""Comprehensive tests for SelfOptimizer — all suggestion types, apply, reject, history."""
from __future__ import annotations

import pytest

from app.intelligence.eval import EvalScorecard
from app.intelligence.self_optimization import OptimizationSuggestion, SelfOptimizer
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k1")


def _scorecard(scores: dict | None = None, goal_id: str = "g1") -> EvalScorecard:
    default = {"task_completion": 0.3, "efficiency": 0.3, "accuracy": 0.3,
               "safety": 0.3, "coherence": 0.3, "sla": 0.3}
    if scores:
        default.update(scores)
    return EvalScorecard(goal_id=goal_id, scores=default, goal="test goal")


# ── 1. record_eval ────────────────────────────────────────────────────────────

def test_record_eval_stores_scorecard():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard()
    opt.record_eval(goal_id="g1", scorecard=sc, tenant_ctx=ctx)
    assert len(opt._eval_history.get("t1", [])) == 1


def test_record_eval_multiple_tenants():
    opt = SelfOptimizer()
    opt.record_eval(goal_id="g1", scorecard=_scorecard(), tenant_ctx=_ctx("t1"))
    opt.record_eval(goal_id="g2", scorecard=_scorecard(), tenant_ctx=_ctx("t2"))
    assert len(opt._eval_history.get("t1", [])) == 1
    assert len(opt._eval_history.get("t2", [])) == 1


def test_record_eval_accumulates_for_same_tenant():
    opt = SelfOptimizer()
    ctx = _ctx()
    for i in range(5):
        opt.record_eval(goal_id=f"g{i}", scorecard=_scorecard(), tenant_ctx=ctx)
    assert len(opt._eval_history["t1"]) == 5


# ── 2. analyze_and_suggest ────────────────────────────────────────────────────

def test_suggest_low_average_score():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard()  # all 0.3 → average < 0.5
    suggestions = opt.analyze_and_suggest(
        goal="do something", scorecard=sc, error_log="", tenant_ctx=ctx
    )
    cats = [s.category for s in suggestions]
    assert "prompt" in cats


def test_suggest_tool_error_in_log():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard({"task_completion": 0.8, "efficiency": 0.8, "accuracy": 0.8,
                     "safety": 0.8, "coherence": 0.8, "sla": 0.8})
    suggestions = opt.analyze_and_suggest(
        goal="run tool", scorecard=sc, error_log="tool not found: search_web", tenant_ctx=ctx
    )
    cats = [s.category for s in suggestions]
    assert "tool_selection" in cats


def test_suggest_not_found_in_log():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard({"task_completion": 0.8, "efficiency": 0.8, "accuracy": 0.8,
                     "safety": 0.8, "coherence": 0.8, "sla": 0.8})
    suggestions = opt.analyze_and_suggest(
        goal="run tool", scorecard=sc, error_log="resource not found", tenant_ctx=ctx
    )
    cats = [s.category for s in suggestions]
    assert "tool_selection" in cats


def test_suggest_low_efficiency():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard({"task_completion": 0.9, "efficiency": 0.2, "accuracy": 0.9,
                     "safety": 0.9, "coherence": 0.9, "sla": 0.9})
    suggestions = opt.analyze_and_suggest(
        goal="optimize", scorecard=sc, error_log="", tenant_ctx=ctx
    )
    cats = [s.category for s in suggestions]
    assert "retry_strategy" in cats


def test_suggest_no_issues_returns_empty():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard({"task_completion": 0.9, "efficiency": 0.9, "accuracy": 0.9,
                     "safety": 0.9, "coherence": 0.9, "sla": 0.9})
    suggestions = opt.analyze_and_suggest(
        goal="good run", scorecard=sc, error_log="", tenant_ctx=ctx
    )
    assert suggestions == []


def test_suggest_all_three_when_bad():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard()  # all 0.3
    suggestions = opt.analyze_and_suggest(
        goal="do", scorecard=sc, error_log="tool not found", tenant_ctx=ctx
    )
    cats = [s.category for s in suggestions]
    assert "prompt" in cats
    assert "tool_selection" in cats
    # efficiency 0.3 < 0.4 → retry_strategy
    assert "retry_strategy" in cats


def test_suggestions_stored_per_tenant():
    opt = SelfOptimizer()
    opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="", tenant_ctx=_ctx("t1"))
    opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="", tenant_ctx=_ctx("t2"))
    assert len(opt._suggestions.get("t1", [])) >= 1
    assert len(opt._suggestions.get("t2", [])) >= 1


# ── 3. list_suggestions ───────────────────────────────────────────────────────

def test_list_suggestions_all():
    opt = SelfOptimizer()
    ctx = _ctx()
    opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="tool error", tenant_ctx=ctx)
    subs = opt.list_suggestions(tenant_ctx=ctx)
    assert len(subs) > 0


def test_list_suggestions_filter_unapplied():
    opt = SelfOptimizer()
    ctx = _ctx()
    opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="", tenant_ctx=ctx)
    unapplied = opt.list_suggestions(tenant_ctx=ctx, applied=False)
    for s in unapplied:
        assert s.applied is False


def test_list_suggestions_empty_for_unknown_tenant():
    opt = SelfOptimizer()
    subs = opt.list_suggestions(tenant_ctx=_ctx("unknown"))
    assert subs == []


# ── 4. apply_suggestion ───────────────────────────────────────────────────────

def test_apply_suggestion_marks_applied():
    opt = SelfOptimizer()
    ctx = _ctx()
    subs = opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="", tenant_ctx=ctx)
    assert len(subs) > 0
    sid = subs[0].suggestion_id
    result = opt.apply_suggestion(suggestion_id=sid, tenant_ctx=ctx)
    assert result is True
    applied = [s for s in opt._suggestions["t1"] if s.suggestion_id == sid]
    assert applied[0].applied is True


def test_apply_suggestion_unknown_id_returns_false():
    opt = SelfOptimizer()
    ctx = _ctx()
    result = opt.apply_suggestion(suggestion_id="nonexistent", tenant_ctx=ctx)
    assert result is False


def test_apply_suggestion_rejected_returns_false():
    opt = SelfOptimizer()
    ctx = _ctx()
    subs = opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="", tenant_ctx=ctx)
    sid = subs[0].suggestion_id
    opt.reject_suggestion(suggestion_id=sid, tenant_ctx=ctx)
    result = opt.apply_suggestion(suggestion_id=sid, tenant_ctx=ctx)
    assert result is False


def test_apply_suggestion_improve_planner_prompt():
    opt = SelfOptimizer()
    ctx = _ctx()
    sc = _scorecard()
    subs = opt.analyze_and_suggest(goal="g", scorecard=sc, error_log="", tenant_ctx=ctx)
    for s in subs:
        s.change_type = "improve_planner_prompt"
        s.after = "New planner instructions"
    agent_cfg = {"goal_template": "existing prompt"}
    opt.apply_suggestion(suggestion_id=subs[0].suggestion_id, tenant_ctx=ctx, agent_config=agent_cfg)
    assert "New planner instructions" in agent_cfg.get("goal_template", "")


def test_apply_suggestion_improve_executor_prompt():
    opt = SelfOptimizer()
    ctx = _ctx()
    s = OptimizationSuggestion(
        change_type="improve_executor_prompt",
        after="Executor instructions",
        category="prompt",
    )
    opt._suggestions.setdefault("t1", []).append(s)
    agent_cfg = {"goal_template": "base"}
    result = opt.apply_suggestion(suggestion_id=s.suggestion_id, tenant_ctx=ctx, agent_config=agent_cfg)
    assert result is True


def test_apply_suggestion_add_domain_context():
    opt = SelfOptimizer()
    ctx = _ctx()
    s = OptimizationSuggestion(
        change_type="add_domain_context",
        after="Healthcare context",
        category="prompt",
    )
    opt._suggestions.setdefault("t1", []).append(s)
    agent_cfg = {"goal_template": "base prompt"}
    result = opt.apply_suggestion(suggestion_id=s.suggestion_id, tenant_ctx=ctx, agent_config=agent_cfg)
    assert result is True
    assert "Healthcare context" in agent_cfg["goal_template"]


def test_apply_suggestion_increase_iterations():
    opt = SelfOptimizer()
    ctx = _ctx()
    s = OptimizationSuggestion(change_type="increase_iterations", after="10", category="retry_strategy")
    opt._suggestions.setdefault("t1", []).append(s)
    agent_cfg = {"max_iterations": 5}
    opt.apply_suggestion(suggestion_id=s.suggestion_id, tenant_ctx=ctx, agent_config=agent_cfg)
    assert agent_cfg["max_iterations"] == 10


def test_apply_suggestion_increase_iterations_invalid_value():
    opt = SelfOptimizer()
    ctx = _ctx()
    s = OptimizationSuggestion(change_type="increase_iterations", after="not_a_number", category="retry_strategy")
    opt._suggestions.setdefault("t1", []).append(s)
    agent_cfg = {}
    opt.apply_suggestion(suggestion_id=s.suggestion_id, tenant_ctx=ctx, agent_config=agent_cfg)
    assert agent_cfg["max_iterations"] == 5  # fallback


def test_apply_suggestion_add_tool_access():
    opt = SelfOptimizer()
    ctx = _ctx()
    s = OptimizationSuggestion(change_type="add_tool_access", after="search_web", category="tool_selection")
    opt._suggestions.setdefault("t1", []).append(s)
    agent_cfg = {"connector_ids": ["existing_tool"]}
    opt.apply_suggestion(suggestion_id=s.suggestion_id, tenant_ctx=ctx, agent_config=agent_cfg)
    assert "search_web" in agent_cfg["connector_ids"]


def test_apply_suggestion_add_tool_access_no_duplicate():
    opt = SelfOptimizer()
    ctx = _ctx()
    s = OptimizationSuggestion(change_type="add_tool_access", after="search_web", category="tool_selection")
    opt._suggestions.setdefault("t1", []).append(s)
    agent_cfg = {"connector_ids": ["search_web"]}
    opt.apply_suggestion(suggestion_id=s.suggestion_id, tenant_ctx=ctx, agent_config=agent_cfg)
    assert agent_cfg["connector_ids"].count("search_web") == 1


def test_apply_suggestion_tracks_applied_changes():
    opt = SelfOptimizer()
    ctx = _ctx()
    subs = opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="", tenant_ctx=ctx)
    sid = subs[0].suggestion_id
    opt.apply_suggestion(suggestion_id=sid, tenant_ctx=ctx)
    changes = opt.get_applied_changes(tenant_ctx=ctx)
    assert len(changes) == 1
    assert changes[0]["suggestion_id"] == sid


# ── 5. reject_suggestion ──────────────────────────────────────────────────────

def test_reject_suggestion_marks_rejected():
    opt = SelfOptimizer()
    ctx = _ctx()
    subs = opt.analyze_and_suggest(goal="g", scorecard=_scorecard(), error_log="", tenant_ctx=ctx)
    sid = subs[0].suggestion_id
    result = opt.reject_suggestion(suggestion_id=sid, tenant_ctx=ctx)
    assert result is True
    assert subs[0].rejected is True


def test_reject_suggestion_unknown_id_returns_false():
    opt = SelfOptimizer()
    ctx = _ctx()
    result = opt.reject_suggestion(suggestion_id="no-such-id", tenant_ctx=ctx)
    assert result is False


# ── 6. get_applied_changes ────────────────────────────────────────────────────

def test_get_applied_changes_empty_initially():
    opt = SelfOptimizer()
    ctx = _ctx()
    assert opt.get_applied_changes(tenant_ctx=ctx) == []


def test_get_applied_changes_contains_change_metadata():
    opt = SelfOptimizer()
    ctx = _ctx()
    s = OptimizationSuggestion(
        change_type="increase_iterations",
        after="8",
        before="15",
        category="retry_strategy",
    )
    opt._suggestions.setdefault("t1", []).append(s)
    opt.apply_suggestion(suggestion_id=s.suggestion_id, tenant_ctx=ctx)
    changes = opt.get_applied_changes(tenant_ctx=ctx)
    assert changes[0]["change_type"] == "increase_iterations"
    assert changes[0]["before"] == "15"
    assert changes[0]["after"] == "8"
    assert "applied_at" in changes[0]


# ── 7. OptimizationSuggestion dataclass ──────────────────────────────────────

def test_suggestion_has_unique_id():
    s1 = OptimizationSuggestion()
    s2 = OptimizationSuggestion()
    assert s1.suggestion_id != s2.suggestion_id


def test_suggestion_defaults():
    s = OptimizationSuggestion()
    assert s.applied is False
    assert s.rejected is False
    assert s.confidence == 0.0
