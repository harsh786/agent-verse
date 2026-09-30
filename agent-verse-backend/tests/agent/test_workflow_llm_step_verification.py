"""CORE-15: a workflow step answered by the LLM (no tool) is verified before it counts.

Non-connector DAG steps were marked ``complete`` from an "Execute this task"
LLM reply with no check at all, so a multi-agent/static workflow could report a
goal done on unverified prose.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.agent.workflow_executor import WorkflowExecutor
from app.agent.workflow_planner import WorkflowPlan, WorkflowStep
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-wfv", plan=PlanTier.ENTERPRISE, api_key_id="k")


class StepAndVerdictProvider:
    """Answers step prompts with *output* and verification prompts with *verdict*."""

    def __init__(self, output: str, verdict: str) -> None:
        self.output, self.verdict = output, verdict
        self.prompts: list[str] = []
        self._default_model = ""

    async def complete(self, request: Any) -> CompletionResponse:
        text = request.messages[-1].content
        self.prompts.append(text)
        content = self.verdict if text.startswith("Verify") else self.output
        return CompletionResponse(content=content, model="fake", input_tokens=1, output_tokens=1)


async def test_verified_llm_step_is_complete() -> None:
    provider = StepAndVerdictProvider("The capital of France is Paris.", '{"success": true}')
    step = WorkflowStep(id="s1", description="Name the capital of France")

    result = await WorkflowExecutor(provider=provider)._execute_step(step, T, prior_results={})

    assert result["status"] == "complete"
    assert result["verified"] is True
    assert any(p.startswith("Verify") for p in provider.prompts)


async def test_rejected_llm_step_is_unverified_and_the_run_incomplete() -> None:
    provider = StepAndVerdictProvider(
        "I have sent the email to the whole team.",
        '{"success": false, "reason": "no tool was available to send email"}',
    )
    plan = WorkflowPlan(goal="G", steps=[WorkflowStep(id="s1", description="Email the team")])

    result = await WorkflowExecutor(provider=provider).execute(plan, T)

    assert result["results"]["s1"]["status"] == "unverified"
    assert "no tool" in result["results"]["s1"]["reason"]
    assert result["status"] == "incomplete"


async def test_verifier_error_leaves_the_step_unverified() -> None:
    calls = {"n": 0}

    async def _complete(request: Any) -> CompletionResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return CompletionResponse(content="answer", model="m", input_tokens=1, output_tokens=1)
        raise RuntimeError("verifier down")

    provider = MagicMock()
    provider._default_model = ""
    provider.complete = AsyncMock(side_effect=_complete)
    step = WorkflowStep(id="s1", description="Do it")

    result = await WorkflowExecutor(provider=provider)._execute_step(step, T, prior_results={})

    assert result["status"] == "unverified"
