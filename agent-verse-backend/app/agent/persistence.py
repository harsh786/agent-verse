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
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


class RetryStrategy(StrEnum):
    SAME_APPROACH      = "same_approach"       # Retry exact same plan
    DIFFERENT_TOOLS    = "different_tools"     # Ask planner to use different tools
    DECOMPOSE          = "decompose"           # Break goal into smaller sub-goals
    SIMPLIFY           = "simplify"            # Reduce scope, achieve partial success
    HUMAN_GUIDANCE     = "human_guidance"      # Ask human for clarification
    ESCALATE           = "escalate"            # Mark for human takeover


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


@dataclass
class PersistenceConfig:
    """Configuration for the persistent retry engine."""
    # How many full goal attempts before giving up permanently
    max_attempts: int = 10
    # Iterations per attempt (passed to AgentGraph/AgentLoop)
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


class GoalPersistenceEngine:
    """Manages persistent goal execution with intelligent retry strategies.

    Wraps an AgentGraph/AgentLoop and retries the goal using different
    strategies until success, human escalation, or permanent failure.
    """

    def __init__(self, config: PersistenceConfig | None = None) -> None:
        self._config = config or PersistenceConfig()
        self._attempts: list[AttemptRecord] = []

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

        if attempt_number >= self._config.escalate_after_failures:
            return RetryStrategy.ESCALATE

        if failures >= self._config.strategy_switch_after:
            # Rotate through strategies
            cycle = failures // self._config.strategy_switch_after
            strategies = [
                RetryStrategy.DIFFERENT_TOOLS,
                RetryStrategy.SIMPLIFY,
                RetryStrategy.DECOMPOSE if self._config.decompose_on_failure
                    else RetryStrategy.DIFFERENT_TOOLS,
                RetryStrategy.HUMAN_GUIDANCE,
            ]
            return strategies[cycle % len(strategies)]

        return RetryStrategy.SAME_APPROACH

    def _backoff_seconds(self, attempt_number: int) -> float:
        """Exponential backoff with jitter: base * 2^attempt ± 20% jitter."""
        raw = self._config.base_backoff_seconds * (2 ** (attempt_number - 1))
        capped = min(raw, self._config.max_backoff_seconds)
        # Add ±20% jitter to avoid thundering herd
        import random
        jitter = capped * random.uniform(-0.2, 0.2)
        return max(1.0, capped + jitter)

    def _build_enriched_goal(
        self,
        original_goal: str,
        strategy: RetryStrategy,
        last_failure: str,
    ) -> str:
        """Enrich the goal prompt with strategy hints for the planner."""
        if strategy == RetryStrategy.SAME_APPROACH:
            return (
                f"{original_goal}\n\n"
                f"[Previous attempt failed: {last_failure}. Try again.]"
            )
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

    async def run(
        self,
        *,
        goal: str,
        agent_factory: Any,  # Callable[[], AgentGraph] or AgentGraph instance
        tenant_ctx: Any,
        event_callback: Any = None,
    ) -> tuple[bool, list[AttemptRecord]]:
        """Run goal with persistence until success, escalation, or exhaustion.

        Args:
            goal: The original goal text
            agent_factory: Callable that returns a fresh AgentGraph/AgentLoop per attempt
            tenant_ctx: TenantContext
            event_callback: Async callback for SSE events

        Returns:
            (success: bool, attempts: list[AttemptRecord])
        """
        config = self._config
        session_start = time.monotonic()
        last_failure = ""

        async def emit(event: dict) -> None:
            if event_callback and config.emit_retry_events:
                try:
                    await event_callback(event)
                except Exception:
                    pass

        for attempt_number in range(1, config.max_attempts + 1):
            # Check total timeout
            if config.total_timeout_seconds > 0:
                elapsed = time.monotonic() - session_start
                if elapsed >= config.total_timeout_seconds:
                    await emit({
                        "type": "persistence_timeout",
                        "elapsed_seconds": elapsed,
                        "attempts": attempt_number - 1,
                    })
                    break

            strategy = self._pick_strategy(attempt_number)

            if strategy == RetryStrategy.ESCALATE:
                await emit({
                    "type": "persistence_escalating",
                    "reason": f"Goal failed {len(self._attempts)} times — escalating to human",
                    "attempts": len(self._attempts),
                    "total_cost_usd": self.total_cost_usd,
                })
                break

            # Wait with exponential backoff (skip on first attempt)
            if attempt_number > 1:
                backoff = self._backoff_seconds(attempt_number - 1)
                await emit({
                    "type": "persistence_waiting",
                    "attempt": attempt_number,
                    "backoff_seconds": round(backoff, 1),
                    "strategy": strategy,
                    "max_attempts": config.max_attempts,
                })
                await asyncio.sleep(backoff)

            attempt = AttemptRecord(
                attempt_number=attempt_number,
                strategy=strategy,
            )
            self._attempts.append(attempt)

            enriched_goal = self._build_enriched_goal(goal, strategy, last_failure)

            await emit({
                "type": "persistence_attempt_start",
                "attempt": attempt_number,
                "max_attempts": config.max_attempts,
                "strategy": strategy,
                "goal_modified": enriched_goal != goal,
            })

            try:
                # Get a fresh agent for this attempt
                if callable(agent_factory) and not hasattr(agent_factory, 'run'):
                    agent = agent_factory()
                else:
                    agent = agent_factory  # Already an agent instance

                attempt_start = time.monotonic()
                state = await agent.run(
                    goal=enriched_goal,
                    tenant_ctx=tenant_ctx,
                    event_callback=event_callback,
                )
                attempt.ended_at = datetime.now(UTC).isoformat()
                attempt.iterations_used = getattr(state, "iterations", 0)
                attempt.cost_usd = getattr(state, "context", {}).get("total_cost_usd", 0.0)
                attempt.success = getattr(state, "verification_success", False) or (
                    getattr(state, "status", None) and
                    str(getattr(state, "status", "")).endswith("COMPLETE")
                )

                if attempt.success:
                    await emit({
                        "type": "persistence_goal_achieved",
                        "attempt": attempt_number,
                        "total_attempts": len(self._attempts),
                        "total_cost_usd": self.total_cost_usd,
                        "strategy_that_worked": strategy,
                    })
                    logger.info(
                        "persistent_goal_achieved",
                        goal=goal[:100], attempt=attempt_number, strategy=strategy
                    )
                    return True, self._attempts

                # Record failure
                last_failure = getattr(state, "error_message", "") or \
                               getattr(state, "verification_feedback", "") or \
                               "verification failed"
                attempt.failure_reason = last_failure

                await emit({
                    "type": "persistence_attempt_failed",
                    "attempt": attempt_number,
                    "reason": last_failure[:200],
                    "iterations_used": attempt.iterations_used,
                    "remaining_attempts": config.max_attempts - attempt_number,
                })
                logger.warning(
                    "persistent_goal_attempt_failed",
                    goal=goal[:100], attempt=attempt_number,
                    reason=last_failure[:100], strategy=strategy
                )

            except asyncio.CancelledError:
                attempt.failure_reason = "cancelled"
                attempt.ended_at = datetime.now(UTC).isoformat()
                await emit({"type": "persistence_cancelled", "attempt": attempt_number})
                raise
            except Exception as exc:
                attempt.failure_reason = str(exc)[:200]
                attempt.ended_at = datetime.now(UTC).isoformat()
                last_failure = str(exc)
                await emit({
                    "type": "persistence_attempt_error",
                    "attempt": attempt_number,
                    "error": str(exc)[:200],
                })
                logger.warning("persistent_goal_error", error=str(exc), attempt=attempt_number)

        # All attempts exhausted
        await emit({
            "type": "persistence_exhausted",
            "total_attempts": len(self._attempts),
            "total_cost_usd": self.total_cost_usd,
            "last_failure": last_failure[:200],
        })
        return False, self._attempts
