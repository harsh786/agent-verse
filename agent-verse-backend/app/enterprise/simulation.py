"""Simulation runner — execute goals in a sandboxed mock-tool environment.

Allows testing agent behavior without real side effects by replacing
registered tools with mock implementations.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.tenancy.context import TenantContext

logger = logging.getLogger(__name__)

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
    Accepts either ``mock_tools`` or ``mock_responses`` for backward compat.
    """

    def __init__(
        self,
        mock_tools: dict[str, Any] | None = None,
        mock_responses: dict[str, Any] | None = None,
    ) -> None:
        self._mocks = mock_responses or mock_tools or {}

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
    # Phase 13: extended fields for full-pipeline simulation output
    steps_executed: list[dict[str, Any]] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    mock_tools_used: list[str] = field(default_factory=list)
    cost_estimate: float = 0.0
    used_real_llm: bool = False
    risk_level: str = ""


class SimulationRunner:
    """Runs goals in a mock-tool sandbox environment."""

    def __init__(self) -> None:
        self._runs: dict[str, SimulationRun] = {}
        self._provider: Any = None

    async def start(
        self,
        *,
        goal: str,
        mock_tools: dict[str, Any] | None = None,
        tenant_ctx: Any = None,
        provider: Any = None,
        app_state: Any = None,
    ) -> SimulationRun:
        """Run a goal through the FULL AgentGraph pipeline with mocked tool responses.

        Tries the full AgentGraph pipeline first (gives accurate simulation including
        real LLM planning, policy evaluation, HITL gate identification, and tool risk
        classification) then falls back to the stub planner when no provider is available.
        """
        run_id = uuid.uuid4().hex
        _mock_tools = mock_tools or {}

        # Build a mock MCP client that returns pre-configured responses
        mock_client = MockMCPClient(mock_responses=_mock_tools)

        # Resolve provider: explicit arg > app_state > stored
        _provider = (
            provider
            or (getattr(app_state, "_app_provider", None) if app_state else None)
            or self._provider
        )

        # Try full AgentGraph pipeline
        try:
            from app.agent.graph import AgentGraph

            if _provider is None:
                # No LLM available — use stub simulation
                return await self._stub_simulation(
                    goal=goal, run_id=run_id, mock_tools=_mock_tools,
                    tenant_ctx=tenant_ctx, provider=None,
                )

            graph = AgentGraph(
                planner=_provider,
                executor=_provider,
                verifier=_provider,
                mcp_client=mock_client,
                audit_log=getattr(app_state, "audit_log", None) if app_state else None,
                cost_controller=(
                    getattr(app_state, "cost_controller", None) if app_state else None
                ),
                policy_engine=(
                    getattr(app_state, "policy_engine", None) if app_state else None
                ),
            )

            if tenant_ctx is None:
                from app.tenancy.context import PlanTier
                tenant_ctx = TenantContext(
                    tenant_id="simulation",
                    plan=PlanTier.ENTERPRISE,
                    api_key_id="sim",
                )

            result = await graph.run(
                goal=goal,
                tenant_ctx=tenant_ctx,
                initial_context={"dry_run": True, "simulation": True},
            )

            steps_raw = getattr(result, "steps", []) or []
            tools_called = [
                str(getattr(s, "tool", ""))
                for s in steps_raw
                if getattr(s, "tool", "")
            ]
            steps_executed = [
                {
                    "description": getattr(s, "description", ""),
                    "tool": str(getattr(s, "tool", "") or ""),
                    "output": str(getattr(s, "output", "") or ""),
                }
                for s in steps_raw
            ]

            run = SimulationRun(
                run_id=run_id,
                goal=goal,
                mock_tools=_mock_tools,
                status="complete",
                steps_executed=steps_executed,
                tools_called=tools_called,
                mock_tools_used=list(_mock_tools.keys()),
                cost_estimate=round(len(steps_raw) * 0.001, 4),
                used_real_llm=True,
                risk_level="simulated",
                result={
                    "goal": goal,
                    "status": "completed",
                    "steps": [
                        {"step": s["description"], "tool": s["tool"], "output": s["output"]}
                        for s in steps_executed
                    ],
                    "cost_usd": round(len(steps_raw) * 0.001, 4),
                    "iterations": len(steps_raw),
                    "message": f"Simulation complete: {len(steps_raw)} steps",
                    "simulated_steps": [s["description"] for s in steps_executed],
                    "outcome": "success (simulated)",
                    "side_effects": [],
                    "mock_tools_used": list(_mock_tools.keys()),
                    "note": "Simulation complete — no real tools were called",
                    "used_real_llm": True,
                },
            )
            self._runs[run_id] = run
            return run

        except Exception as exc:
            logger.warning("simulation_full_pipeline_failed: %s", exc)
            return await self._stub_simulation(
                goal=goal, run_id=run_id, mock_tools=_mock_tools,
                tenant_ctx=tenant_ctx, provider=provider,
            )

    async def _stub_simulation(
        self,
        *,
        goal: str,
        run_id: str,
        mock_tools: dict[str, Any],
        tenant_ctx: Any = None,
        provider: Any = None,
    ) -> SimulationRun:
        """Stub simulation using keyword-based planning (no real LLM required)."""
        run = SimulationRun(run_id=run_id, goal=goal, mock_tools=mock_tools)
        run.status = "running"

        if provider is not None:
            try:
                from app.providers.base import CompletionRequest, Message

                req = CompletionRequest(
                    messages=[Message(
                        role="user",
                        content=(
                            f"Goal: {goal}\n\n"
                            f"Available mock tools: {list(mock_tools.keys())}\n\n"
                            "Produce a step-by-step plan using these tools."
                        )
                    )],
                    model="",
                )
                used_real_llm = False
                resp = None
                try:
                    resp = await provider.complete(req)
                    used_real_llm = True
                except Exception as exc:
                    logger.warning("simulation_llm_failed: %s", exc)
                    resp = None

                if used_real_llm and resp is not None:
                    plan_text = resp.content
                    step_lines = [
                        line.strip().lstrip("0123456789.-) ")
                        for line in plan_text.split("\n")
                        if line.strip() and len(line.strip()) > 5
                    ][:10]
                    steps_with_tools: list[dict[str, Any]] = []
                    for step_line in step_lines:
                        matched_tool = next(
                            (t for t in mock_tools if any(
                                word in step_line.lower()
                                for word in t.lower().split(".")[-1].split("_")
                            )),
                            None
                        )
                        steps_with_tools.append(
                            {"description": step_line, "tool": matched_tool}
                        )
                else:
                    steps_with_tools = self._build_plan(goal, mock_tools)
            except Exception:
                steps_with_tools = self._build_plan(goal, mock_tools)
        else:
            steps_with_tools = self._build_plan(goal, mock_tools)

        # Execute steps with mock tool responses
        executed_steps: list[dict[str, Any]] = []
        for step in steps_with_tools:
            tool_name = step.get("tool")
            output: str

            if tool_name and tool_name in mock_tools:
                mock_resp = mock_tools[tool_name]
                raw = mock_resp if isinstance(mock_resp, str) else str(mock_resp)
                output = raw[:200]
            else:
                output = f"[simulated] {step.get('description', 'step completed')}"

            executed_steps.append(
                {
                    "step": step.get("description", ""),
                    "tool": tool_name,
                    "output": output,
                }
            )

        run.status = "completed"
        run.steps_executed = [
            {"description": s["step"], "tool": s.get("tool") or "", "output": s.get("output", "")}
            for s in executed_steps
        ]
        run.tools_called = [s["tool"] for s in executed_steps if s.get("tool")]
        run.mock_tools_used = list(mock_tools.keys())
        run.cost_estimate = round(len(executed_steps) * 0.001, 4)
        run.used_real_llm = provider is not None
        run.result = {
            # New fields (frontend)
            "goal": goal,
            "status": "completed",
            "steps": executed_steps,
            "cost_usd": round(len(executed_steps) * 0.001, 4),
            "iterations": len(executed_steps),
            "message": f"Simulation complete: {len(executed_steps)} steps",
            # Backward-compatible fields (existing tests)
            "simulated_steps": [s["step"] for s in executed_steps],
            "outcome": "success (simulated)",
            "side_effects": [],
            "mock_tools_used": list(mock_tools.keys()),
            "note": "Simulation complete — no real tools were called",
            "used_real_llm": provider is not None,
        }
        self._runs[run_id] = run
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

