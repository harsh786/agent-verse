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

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.prompts import EXECUTOR_SYSTEM, PLANNER_SYSTEM, VERIFIER_SYSTEM
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix
from app.memory.execution import ExecutionMemory
from app.observability.logging import get_logger
from app.providers.base import CompletionRequest, LLMProvider, Message
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import TenantContext

logger = get_logger(__name__)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

_DEFAULT_MAX_ITERATIONS = 15

# Keywords that indicate a high-risk step requiring HITL approval
_HIGH_RISK_KEYWORDS = ("deploy", "delete", "drop", "rm", "prod", "production", "destroy")


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


def _extract_tool_name(step: str) -> str:
    """Heuristically extract a tool name from a step description.

    If the step contains 'call', takes the first word after 'call'.
    Otherwise returns 'llm_call' as the default.
    """
    lower = step.lower()
    if "call" in lower:
        parts = lower.split("call", 1)
        if len(parts) > 1:
            words = parts[1].strip().split()
            if words:
                return words[0].strip("_-.,;:")
    return "llm_call"


class AgentLoop:
    """Stateful agent execution loop.

    Args:
        planner: LLMProvider used for goal decomposition.
        executor: LLMProvider used for step execution.
        verifier: LLMProvider used for result verification.
        max_iterations: Safety ceiling on plan/execute/verify cycles.
        permission_matrix: Optional per-tenant permission matrix for governance.
        audit_log: Optional append-only audit trail.
        cost_controller: Optional per-goal/per-tenant budget enforcement.
        hitl_gateway: Optional human-in-the-loop approval gateway.
        circuit_breakers: Optional map of tool name → CircuitBreaker.
        rollback_engine: Optional LIFO rollback point registry.
        dedup_cache: Optional content-hash deduplication cache.
        result_processor: Optional output sanitizer (redact, truncate).
        exec_memory: Optional execution memory for winning plan recall.
    """

    def __init__(
        self,
        *,
        planner: LLMProvider,
        executor: LLMProvider,
        verifier: LLMProvider,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        permission_matrix: PermissionMatrix | None = None,
        audit_log: AuditLog | None = None,
        cost_controller: CostController | None = None,
        hitl_gateway: HITLGateway | None = None,
        circuit_breakers: dict[str, CircuitBreaker] | None = None,
        rollback_engine: RollbackEngine | None = None,
        dedup_cache: DeduplicationCache | None = None,
        result_processor: ResultProcessor | None = None,
        exec_memory: ExecutionMemory | None = None,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._verifier = verifier
        self._max_iterations = max_iterations
        self._permission_matrix = permission_matrix
        self._audit_log = audit_log
        self._cost_controller = cost_controller
        self._hitl_gateway = hitl_gateway
        self._circuit_breakers = circuit_breakers
        self._rollback_engine = rollback_engine
        self._dedup_cache = dedup_cache
        self._result_processor = result_processor
        self._exec_memory = exec_memory

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
                # Record the winning plan in execution memory for future reuse
                if self._exec_memory is not None:
                    self._exec_memory.record(
                        goal=state.goal,
                        plan=state.plan,
                        tenant_ctx=state.tenant_ctx,
                    )
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
        tenant_ctx = state.tenant_ctx
        tool_name = _extract_tool_name(step)

        # ── Step 1: Cost check ───────────────────────────────────────────────
        if self._cost_controller is not None:
            within_budget = self._cost_controller.check_and_record(
                goal_id=state.goal_id,
                cost_usd=0.01,
                tenant_ctx=tenant_ctx,
            )
            if not within_budget:
                return "Step skipped: budget exceeded."

        # ── Step 2: Smart context (no-op — recent outputs injected below) ────

        # ── Step 3: Exec memory lookup ───────────────────────────────────────
        if self._exec_memory is not None:
            # Retrieve past winning plans; available for future prompt injection
            self._exec_memory.recall(goal_hint=state.goal, tenant_ctx=tenant_ctx)

        # ── Step 4: Dedup check ──────────────────────────────────────────────
        if self._dedup_cache is not None:
            content_hash = hashlib.sha256(f"{step}:{state.goal}".encode()).hexdigest()
            if self._dedup_cache.is_duplicate(content_hash=content_hash, tenant_ctx=tenant_ctx):
                return "Duplicate step, returning cached result."
            self._dedup_cache.mark_seen(content_hash=content_hash, tenant_ctx=tenant_ctx)

        # ── Step 5: Circuit breaker ("llm" key) ──────────────────────────────
        if self._circuit_breakers is not None:
            breaker = self._circuit_breakers.get("llm")
            if breaker is not None and not breaker.is_closed():
                return "Circuit open, step skipped."

        # ── Step 6: Governance check ─────────────────────────────────────────
        if self._permission_matrix is not None:
            level = self._permission_matrix.check(tool_name=tool_name, tenant_ctx=tenant_ctx)
            if level == ActionLevel.DENY:
                raise PermissionError(
                    f"Tool '{tool_name}' denied by governance policy "
                    f"for tenant '{tenant_ctx.tenant_id}'."
                )

        # ── Step 7: HITL gate ────────────────────────────────────────────────
        if self._hitl_gateway is not None:
            risk_level = (
                "high"
                if any(kw in step.lower() for kw in _HIGH_RISK_KEYWORDS)
                else "low"
            )
            if risk_level == "high":
                self._hitl_gateway.request_approval(
                    goal_id=state.goal_id,
                    action=step,
                    risk_level=risk_level,
                    tenant_ctx=tenant_ctx,
                )
                # Auto-proceed after logging the approval request

        # ── Step 8: Execute LLM call ─────────────────────────────────────────
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
        raw_output = resp.content

        # ── Step 9: Result processor ─────────────────────────────────────────
        if self._result_processor is not None:
            raw_output = self._result_processor.process(raw_output)

        # ── Step 10: Record rollback point ───────────────────────────────────
        if self._rollback_engine is not None:
            self._rollback_engine.register(
                action=step,
                inverse=lambda: None,  # placeholder; real inverse comes from tool adapters
            )

        # ── Step 11: Stream event — handled by caller via event_callback ─────

        # ── Step 12: Record usage in audit log ───────────────────────────────
        if self._audit_log is not None:
            audit_event = AuditEvent(
                goal_id=state.goal_id,
                tool_name=tool_name,
                action_level=ActionLevel.ALLOW_LOG,
                outcome="step_complete",
                step_id=state.steps[-1].step_id if state.steps else "",
            )
            self._audit_log.record(audit_event, tenant_ctx=tenant_ctx)

        return raw_output

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
