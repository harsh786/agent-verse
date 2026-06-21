"""LangGraph-backed autonomous agent execution loop.

Graph topology:
  initialize → plan → execute → verify → (complete | replan | max_iterations_exceeded)

Three separate LLM roles (separate providers for independent prompt tuning):
  - Planner: receives goal + context → produces plan (list of steps)
  - Executor: receives one step → executes it (tool calls, code, etc.)
  - Verifier: receives step + result → determines success/failure

The loop is stateful (AgentState), checkpointable, and emits SSE events via
an optional event_callback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.prompts import EXECUTOR_SYSTEM, PLANNER_SYSTEM, VERIFIER_SYSTEM
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.providers.base import CompletionRequest, LLMProvider, Message
from app.tenancy.context import TenantContext

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

_DEFAULT_MAX_ITERATIONS = 15


def _parse_json_response(text: str, key: str | None = None) -> dict[str, Any]:
    """Extract JSON from an LLM response, tolerating minor formatting noise."""
    # Strip markdown code blocks if present
    text = re.sub(r"```(?:json)?\n?", "", text).strip()
    try:
        obj: dict[str, Any] = json.loads(text)
        return obj
    except json.JSONDecodeError:
        # Fallback: wrap in expected structure
        if key == "steps":
            return {"steps": [text]}
        return {"success": True, "reason": text}


class AgentLoop:
    """Stateful agent execution loop.

    Args:
        planner: LLMProvider used for goal decomposition.
        executor: LLMProvider used for step execution.
        verifier: LLMProvider used for result verification.
        max_iterations: Safety ceiling on plan/execute/verify cycles.
    """

    def __init__(
        self,
        *,
        planner: LLMProvider,
        executor: LLMProvider,
        verifier: LLMProvider,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._verifier = verifier
        self._max_iterations = max_iterations

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: TenantContext,
        initial_context: dict[str, Any] | None = None,
        event_callback: EventCallback | None = None,
    ) -> AgentState:
        """Execute the agent loop and return the final state."""
        state = AgentState(goal=goal, tenant_ctx=tenant_ctx, context=initial_context or {})

        async def emit(event: dict[str, Any]) -> None:
            state.events.append(event)
            if event_callback is not None:
                await event_callback(event)

        await emit({"type": "goal_started", "goal": goal})

        while state.iterations < self._max_iterations:
            state.iterations += 1

            # ── PLAN ────────────────────────────────────────────────────────
            state.status = GoalStatus.PLANNING
            plan = await self._plan(state)
            state.plan = plan
            await emit({"type": "plan_ready", "steps": plan, "iteration": state.iterations})

            # ── EXECUTE ─────────────────────────────────────────────────────
            state.status = GoalStatus.EXECUTING

            for step_desc in plan:
                step = StepResult(description=step_desc, status=StepStatus.RUNNING)
                state.steps.append(step)
                await emit({"type": "step_started", "step": step_desc})

                output = await self._execute(step_desc, state)
                step.output = output
                step.status = StepStatus.COMPLETE
                await emit({"type": "step_complete", "step": step_desc, "output": output})

            # ── VERIFY ──────────────────────────────────────────────────────
            state.status = GoalStatus.VERIFYING
            success, reason = await self._verify(state)
            state.verification_success = success
            state.verification_feedback = reason
            await emit({"type": "verification_done", "success": success, "reason": reason})

            if success:
                state.status = GoalStatus.COMPLETE
                await emit({"type": "goal_complete"})
                return state

            # Verification failed — will replan in the next iteration
            await emit({"type": "replanning", "reason": reason, "iteration": state.iterations})

        # Exceeded max iterations
        state.status = GoalStatus.FAILED
        state.error_message = f"Goal failed: max iterations ({self._max_iterations}) reached."
        await emit({"type": "goal_failed", "reason": state.error_message})
        return state

    async def _plan(self, state: AgentState) -> list[str]:
        context_summary = (
            f"Previous feedback: {state.verification_feedback}"
            if state.verification_feedback
            else ""
        )
        content = f"Goal: {state.goal}\n{context_summary}"
        req = CompletionRequest(
            messages=[
                Message(role="system", content=PLANNER_SYSTEM),
                Message(role="user", content=content),
            ],
            model="claude-opus-4-8",
        )
        resp = await self._planner.complete(req)
        parsed = _parse_json_response(resp.content, key="steps")
        steps: list[str] = parsed.get("steps", [resp.content])
        return steps if steps else [resp.content]

    async def _execute(self, step: str, state: AgentState) -> str:
        recent_outputs = "\n".join(s.output for s in state.steps[-3:] if s.output)
        content = (
            f"Step: {step}\nRecent context:\n{recent_outputs}"
            if recent_outputs
            else f"Step: {step}"
        )
        req = CompletionRequest(
            messages=[
                Message(role="system", content=EXECUTOR_SYSTEM),
                Message(role="user", content=content),
            ],
            model="claude-opus-4-8",
        )
        resp = await self._executor.complete(req)
        return resp.content

    async def _verify(self, state: AgentState) -> tuple[bool, str]:
        steps_summary = "\n".join(
            f"- {s.description}: {s.output}" for s in state.steps[-5:]
        )
        req = CompletionRequest(
            messages=[
                Message(role="system", content=VERIFIER_SYSTEM),
                Message(
                    role="user",
                    content=f"Goal: {state.goal}\nExecuted steps:\n{steps_summary}",
                ),
            ],
            model="claude-opus-4-8",
        )
        resp = await self._verifier.complete(req)
        parsed = _parse_json_response(resp.content)
        success: bool = bool(parsed.get("success", False))
        reason: str = str(parsed.get("reason", ""))
        return success, reason
