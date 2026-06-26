"""Tests for static multi-agent workflow planning."""

from __future__ import annotations

from app.agent.workflow_planner import build_static_workflow


def test_build_static_workflow_maps_jira_confluence_and_email() -> None:
    plan = build_static_workflow(
        "Summarize open Jira issues in Confluence and email the team"
    )

    assert [step.step_id for step in plan.steps] == ["step_1", "step_2", "step_3"]
    assert [step.connector_name for step in plan.steps] == [
        "jira",
        "confluence",
        "email",
    ]
    assert [step.intent for step in plan.steps] == [
        "fetch_open_issues",
        "create_summary_page",
        "send_summary_email",
    ]
    assert [step.input_from for step in plan.steps] == [[], ["step_1"], ["step_1", "step_2"]]
    assert [step.agent_id for step in plan.steps] == [None, None, None]
    assert [step.requires_approval for step in plan.steps] == [False, False, False]


def test_build_static_workflow_handles_rpa_browser_goals() -> None:
    plan = build_static_workflow("Use the browser to check the customer website UI")

    assert len(plan.steps) == 1
    assert plan.steps[0].connector_name == "rpa"
    assert plan.steps[0].intent == "browser_automation"
    assert plan.steps[0].input_from == []
