"""AgentTestHarness — test agent behavior with mocked tools.

Allows testing agent plans and execution in isolation without running
a full server. Uses FakeProvider for LLM and MockMCPClient for tools.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TestResult:
    __test__ = False  # Prevent pytest from collecting this as a test class
    success: bool
    events: list[dict[str, Any]] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    plan_steps: list[str] = field(default_factory=list)
    output: str = ""
    cost_usd: float = 0.0
    iterations: int = 0
    error: str | None = None

    def get_events_by_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == event_type]


class AgentTestHarness:
    """Run agent goals with mocked tools for testing.

    Usage:
        harness = AgentTestHarness()
        harness.set_mock_tool("jira.search_issues", [{"id": "BAU-1"}])
        result = await harness.run_goal("List open Jira issues")
        harness.assert_tool_called("jira.search_issues", result)
        harness.assert_goal_completed(result)
    """

    def __init__(
        self,
        planner_responses: list[str] | None = None,
        executor_responses: list[str] | None = None,
        verifier_responses: list[str] | None = None,
    ) -> None:
        self._mock_tools: dict[str, Any] = {}
        self._planner_responses = planner_responses or ['{"steps": ["Execute the goal"]}']
        self._executor_responses = executor_responses or ["Task executed successfully"]
        self._verifier_responses = verifier_responses or ['{"success": true, "reason": "Goal achieved"}']

    def set_mock_tool(self, tool_name: str, response: Any) -> "AgentTestHarness":
        """Configure a mock response for a specific tool."""
        self._mock_tools[tool_name] = response
        return self  # Enable chaining

    def set_planner_responses(self, responses: list[str]) -> "AgentTestHarness":
        self._planner_responses = responses
        return self

    async def run_goal(
        self,
        goal: str,
        *,
        tenant_id: str = "test-tenant",
        dry_run: bool = False,
    ) -> TestResult:
        """Run a goal and return the result with event trace."""
        from app.agent.graph import AgentGraph
        from app.enterprise.simulation import MockMCPClient
        from app.intelligence.guardrails import GuardrailChecker
        from app.providers.fake import FakeProvider
        from app.reliability.dedup import DeduplicationCache
        from app.reliability.result_processor import ResultProcessor
        from app.reliability.rollback import RollbackEngine
        from app.tenancy.context import PlanTier, TenantContext

        events: list[dict[str, Any]] = []

        async def event_callback(event: dict[str, Any]) -> None:
            events.append(event)

        planner = FakeProvider(responses=self._planner_responses)
        executor = FakeProvider(responses=self._executor_responses)
        verifier = FakeProvider(responses=self._verifier_responses)
        mock_mcp = MockMCPClient(self._mock_tools)

        graph = AgentGraph(
            planner=planner,
            executor=executor,
            verifier=verifier,
            mcp_client=mock_mcp,
            result_processor=ResultProcessor(),
            dedup_cache=DeduplicationCache(),
            rollback_engine=RollbackEngine(),
            guardrail_checker=GuardrailChecker(),
        )

        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="test")

        try:
            state = await graph.run(
                goal=goal,
                tenant_ctx=ctx,
                event_callback=event_callback,
            )
            tools_called = [
                str(e.get("tool_name") or e.get("tool") or "")
                for e in events if e.get("type") == "tool_call_complete"
            ]
            plan_steps: list[str] = []
            for e in events:
                if e.get("type") == "plan_ready":
                    plan_steps = e.get("steps", [])

            return TestResult(
                success=state.verification_success,
                events=events,
                tools_called=tools_called,
                plan_steps=plan_steps,
                output=state.steps[-1].output if state.steps else "",
                cost_usd=state.context.get("total_cost_usd", 0.0),
                iterations=state.iterations,
            )
        except Exception as exc:
            return TestResult(
                success=False,
                events=events,
                error=str(exc),
            )

    def assert_tool_called(self, tool_name: str, result: TestResult, times: int | None = None) -> None:
        called = [t for t in result.tools_called if tool_name in t]
        if times is not None:
            assert len(called) == times, (
                f"Expected tool '{tool_name}' to be called {times} times, "
                f"but it was called {len(called)} times"
            )
        else:
            assert called, (
                f"Expected tool '{tool_name}' to be called, "
                f"but it was not. Tools called: {result.tools_called}"
            )

    def assert_tool_not_called(self, tool_name: str, result: TestResult) -> None:
        called = [t for t in result.tools_called if tool_name in t]
        assert not called, f"Expected tool '{tool_name}' NOT to be called, but it was"

    def assert_goal_completed(self, result: TestResult) -> None:
        assert result.success, (
            f"Expected goal to complete successfully, but got error: {result.error}. "
            f"Events: {[e['type'] for e in result.events]}"
        )

    def assert_output_contains(self, text: str, result: TestResult) -> None:
        all_output = " ".join(str(e.get("output", "")) for e in result.events)
        assert text.lower() in all_output.lower(), (
            f"Expected output to contain '{text}', but got: {all_output[:200]}"
        )
