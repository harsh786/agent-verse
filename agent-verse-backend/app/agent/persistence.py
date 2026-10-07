"""Agent goal persistence — keeps trying until goal is achieved or explicitly stopped.

Design principles:
- Never give up silently: every termination reason is logged and auditable
- Smart retry: learn from failures, rotate strategies, decompose on repeated failure
- Human escalation: after configurable failures, ask human for guidance
- Exponential backoff: don't hammer LLM providers on repeated failures
- Open-source, no cloud deps
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.agent.fanout_ledger import FanoutParked
from app.observability.logging import get_logger
from app.reliability.goal_lifecycle import GoalCancelledError

logger = get_logger(__name__)


class RetryStrategy(StrEnum):
    SAME_APPROACH = "same_approach"  # Retry exact same plan
    DIFFERENT_TOOLS = "different_tools"  # Ask planner to use different tools
    DECOMPOSE = "decompose"  # Break goal into smaller sub-goals
    SIMPLIFY = "simplify"  # Reduce scope, achieve partial success
    HUMAN_GUIDANCE = "human_guidance"  # Ask human for clarification
    ESCALATE = "escalate"  # Mark for human takeover


@dataclass
class AttemptRecord:
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    attempt_number: int = 0
    strategy: RetryStrategy = RetryStrategy.SAME_APPROACH
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ended_at: str = ""
    success: bool = False
    failure_reason: str = ""
    iterations_used: int = 0
    cost_usd: float = 0.0
    tools_tried: list[str] = field(default_factory=list)
    goal_id: str = ""
    strategy_execution_id: str = ""
    strategy_id: str = "react"
    strategy_version: str = "1.0.0"
    profile_id: str = "legacy"
    profile_version: int = 1
    transition_reason: str = "started"
    checkpoint_reference: str = ""
    terminal_evidence: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""


@dataclass
class PersistenceConfig:
    """Configuration for the persistent retry engine."""

    # How many full goal attempts before giving up permanently
    max_attempts: int = 10
    # Iterations per attempt (passed to AgentGraph)
    iterations_per_attempt: int = 15
    # Seconds to wait between attempts (base for exponential backoff)
    base_backoff_seconds: float = 30.0
    # Maximum backoff cap (default 10 minutes)
    max_backoff_seconds: float = 600.0
    # After this many consecutive failures, switch strategy
    strategy_switch_after: int = 2
    # After this many total failures, escalate to human
    escalate_after_failures: int = 6
    # If True, emit SSE events about retry progress
    emit_retry_events: bool = True
    # Timeout for the entire persistence session (0 = no timeout)
    total_timeout_seconds: float = 0.0
    # Whether to decompose goal into sub-goals after repeated failure
    decompose_on_failure: bool = True
    strategy_id: str = "react"
    strategy_version: str = "1.0.0"
    profile_id: str = "legacy"
    profile_version: int = 1
    # How long an ESCALATE waits for a human to approve more attempts (0 = the
    # approval gateway's default timeout).
    escalation_timeout_seconds: float = 0.0

    @classmethod
    def from_runtime_profile(cls, profile: Any) -> PersistenceConfig:
        """Derive retry ceilings and strategy identity from the admitted profile."""
        patterns = profile.agent_patterns
        limits = profile.effective_limits
        return cls(
            max_attempts=min(patterns.max_persistence_attempts, limits.rounds),
            iterations_per_attempt=min(patterns.max_iterations, limits.rounds),
            total_timeout_seconds=float(limits.duration_seconds),
            strategy_id=profile.primary_strategy.strategy_id,
            strategy_version=profile.primary_strategy.adapter_version,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
        )


def _attempt_kwargs(agent: Any, goal_id: str, attempt_number: int) -> dict[str, Any]:
    """goal_id/attempt kwargs for ``agent.run``, limited to what its signature accepts."""
    if not goal_id:
        return {}
    import inspect

    try:
        params = inspect.signature(agent.run).parameters
    except (TypeError, ValueError):
        return {}
    accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs: dict[str, Any] = {}
    if accepts_any or "goal_id" in params:
        kwargs["goal_id"] = goal_id
    if accepts_any or "attempt" in params:
        kwargs["attempt"] = attempt_number
    return kwargs


class GoalPersistenceEngine:
    """Manages persistent goal execution with intelligent retry strategies.

    Wraps an AgentGraph and retries the goal using different
    strategies until success, human escalation, or permanent failure.
    """

    # How often a running attempt polls for an operator abort.
    _ABORT_POLL_SECONDS = 2.0

    def __init__(
        self,
        config: PersistenceConfig | None = None,
        db: Any = None,
        redis: Any = None,
        *,
        hitl_gateway: Any = None,
    ) -> None:
        self._config = config or PersistenceConfig()
        # ESCALATE asks a human through this gateway whether to keep retrying.
        self._hitl = hitl_gateway
        # Attempt number after which the escalation counter restarts (a human
        # approved more attempts at that point).
        self._escalation_base = 0
        self._attempts: list[AttemptRecord] = []
        self._db = db  # Optional async session factory for DB persistence
        # Operator controls written by POST /goals/{id}/persistence/{abort,
        # skip-strategy,inject-guidance}. Those endpoints used to write Redis keys
        # that nothing read; the engine now reads and honours them between attempts.
        self._redis = redis
        self._strategy_offset = 0
        self._human_guidance = ""

    # ── operator controls ────────────────────────────────────────────────────

    @staticmethod
    def control_key(kind: str, tenant_id: str, goal_id: str) -> str:
        return f"persistence_{kind}:{tenant_id}:{goal_id}"

    async def _take_control(self, kind: str, tenant_id: str, goal_id: str) -> str | None:
        """Read-and-consume one operator control key (None when absent/unavailable)."""
        if self._redis is None or not goal_id:
            return None
        key = self.control_key(kind, tenant_id, goal_id)
        try:
            value = await self._redis.get(key)
            if value is None:
                return None
            await self._redis.delete(key)
        except Exception as exc:
            logger.warning("persistence_control_read_failed", kind=kind, error=str(exc))
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def _apply_controls(self, tenant_id: str, goal_id: str) -> bool:
        """Apply pending operator controls. Returns True when an abort was requested."""
        if await self._take_control("abort", tenant_id, goal_id) is not None:
            return True
        if await self._take_control("skip_strategy", tenant_id, goal_id) is not None:
            self._strategy_offset += 1
        guidance = await self._take_control("guidance", tenant_id, goal_id)
        if guidance:
            self._human_guidance = guidance[:5000]
        return False

    async def _sleep_honouring_abort(self, seconds: float, tenant_id: str, goal_id: str) -> bool:
        """Back off, polling for an abort. Returns True when aborted mid-wait."""
        if self._redis is None or not goal_id:
            await asyncio.sleep(seconds)
            return False
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(5.0, remaining))
            try:
                if await self._redis.get(self.control_key("abort", tenant_id, goal_id)):
                    return True
            except Exception:
                pass

    async def _run_abortable(self, coro: Any, tenant_id: str, goal_id: str) -> tuple[bool, Any]:
        """Run one attempt, cancelling it when an operator abort arrives.

        Returns ``(aborted, state)``. Aborts used to be read only between
        attempts, so a running attempt went on to completion (CORE-14).
        """
        if self._redis is None or not goal_id:
            return False, await coro
        task = asyncio.ensure_future(coro)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self._ABORT_POLL_SECONDS)
                if task in done:
                    return False, task.result()
                if await self._take_control("abort", tenant_id, goal_id) is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    return True, None
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            raise

    async def _escalate(
        self, goal: str, tenant_ctx: Any, goal_id: str, emit: Any
    ) -> bool:
        """Ask a human whether to keep retrying. True = approved, keep going.

        ESCALATE used to emit "escalating to human" and stop — nobody was asked.
        Now an approval request is filed (durably when the gateway supports it)
        and awaited; without a gateway the engine says honestly that it gives up.
        """
        failures = len(self._attempts)
        base = {
            "attempts": failures,
            "total_cost_usd": self.total_cost_usd,
        }
        gateway = self._hitl
        if gateway is None:
            await emit(
                {
                    "type": "persistence_gave_up",
                    "reason": (
                        f"Goal failed {failures} times; no human approver is configured, "
                        "so retrying stops here"
                    ),
                    **base,
                }
            )
            return False
        action = (
            f"Persistent goal failed {failures} times — approve to keep retrying: {goal[:300]}"
        )
        try:
            durable = getattr(gateway, "request_approval_async", None)
            if durable is not None and asyncio.iscoroutinefunction(durable):
                req_id = str(
                    await durable(
                        goal_id=goal_id,
                        action=action,
                        risk_level="medium",
                        tenant_ctx=tenant_ctx,
                        require_persisted=True,
                    )
                )
            else:
                req_id = str(
                    gateway.request_approval(
                        goal_id=goal_id, action=action, risk_level="medium", tenant_ctx=tenant_ctx
                    )
                )
        except Exception as exc:
            await emit(
                {
                    "type": "persistence_gave_up",
                    "reason": (
                        f"Goal failed {failures} times; the escalation could not be filed "
                        f"({type(exc).__name__}), so retrying stops here"
                    ),
                    **base,
                }
            )
            return False
        await emit(
            {
                "type": "persistence_escalating",
                "request_id": req_id,
                "reason": (
                    f"Goal failed {failures} times — waiting for a human to approve more attempts"
                ),
                **base,
            }
        )
        timeout = self._config.escalation_timeout_seconds or None
        status = await gateway.wait_for_approval(req_id, tenant_ctx=tenant_ctx, timeout=timeout)
        if str(getattr(status, "value", status)) == "approved":
            await emit({"type": "persistence_escalation_approved", "request_id": req_id, **base})
            return True
        await emit(
            {
                "type": "persistence_escalation_declined",
                "request_id": req_id,
                "status": str(getattr(status, "value", status)),
                **base,
            }
        )
        return False

    @property
    def attempts(self) -> list[AttemptRecord]:
        return list(self._attempts)

    @property
    def consecutive_failures(self) -> int:
        """Count trailing consecutive failures."""
        count = 0
        for attempt in reversed(self._attempts):
            if not attempt.success:
                count += 1
            else:
                break
        return count

    @property
    def total_cost_usd(self) -> float:
        return sum(a.cost_usd for a in self._attempts)

    def _pick_strategy(self, attempt_number: int) -> RetryStrategy:
        """Choose the next retry strategy based on failure history."""
        failures = self.consecutive_failures

        if attempt_number - self._escalation_base >= self._config.escalate_after_failures:
            return RetryStrategy.ESCALATE

        if failures >= self._config.strategy_switch_after or self._strategy_offset:
            # Rotate through strategies. An operator skip advances the rotation by one;
            # from SAME_APPROACH the first skip lands on DIFFERENT_TOOLS (index 0).
            base = failures // self._config.strategy_switch_after
            cycle = base + self._strategy_offset if base else self._strategy_offset - 1
            strategies = [
                RetryStrategy.DIFFERENT_TOOLS,
                RetryStrategy.SIMPLIFY,
                RetryStrategy.DECOMPOSE
                if self._config.decompose_on_failure
                else RetryStrategy.DIFFERENT_TOOLS,
                RetryStrategy.HUMAN_GUIDANCE,
            ]
            return strategies[cycle % len(strategies)]

        return RetryStrategy.SAME_APPROACH

    def _backoff_seconds(self, attempt_number: int) -> float:
        """Exponential backoff with jitter: base * 2^attempt ± 20% jitter."""
        raw = self._config.base_backoff_seconds * (2 ** (attempt_number - 1))
        capped = min(raw, self._config.max_backoff_seconds)
        # Add 0-20% additive jitter to avoid thundering herd
        import random

        jitter = capped * random.uniform(0, 0.2)
        return float(max(1.0, capped + jitter))

    def _build_enriched_goal(
        self,
        original_goal: str,
        strategy: RetryStrategy,
        last_failure: str,
    ) -> str:
        """Enrich the goal prompt with strategy hints (and any operator guidance)."""
        enriched = self._strategy_enriched_goal(original_goal, strategy, last_failure)
        if self._human_guidance:
            enriched = f"{enriched}\n\n[Human operator guidance: {self._human_guidance}]"
        return enriched

    def _strategy_enriched_goal(
        self,
        original_goal: str,
        strategy: RetryStrategy,
        last_failure: str,
    ) -> str:
        if strategy == RetryStrategy.SAME_APPROACH:
            return f"{original_goal}\n\n[Previous attempt failed: {last_failure}. Try again.]"
        elif strategy == RetryStrategy.DIFFERENT_TOOLS:
            return (
                f"{original_goal}\n\n"
                f"[Previous attempt failed using those tools: {last_failure}. "
                f"Use a DIFFERENT approach or different tools this time.]"
            )
        elif strategy == RetryStrategy.SIMPLIFY:
            return (
                f"{original_goal}\n\n"
                f"[Multiple attempts failed. Simplify: achieve the most important "
                f"part of this goal that IS achievable right now. Skip optional parts. "
                f"Previous failure: {last_failure}]"
            )
        elif strategy == RetryStrategy.DECOMPOSE:
            return (
                f"{original_goal}\n\n"
                f"[This goal has failed {self.consecutive_failures} times. "
                f"Break it into the smallest possible first step and just do that. "
                f"Previous failure: {last_failure}]"
            )
        elif strategy == RetryStrategy.HUMAN_GUIDANCE:
            return (
                f"{original_goal}\n\n"
                f"[IMPORTANT: This goal has failed {len(self._attempts)} times. "
                f"Before attempting, explicitly describe what you plan to do and why "
                f"previous attempts failed. Latest failure: {last_failure}. "
                f"If you cannot determine a viable path, say so clearly.]"
            )
        return original_goal

    async def _write_attempt_start(
        self,
        goal_id: str,
        tenant_id: str,
        attempt_num: int,
        strategy: str,
        enriched_goal: str,
        backoff: int,
        strategy_id: str = "react",
        strategy_version: str = "1.0.0",
        profile_id: str = "legacy",
        profile_version: int = 1,
        strategy_execution_id: str = "",
        idempotency_key: str = "",
    ) -> str:
        """Write attempt start record to DB. Returns attempt record ID."""
        stable_key = idempotency_key or f"goal-attempt:{tenant_id}:{goal_id}:{attempt_num}"
        attempt_id = uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex
        if self._db is None:
            return attempt_id
        try:
            from sqlalchemy import text as _t

            async with self._db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": tenant_id},
                )
                result = await session.execute(
                    _t("""
                    INSERT INTO goal_attempts
                        (id, goal_id, tenant_id, attempt_number, strategy,
                         enriched_goal, started_at, backoff_seconds,
                         strategy_execution_id, strategy_id, strategy_version,
                         profile_id, profile_version, transition_reason,
                         budget_consumed, terminal_evidence, idempotency_key, version)
                    VALUES (:id, :goal, :tenant, :num, :strat, :goal_text, NOW(), :backoff,
                            NULLIF(:execution, ''), :strategy_id, :strategy_version,
                            :profile_id, :profile_version, 'started',
                            '{}'::jsonb, '{}'::jsonb, :idempotency_key, 1)
                    ON CONFLICT (tenant_id, goal_id, attempt_number) DO NOTHING
                    RETURNING id
                """),
                    {
                        "id": attempt_id,
                        "goal": goal_id,
                        "tenant": tenant_id,
                        "num": attempt_num,
                        "strat": strategy,
                        "goal_text": enriched_goal[:2000],
                        "backoff": backoff,
                        "execution": strategy_execution_id,
                        "strategy_id": strategy_id,
                        "strategy_version": strategy_version,
                        "profile_id": profile_id,
                        "profile_version": profile_version,
                        "idempotency_key": stable_key,
                    },
                )
                accepted_id = result.scalar_one_or_none()
                if accepted_id is not None:
                    await session.commit()
                    return str(accepted_id)
                existing = await session.execute(
                    _t(
                        "SELECT id FROM goal_attempts "
                        "WHERE tenant_id=:tenant AND goal_id=:goal AND attempt_number=:num"
                    ),
                    {"tenant": tenant_id, "goal": goal_id, "num": attempt_num},
                )
                existing_id = str(existing.scalar_one())
                await session.commit()
                return existing_id
        except Exception as exc:
            logger.warning("attempt_write_failed", error=str(exc))
        return attempt_id

    async def _write_attempt_end(
        self,
        attempt_id: str,
        tenant_id: str,
        succeeded: bool,
        failure_reason: str,
        iterations: int,
        cost_usd: float,
        checkpoint_reference: str = "",
        terminal_evidence: dict[str, Any] | None = None,
    ) -> None:
        """Update attempt record with completion data."""
        if self._db is None or not attempt_id:
            return
        try:
            import json

            from sqlalchemy import text as _t

            async with self._db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": tenant_id},
                )
                await session.execute(
                    _t("""
                    UPDATE goal_attempts
                    SET ended_at = NOW(),
                        succeeded = :ok,
                        failure_reason = :reason,
                        iterations_used = :iters,
                        cost_usd = :cost,
                        transition_reason = :transition_reason,
                        checkpoint_reference = NULLIF(:checkpoint_reference, ''),
                        budget_consumed = CAST(:budget_consumed AS jsonb),
                        terminal_evidence = CAST(:terminal_evidence AS jsonb),
                        version = version + 1
                    WHERE id = :id AND tenant_id = :tenant
                """),
                    {
                        "id": attempt_id,
                        "tenant": tenant_id,
                        "ok": succeeded,
                        "reason": failure_reason[:500] if failure_reason else "",
                        "iters": iterations,
                        "cost": cost_usd,
                        "transition_reason": "completed" if succeeded else "failed",
                        "checkpoint_reference": checkpoint_reference,
                        "budget_consumed": json.dumps({"cost_usd": cost_usd}),
                        "terminal_evidence": json.dumps(terminal_evidence or {}),
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.warning("attempt_update_failed", error=str(exc))

    async def run(
        self,
        *,
        goal: str,
        agent_factory: Any,  # Callable[[], AgentGraph] or AgentGraph instance
        tenant_ctx: Any,
        event_callback: Any = None,
        goal_id: str = "",
    ) -> tuple[bool, list[AttemptRecord]]:
        """Run goal with persistence until success, escalation, or exhaustion.

        Args:
            goal: The original goal text
            agent_factory: Callable that returns a fresh AgentGraph per attempt
            tenant_ctx: TenantContext
            event_callback: Async callback for SSE events
            goal_id: Optional goal ID for DB persistence writes

        Returns:
            (success: bool, attempts: list[AttemptRecord])
        """
        config = self._config
        session_start = time.monotonic()
        last_failure = ""
        tenant_id = getattr(tenant_ctx, "tenant_id", "")

        async def emit(event: dict[str, Any]) -> None:
            if event_callback and config.emit_retry_events:
                with contextlib.suppress(Exception):
                    await event_callback(event)

        for attempt_number in range(1, config.max_attempts + 1):
            # Operator controls (abort / skip-strategy / guidance) — between attempts.
            if await self._apply_controls(tenant_id, goal_id):
                await emit({"type": "persistence_aborted", "attempts": len(self._attempts)})
                logger.info("persistent_goal_aborted", goal_id=goal_id)
                return False, self._attempts
            # Check total timeout
            if config.total_timeout_seconds > 0:
                elapsed = time.monotonic() - session_start
                if elapsed >= config.total_timeout_seconds:
                    await emit(
                        {
                            "type": "persistence_timeout",
                            "elapsed_seconds": elapsed,
                            "attempts": attempt_number - 1,
                        }
                    )
                    break

            strategy = self._pick_strategy(attempt_number)

            if strategy == RetryStrategy.ESCALATE:
                if not await self._escalate(goal, tenant_ctx, goal_id, emit):
                    break
                # A human approved more attempts: restart the escalation window.
                self._escalation_base = attempt_number - 1
                strategy = self._pick_strategy(attempt_number)

            # Wait with exponential backoff (skip on first attempt)
            if attempt_number > 1:
                backoff = self._backoff_seconds(attempt_number - 1)
                await emit(
                    {
                        "type": "persistence_waiting",
                        "attempt": attempt_number,
                        "backoff_seconds": round(backoff, 1),
                        "strategy": strategy,
                        "max_attempts": config.max_attempts,
                    }
                )
                if await self._sleep_honouring_abort(backoff, tenant_id, goal_id):
                    await emit({"type": "persistence_aborted", "attempts": len(self._attempts)})
                    return False, self._attempts
                # Controls that arrived during the backoff apply to this attempt.
                if await self._apply_controls(tenant_id, goal_id):
                    await emit({"type": "persistence_aborted", "attempts": len(self._attempts)})
                    return False, self._attempts
                strategy = self._pick_strategy(attempt_number)

            attempt = AttemptRecord(
                attempt_number=attempt_number,
                strategy=strategy,
                goal_id=goal_id,
                strategy_id=config.strategy_id,
                strategy_version=config.strategy_version,
                profile_id=config.profile_id,
                profile_version=config.profile_version,
                idempotency_key=f"goal-attempt:{tenant_id}:{goal_id}:{attempt_number}",
            )
            self._attempts.append(attempt)

            enriched_goal = self._build_enriched_goal(goal, strategy, last_failure)
            backoff_used = (
                0 if attempt_number == 1 else int(self._backoff_seconds(attempt_number - 1))
            )

            # Write attempt start to DB
            _db_attempt_id = await self._write_attempt_start(
                goal_id=goal_id,
                tenant_id=tenant_id,
                attempt_num=attempt_number,
                strategy=str(strategy),
                enriched_goal=enriched_goal,
                backoff=backoff_used,
                strategy_id=attempt.strategy_id,
                strategy_version=attempt.strategy_version,
                profile_id=attempt.profile_id,
                profile_version=attempt.profile_version,
                strategy_execution_id=attempt.strategy_execution_id,
                idempotency_key=attempt.idempotency_key,
            )

            await emit(
                {
                    "type": "persistence_attempt_start",
                    "attempt": attempt_number,
                    "max_attempts": config.max_attempts,
                    "strategy": strategy,
                    "goal_modified": enriched_goal != goal,
                }
            )

            try:
                # Get a fresh agent for this attempt
                if callable(agent_factory) and not hasattr(agent_factory, "run"):
                    agent = agent_factory()
                else:
                    agent = agent_factory  # Already an agent instance

                # Each attempt runs under the goal's id so checkpoints, events and
                # evaluations attach to the real goal row (it used to run with no
                # goal_id). ``attempt`` isolates the attempt's checkpoint thread so a
                # retry does not resume the previous attempt's failed state.
                aborted, state = await self._run_abortable(
                    agent.run(
                        goal=enriched_goal,
                        tenant_ctx=tenant_ctx,
                        event_callback=event_callback,
                        **_attempt_kwargs(agent, goal_id, attempt_number),
                    ),
                    tenant_id,
                    goal_id,
                )
                if aborted:
                    attempt.failure_reason = "aborted"
                    attempt.ended_at = datetime.now(UTC).isoformat()
                    await self._write_attempt_end(
                        attempt_id=_db_attempt_id,
                        tenant_id=tenant_id,
                        succeeded=False,
                        failure_reason="aborted",
                        iterations=attempt.iterations_used,
                        cost_usd=attempt.cost_usd,
                    )
                    await emit(
                        {
                            "type": "persistence_aborted",
                            "attempts": len(self._attempts),
                            "during_attempt": attempt_number,
                        }
                    )
                    logger.info("persistent_goal_aborted_mid_attempt", goal_id=goal_id)
                    return False, self._attempts
                attempt.ended_at = datetime.now(UTC).isoformat()
                attempt.iterations_used = getattr(state, "iterations", 0)
                attempt.cost_usd = getattr(state, "context", {}).get("total_cost_usd", 0.0)
                attempt.success = getattr(state, "verification_success", False) or (
                    str(getattr(state, "status", "")).lower()
                    in ("complete", "completed", "success")
                )
                attempt.checkpoint_reference = str(
                    getattr(state, "context", {}).get("checkpoint_reference", "")
                )
                attempt.terminal_evidence = {
                    "status": str(getattr(state, "status", "")),
                    "verification_success": bool(getattr(state, "verification_success", False)),
                }

                # Write attempt end to DB
                await self._write_attempt_end(
                    attempt_id=_db_attempt_id,
                    tenant_id=tenant_id,
                    succeeded=attempt.success,
                    failure_reason=attempt.failure_reason,
                    iterations=attempt.iterations_used,
                    cost_usd=attempt.cost_usd,
                    checkpoint_reference=attempt.checkpoint_reference,
                    terminal_evidence=attempt.terminal_evidence,
                )

                if attempt.success:
                    await emit(
                        {
                            "type": "persistence_goal_achieved",
                            "attempt": attempt_number,
                            "total_attempts": len(self._attempts),
                            "total_cost_usd": self.total_cost_usd,
                            "strategy_that_worked": strategy,
                        }
                    )
                    logger.info(
                        "persistent_goal_achieved",
                        goal=goal[:100],
                        attempt=attempt_number,
                        strategy=strategy,
                    )
                    return True, self._attempts

                # Record failure
                last_failure = (
                    getattr(state, "error_message", "")
                    or getattr(state, "verification_feedback", "")
                    or "verification failed"
                )
                attempt.failure_reason = last_failure

                # Write failure end to DB
                await self._write_attempt_end(
                    attempt_id=_db_attempt_id,
                    tenant_id=tenant_id,
                    succeeded=False,
                    failure_reason=last_failure,
                    iterations=attempt.iterations_used,
                    cost_usd=attempt.cost_usd,
                )

                await emit(
                    {
                        "type": "persistence_attempt_failed",
                        "attempt": attempt_number,
                        "reason": last_failure[:200],
                        "iterations_used": attempt.iterations_used,
                        "remaining_attempts": config.max_attempts - attempt_number,
                    }
                )
                logger.warning(
                    "persistent_goal_attempt_failed",
                    goal=goal[:100],
                    attempt=attempt_number,
                    reason=last_failure[:100],
                    strategy=strategy,
                )

            except asyncio.CancelledError:
                attempt.failure_reason = "cancelled"
                attempt.ended_at = datetime.now(UTC).isoformat()
                await self._write_attempt_end(
                    attempt_id=_db_attempt_id,
                    tenant_id=tenant_id,
                    succeeded=False,
                    failure_reason="cancelled",
                    iterations=attempt.iterations_used,
                    cost_usd=attempt.cost_usd,
                )
                await emit({"type": "persistence_cancelled", "attempt": attempt_number})
                raise
            except (GoalCancelledError, PermissionError) as exc:
                # An operator cancel / emergency stop (raised by the step gate) or a
                # governance denial is final: retrying re-planned through the stop
                # (an LLM call per attempt) and re-ran the same denial, then ended
                # "failed" instead of stopped (CORE-13).
                reason = "stopped" if isinstance(exc, GoalCancelledError) else "denied"
                attempt.failure_reason = f"{reason}: {exc}"[:200]
                attempt.ended_at = datetime.now(UTC).isoformat()
                await self._write_attempt_end(
                    attempt_id=_db_attempt_id,
                    tenant_id=tenant_id,
                    succeeded=False,
                    failure_reason=f"{reason}: {exc}"[:500],
                    iterations=attempt.iterations_used,
                    cost_usd=attempt.cost_usd,
                )
                await emit(
                    {
                        "type": "persistence_stopped",
                        "attempt": attempt_number,
                        "reason": reason,
                        "detail": str(exc)[:200],
                    }
                )
                logger.info("persistent_goal_stopped", goal_id=goal_id, reason=reason)
                raise
            except FanoutParked:
                # The attempt parked waiting for its sub-goals (a01-F006-05): not a
                # failure and nothing to retry — the runner releases the slot and
                # the last sub-goal re-queues the goal.
                await self._write_attempt_end(
                    attempt_id=_db_attempt_id,
                    tenant_id=tenant_id,
                    succeeded=False,
                    failure_reason="waiting_children",
                    iterations=attempt.iterations_used,
                    cost_usd=attempt.cost_usd,
                )
                raise
            except Exception as exc:
                attempt.failure_reason = str(exc)[:200]
                attempt.ended_at = datetime.now(UTC).isoformat()
                last_failure = str(exc)
                await self._write_attempt_end(
                    attempt_id=_db_attempt_id,
                    tenant_id=tenant_id,
                    succeeded=False,
                    failure_reason=str(exc)[:500],
                    iterations=attempt.iterations_used,
                    cost_usd=attempt.cost_usd,
                )
                await emit(
                    {
                        "type": "persistence_attempt_error",
                        "attempt": attempt_number,
                        "error": str(exc)[:200],
                    }
                )
                logger.warning("persistent_goal_error", error=str(exc), attempt=attempt_number)

        # All attempts exhausted
        await emit(
            {
                "type": "persistence_exhausted",
                "total_attempts": len(self._attempts),
                "total_cost_usd": self.total_cost_usd,
                "last_failure": last_failure[:200],
            }
        )
        return False, self._attempts
