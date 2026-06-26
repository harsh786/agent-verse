"""Simulation runner — execute goals in a sandboxed mock-tool environment.

Allows testing agent behavior without real side effects by replacing
registered tools with mock implementations.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.tenancy.context import TenantContext

# Human-readable labels for known tool names
_TOOL_ACTIONS: dict[str, str] = {
    "github:list_issues": "Fetch GitHub issues",
    "github:create_pr": "Open GitHub pull request",
    "github:merge_pr": "Merge GitHub pull request",
    "jira:search_issues": "Search Jira issues",
    "jira:create_issue": "Create Jira issue",
    "jira:update_issue": "Update Jira issue",
    "confluence:create_page": "Create Confluence page",
    "confluence:update_page": "Update Confluence page",
    "slack:send_message": "Send Slack message",
    "email:send": "Send email",
    "datadog:get_metrics": "Fetch Datadog metrics",
    "salesforce:query": "Query Salesforce CRM",
}


class MockMCPClient:
    """MCP client that returns pre-configured mock responses.

    Used in simulation to test agent behavior without real tool calls.
    """

    def __init__(self, mock_tools: dict[str, Any]) -> None:
        self._mocks = mock_tools

    async def discover_tools(self, server_id: str, tenant_ctx: Any) -> list:
        return []

    async def discover_all_tools(self, tenant_ctx: Any) -> list:
        return []

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: Any,
    ) -> dict[str, Any]:
        key = tool_name
        full_key = f"{server_id}.{tool_name}"
        mock = self._mocks.get(full_key) or self._mocks.get(key)
        if mock is not None:
            content = mock if isinstance(mock, str) else __import__("json").dumps(mock)
            return {"content": [{"type": "text", "text": content}], "simulated": True}
        return {
            "content": [{"type": "text",
                         "text": f"[simulated: no mock for {tool_name}]"}],
            "simulated": True,
        }


@dataclass
class SimulationRun:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    goal: str = ""
    mock_tools: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class SimulationRunner:
    """Runs goals in a mock-tool sandbox environment."""

    def __init__(self) -> None:
        self._runs: dict[str, SimulationRun] = {}

    def start(
        self,
        *,
        goal: str,
        mock_tools: dict[str, Any],
        tenant_ctx: TenantContext,
        provider: Any = None,
    ) -> SimulationRun:
        run = SimulationRun(goal=goal, mock_tools=mock_tools)
        run.status = "running"

        # If a real LLM provider is supplied, a MockMCPClient is available for
        # integration with AgentGraph. For now we fall through to the heuristic
        # planner since async AgentGraph instantiation requires an event loop.
        _ = MockMCPClient(mock_tools) if provider is not None else None

        # Build a keyword-based execution plan from the goal
        steps = self._build_plan(goal, mock_tools)

        executed_steps: list[dict[str, Any]] = []
        for step in steps:
            tool_name = step.get("tool")
            output: str | None = None

            if tool_name and tool_name in mock_tools:
                mock_output = mock_tools[tool_name]
                raw = mock_output if isinstance(mock_output, str) else str(mock_output)
                output = raw[:200]
            elif tool_name:
                output = f"[simulated] {step['description']} completed (tool not in mock_tools)"
            else:
                output = f"[simulated] {step['description']} completed"

            executed_steps.append(
                {
                    "step": step["description"],
                    "tool": tool_name,
                    "output": output,
                }
            )

        run.status = "completed"
        run.result = {
            # New fields (frontend)
            "goal": goal,
            "status": "completed",
            "steps": executed_steps,
            "cost_usd": round(len(executed_steps) * 0.001, 4),
            "iterations": len(executed_steps),
            "message": (
                f"Simulation complete: {len(executed_steps)} steps executed with mock tools"
            ),
            # Backward-compatible fields (existing tests)
            "simulated_steps": [s["step"] for s in executed_steps],
            "outcome": "success (simulated)",
            "side_effects": [],
            "mock_tools_used": list(mock_tools.keys()),
            "note": "Simulation complete — no real tools were called",
        }
        self._runs[run.run_id] = run
        return run

    # ── Private helpers ────────────────────────────────────────────────────────

    def _build_plan(self, goal: str, mock_tools: dict[str, Any]) -> list[dict[str, Any]]:
        """Build a keyword-based execution plan for the simulated goal."""
        steps: list[dict[str, Any]] = []
        goal_lower = goal.lower()

        # Step 1 — always analyse the goal first
        steps.append({"description": "Analyse goal requirements", "tool": None})

        # Step 2 — add a step for each provided mock tool
        for tool_name in mock_tools:
            action = _TOOL_ACTIONS.get(tool_name, f"Execute {tool_name}")
            steps.append({"description": action, "tool": tool_name})

        # Step 3 — add keyword-inferred steps when no matching mock tool covers them
        tool_names_lower = {t.lower() for t in mock_tools}

        if any(kw in goal_lower for kw in ("test", "verify", "check", "validate")):
            if not any("test" in t or "verify" in t for t in tool_names_lower):
                steps.append({"description": "Run verification checks", "tool": None})

        if any(kw in goal_lower for kw in ("report", "summary", "document")):
            if not any("confluence" in t or "doc" in t for t in tool_names_lower):
                steps.append({"description": "Generate summary report", "tool": None})

        if any(kw in goal_lower for kw in ("notify", "alert", "message", "ping")):
            if not any("slack" in t or "email" in t for t in tool_names_lower):
                steps.append({"description": "Send notification", "tool": None})

        # Step 4 — always end with a verification step
        steps.append({"description": "Verify goal completion", "tool": None})

        return steps

    def get(self, *, run_id: str, tenant_ctx: TenantContext) -> SimulationRun | None:
        return self._runs.get(run_id)

    def list_runs(self, *, tenant_ctx: TenantContext) -> list[SimulationRun]:
        return list(self._runs.values())

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    goal: str = ""
    mock_tools: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class SimulationRunner:
    """Runs goals in a mock-tool sandbox environment."""

    def __init__(self) -> None:
        self._runs: dict[str, SimulationRun] = {}

    def start(
        self, *, goal: str, mock_tools: dict[str, Any], tenant_ctx: TenantContext
    ) -> SimulationRun:
        run = SimulationRun(goal=goal, mock_tools=mock_tools)
        run.status = "running"

        # Build a keyword-based execution plan from the goal
        steps = self._build_plan(goal, mock_tools)

        executed_steps: list[dict[str, Any]] = []
        for step in steps:
            tool_name = step.get("tool")
            output: str | None = None

            if tool_name and tool_name in mock_tools:
                mock_output = mock_tools[tool_name]
                raw = mock_output if isinstance(mock_output, str) else str(mock_output)
                output = raw[:200]
            elif tool_name:
                output = f"[simulated] {step['description']} completed (tool not in mock_tools)"
            else:
                output = f"[simulated] {step['description']} completed"

            executed_steps.append(
                {
                    "step": step["description"],
                    "tool": tool_name,
                    "output": output,
                }
            )

        run.status = "completed"
        run.result = {
            # New fields (frontend)
            "goal": goal,
            "status": "completed",
            "steps": executed_steps,
            "cost_usd": round(len(executed_steps) * 0.001, 4),
            "iterations": len(executed_steps),
            "message": (
                f"Simulation complete: {len(executed_steps)} steps executed with mock tools"
            ),
            # Backward-compatible fields (existing tests)
            "simulated_steps": [s["step"] for s in executed_steps],
            "outcome": "success (simulated)",
            "side_effects": [],
            "mock_tools_used": list(mock_tools.keys()),
            "note": "Simulation complete — no real tools were called",
        }
        self._runs[run.run_id] = run
        return run

    # ── Private helpers ────────────────────────────────────────────────────────

    def _build_plan(self, goal: str, mock_tools: dict[str, Any]) -> list[dict[str, Any]]:
        """Build a keyword-based execution plan for the simulated goal."""
        steps: list[dict[str, Any]] = []
        goal_lower = goal.lower()

        # Step 1 — always analyse the goal first
        steps.append({"description": "Analyse goal requirements", "tool": None})

        # Step 2 — add a step for each provided mock tool
        for tool_name in mock_tools:
            action = _TOOL_ACTIONS.get(tool_name, f"Execute {tool_name}")
            steps.append({"description": action, "tool": tool_name})

        # Step 3 — add keyword-inferred steps when no matching mock tool covers them
        tool_names_lower = {t.lower() for t in mock_tools}

        if any(kw in goal_lower for kw in ("test", "verify", "check", "validate")):
            if not any("test" in t or "verify" in t for t in tool_names_lower):
                steps.append({"description": "Run verification checks", "tool": None})

        if any(kw in goal_lower for kw in ("report", "summary", "document")):
            if not any("confluence" in t or "doc" in t for t in tool_names_lower):
                steps.append({"description": "Generate summary report", "tool": None})

        if any(kw in goal_lower for kw in ("notify", "alert", "message", "ping")):
            if not any("slack" in t or "email" in t for t in tool_names_lower):
                steps.append({"description": "Send notification", "tool": None})

        # Step 4 — always end with a verification step
        steps.append({"description": "Verify goal completion", "tool": None})

        return steps

    def get(self, *, run_id: str, tenant_ctx: TenantContext) -> SimulationRun | None:
        return self._runs.get(run_id)

    def list_runs(self, *, tenant_ctx: TenantContext) -> list[SimulationRun]:
        return list(self._runs.values())
