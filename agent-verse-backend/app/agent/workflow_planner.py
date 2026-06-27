"""Workflow planner: static keyword-based planner and LLM-based DAG planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Legacy static workflow types (used by build_static_workflow / goal_service)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _StaticWorkflowStep:
    step_id: str
    connector_name: str | None
    agent_id: str | None
    intent: str
    input_from: list[str]
    requires_approval: bool


@dataclass(frozen=True)
class _StaticWorkflowPlan:
    steps: list[_StaticWorkflowStep]


def build_static_workflow(goal: str) -> _StaticWorkflowPlan:
    """Build a deterministic connector-targeted workflow from goal keywords."""
    goal_lower = goal.casefold()
    steps: list[_StaticWorkflowStep] = []

    def add_step(connector_name: str, intent: str, input_from: list[str]) -> None:
        steps.append(
            _StaticWorkflowStep(
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

    return _StaticWorkflowPlan(steps=steps)


# ---------------------------------------------------------------------------
# New DAG-capable workflow types (used by WorkflowPlanner / WorkflowExecutor)
# ---------------------------------------------------------------------------

@dataclass
class WorkflowStep:
    id: str
    description: str
    tool: str = ""
    depends_on: list[str] = field(default_factory=list)
    can_parallel: bool = True
    estimated_minutes: int = 1
    status: str = "pending"   # pending|running|complete|failed
    result: str = ""
    error: str = ""


@dataclass
class WorkflowPlan:
    goal: str
    steps: list[WorkflowStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, goal: str) -> "WorkflowPlan":
        steps = [
            WorkflowStep(
                id=s.get("id", f"s{i + 1}"),
                description=s.get("description", ""),
                tool=s.get("tool", ""),
                depends_on=s.get("depends_on", []),
                can_parallel=s.get("can_parallel", True),
                estimated_minutes=s.get("estimated_minutes", 1),
            )
            for i, s in enumerate(data.get("steps", []))
        ]
        return cls(goal=goal, steps=steps)

    def execution_waves(self) -> list[list[WorkflowStep]]:
        """Topological sort into parallel execution waves.

        Steps whose *depends_on* is empty (or all dependencies are complete)
        form a wave and can be executed in parallel via asyncio.gather().
        """
        completed: set[str] = set()
        remaining = list(self.steps)
        waves: list[list[WorkflowStep]] = []
        max_iterations = len(self.steps) + 1
        iteration = 0
        while remaining and iteration < max_iterations:
            iteration += 1
            wave = [s for s in remaining if all(d in completed for d in s.depends_on)]
            if not wave:
                # Circular dependency or unresolvable — add all remaining as one wave
                waves.append(remaining)
                break
            waves.append(wave)
            completed.update(s.id for s in wave)
            remaining = [s for s in remaining if s.id not in completed]
        return waves


# ---------------------------------------------------------------------------
# LLM-based WorkflowPlanner
# ---------------------------------------------------------------------------

class WorkflowPlanner:
    """LLM-based workflow DAG planner.

    Given a goal, produces a dependency graph of steps that can be
    executed in parallel waves using asyncio.gather().
    """

    def __init__(self, provider: Any = None) -> None:
        self._provider = provider

    async def plan(
        self,
        goal: str,
        tenant_ctx: Any,
        tool_context: Any = None,
    ) -> WorkflowPlan:
        """Generate a parallel-aware workflow plan from a natural language goal."""
        if self._provider is None:
            return self._heuristic_plan(goal)

        # Build tool context summary for the planner
        tool_summary = ""
        if tool_context is not None:
            try:
                tool_names = [t.name for t in (tool_context.tools or [])][:20]
                tool_summary = f"\nAvailable tools: {', '.join(tool_names)}"
            except Exception:
                pass

        prompt = f"""You are a workflow orchestration engine. Given a goal, produce a parallel-aware execution plan.

Goal: {goal}{tool_summary}

Return a JSON workflow plan:
{{
  "steps": [
    {{
      "id": "s1",
      "description": "Step description",
      "tool": "tool_name_or_empty",
      "depends_on": [],
      "can_parallel": true,
      "estimated_minutes": 1
    }}
  ]
}}

Rules:
- Steps with no depends_on can start immediately (in parallel if multiple)
- depends_on contains IDs of steps that must complete first
- can_parallel=true means this step can run alongside other parallel steps
- Keep steps atomic (one tool call or one clear action per step)
- Maximum 10 steps
Return ONLY the JSON, no other text."""

        try:
            import json
            import re

            from app.providers.base import CompletionRequest, Message

            model = getattr(self._provider, "_default_model", "")
            resp = await self._provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model=model,
                max_tokens=1500,
            ))
            text = resp.content.strip()
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return WorkflowPlan.from_dict(data, goal=goal)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("workflow_planner_llm_failed: %s", exc)

        return self._heuristic_plan(goal)

    def _heuristic_plan(self, goal: str) -> WorkflowPlan:
        """Fallback heuristic plan when LLM unavailable."""
        return WorkflowPlan(
            goal=goal,
            steps=[WorkflowStep(
                id="s1",
                description=goal,
                tool="",
                depends_on=[],
                can_parallel=False,
                estimated_minutes=5,
            )]
        )
