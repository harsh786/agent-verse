"""Minimal static workflow planner for multi-agent goals."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    connector_name: str | None
    agent_id: str | None
    intent: str
    input_from: list[str]
    requires_approval: bool


@dataclass(frozen=True)
class WorkflowPlan:
    steps: list[WorkflowStep]


def build_static_workflow(goal: str) -> WorkflowPlan:
    """Build a deterministic connector-targeted workflow from goal keywords."""
    goal_lower = goal.casefold()
    steps: list[WorkflowStep] = []

    def add_step(connector_name: str, intent: str, input_from: list[str]) -> None:
        steps.append(
            WorkflowStep(
                step_id=f"step_{len(steps) + 1}",
                connector_name=connector_name,
                agent_id=None,
                intent=intent,
                input_from=list(input_from),
                requires_approval=False,
            )
        )

    if "jira" in goal_lower:
        add_step("jira", "fetch_open_issues", [])

    if "confluence" in goal_lower:
        jira_step_ids = [step.step_id for step in steps if step.connector_name == "jira"]
        add_step("confluence", "create_summary_page", jira_step_ids[-1:])

    if "mail" in goal_lower or "email" in goal_lower:
        add_step("email", "send_summary_email", [step.step_id for step in steps])

    if any(keyword in goal_lower for keyword in ("browser", "rpa", "website", "ui")):
        add_step("rpa", "browser_automation", [])

    return WorkflowPlan(steps=steps)
