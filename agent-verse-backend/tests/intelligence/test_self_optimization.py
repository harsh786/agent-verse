"""Tests for self_optimization.apply_suggestion() actually mutating agent configs."""
from __future__ import annotations

import pytest

from app.intelligence.self_optimization import OptimizationSuggestion, SelfOptimizer
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="opt-t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


def test_apply_suggestion_mutates_agent_config() -> None:
    opt = SelfOptimizer()
    agent_config: dict = {"goal_template": "You are an agent.", "connector_ids": []}

    s = OptimizationSuggestion(
        suggestion_id="s1",
        change_type="improve_planner_prompt",
        before="old prompt",
        after="Add step-by-step reasoning for each subtask.",
        tenant_id=T.tenant_id,
    )
    opt._suggestions[T.tenant_id] = [s]

    result = opt.apply_suggestion(
        suggestion_id="s1", tenant_ctx=T, agent_config=agent_config
    )

    assert result is True
    assert s.applied is True
    assert "Add step-by-step reasoning" in agent_config["goal_template"]


def test_apply_suggestion_returns_false_for_unknown() -> None:
    opt = SelfOptimizer()
    result = opt.apply_suggestion(suggestion_id="nonexistent", tenant_ctx=T)
    assert result is False


def test_apply_suggestion_add_tool_access() -> None:
    opt = SelfOptimizer()
    agent_config: dict = {"goal_template": "", "connector_ids": []}

    s = OptimizationSuggestion(
        suggestion_id="s2",
        change_type="add_tool_access",
        before="no tools",
        after="web_search",
        tenant_id=T.tenant_id,
    )
    opt._suggestions[T.tenant_id] = [s]

    opt.apply_suggestion(suggestion_id="s2", tenant_ctx=T, agent_config=agent_config)

    assert "web_search" in agent_config["connector_ids"]


def test_apply_suggestion_increase_iterations() -> None:
    opt = SelfOptimizer()
    agent_config: dict = {"goal_template": "", "max_iterations": 3}

    s = OptimizationSuggestion(
        suggestion_id="s3",
        change_type="increase_iterations",
        before="3",
        after="8",
        tenant_id=T.tenant_id,
    )
    opt._suggestions[T.tenant_id] = [s]

    result = opt.apply_suggestion(
        suggestion_id="s3", tenant_ctx=T, agent_config=agent_config
    )

    assert result is True
    assert agent_config["max_iterations"] == 8


def test_apply_suggestion_does_not_duplicate_goal_template_text() -> None:
    """Re-applying the same suggestion should not double the text."""
    opt = SelfOptimizer()
    text = "Add step-by-step reasoning."
    agent_config: dict = {"goal_template": f"You are an agent.\n\n{text}"}

    s = OptimizationSuggestion(
        suggestion_id="s4",
        change_type="improve_planner_prompt",
        before="",
        after=text,
        tenant_id=T.tenant_id,
    )
    opt._suggestions[T.tenant_id] = [s]

    opt.apply_suggestion(suggestion_id="s4", tenant_ctx=T, agent_config=agent_config)

    # Text was already present — should not be appended again
    assert agent_config["goal_template"].count(text) == 1


def test_apply_suggestion_rejected_returns_false() -> None:
    opt = SelfOptimizer()

    s = OptimizationSuggestion(
        suggestion_id="s5",
        change_type="improve_planner_prompt",
        before="",
        after="some improvement",
        tenant_id=T.tenant_id,
        rejected=True,
    )
    opt._suggestions[T.tenant_id] = [s]

    result = opt.apply_suggestion(suggestion_id="s5", tenant_ctx=T)
    assert result is False
    assert s.applied is False


def test_applied_changes_are_tracked() -> None:
    opt = SelfOptimizer()
    agent_config: dict = {"goal_template": "", "connector_ids": []}

    s = OptimizationSuggestion(
        suggestion_id="s6",
        change_type="add_tool_access",
        before="",
        after="sql_query",
        tenant_id=T.tenant_id,
    )
    opt._suggestions[T.tenant_id] = [s]
    opt.apply_suggestion(suggestion_id="s6", tenant_ctx=T, agent_config=agent_config)

    changes = opt.get_applied_changes(tenant_ctx=T)
    assert len(changes) == 1
    assert changes[0]["suggestion_id"] == "s6"
    assert changes[0]["change_type"] == "add_tool_access"
    assert changes[0]["agent_config_mutated"] is True
