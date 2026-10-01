"""Run a goal's coordination strategy on the pattern runtime (GOAL-STRATEGIES).

A goal whose runtime profile selects magentic, mixture_of_agents, camel,
generative_agents, decentralized_swarm or market_auction reaches
``DistributedStrategyExecutor``, which hands it here:

1. a coordination session is created for the goal behind the scenes — tenant
   scoped, ``goal_id`` recorded on the session, deterministic from the goal so a
   retried goal reuses it — and started;
2. the goal's HITL gate: high-risk goal text needs a persisted human approval
   before any agent runs (fail closed without a gateway or on rejection/timeout);
3. the pattern runs through ``PatternRunService`` with the goal text as objective,
   every LLM call charged to the goal's budget (a denial stops the run), bounded
   by the goal's effective limits; each pattern step reaches the goal's event
   stream as a ``coordination_progress`` event;
4. a Magentic run that exhausts its replans asks for human review through the
   same HITL gateway, and approval continues it;
5. the session is completed (or failed) and the goal gets the pattern's answer,
   or a failure carrying the pattern's terminal reason.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from app.agent.nodes._helpers import _is_high_risk_step
from app.coordination.contracts import AuthorizationContext
from app.coordination.pattern_runs.service import PATTERNS, PatternRunError, PatternRunService
from app.coordination.service import SessionAdmission
from app.coordination.state_machines import InvalidTransitionError
from app.orchestration.strategy_runner import ExecutionMetrics, StrategyRunOutput

logger = structlog.get_logger(__name__)

_DEFAULT_HITL_TIMEOUT_S = 3_600.0


class PatternGoalFailedError(RuntimeError):
    """The pattern ended without an answer; ``reason_code`` reaches the goal."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


async def _noop(_event: dict[str, Any]) -> None:
    return None


class CoordinationGoalBridge:
    def __init__(
        self,
        state: Callable[[], Any],
        *,
        hitl_timeout_seconds: float = _DEFAULT_HITL_TIMEOUT_S,
    ) -> None:
        self._state = state
        self._hitl_timeout = hitl_timeout_seconds

    async def run(self, request: Any, context: Any, cancelled: Any = None) -> StrategyRunOutput:
        del cancelled  # the StrategyRunner cancels this coroutine directly
        state = self._state()
        pattern = str(request.strategy_id)
        if pattern not in PATTERNS:
            raise PatternGoalFailedError(
                "unknown_pattern", f"not a coordination pattern: {pattern}"
            )
        tenant_ctx = getattr(context, "tenant_ctx", None)
        provider = getattr(context, "provider", None)
        if tenant_ctx is None:
            raise PatternGoalFailedError("tenant_context_missing", "goal has no tenant context")
        goal_id = str(request.goal_id)
        emit = getattr(context, "event_callback", None) or _noop
        limits = getattr(context, "limits", None)

        session_id = await self._open_session(state, tenant_ctx, request)
        await emit(
            {
                "type": "coordination_session",
                "session_id": session_id,
                "strategy_id": pattern,
                "execution_tier": "distributed",
            }
        )
        if _is_high_risk_step(str(context.goal_text)):
            await self._require_approval(
                state,
                tenant_ctx,
                goal_id,
                emit,
                action=f"{pattern} coordination run: {context.goal_text}"[:500],
            )

        async def observe(frame: dict[str, Any]) -> None:
            await emit(_progress_event(pattern, session_id, frame))

        runs = PatternRunService(state)
        try:
            result = await runs.run(
                tenant_ctx,
                session_id,
                pattern,
                objective=str(context.goal_text)[:4_000],
                participants=(),
                max_rounds=_bounded(getattr(limits, "rounds", None), default=6, high=12),
                options=_options(limits),
                idempotency_key=f"goal:{goal_id}",
                goal_id=goal_id,
                max_calls=getattr(limits, "calls", None),
                observer=observe,
                provider=provider,
            )
            if result.get("phase") == "awaiting_human":
                result = await self._magentic_review(
                    state, runs, tenant_ctx, session_id, goal_id, emit, result, observe, provider
                )
        except PatternRunError as exc:
            await self._finish_session(state, tenant_ctx, session_id, succeeded=False)
            raise PatternGoalFailedError("pattern_unavailable", str(exc)) from exc

        succeeded = result.get("phase") == "completed" and bool(result.get("safe_output"))
        await self._finish_session(state, tenant_ctx, session_id, succeeded=succeeded)
        if not succeeded:
            reason = str(result.get("terminal_reason") or result.get("phase") or "failed")
            raise PatternGoalFailedError(
                f"pattern_{reason}", f"{pattern} ended {result.get('phase')}: {reason}"
            )
        view = dict(result.get("view") or {})
        return StrategyRunOutput(
            answer=str(result["safe_output"]),
            metrics=ExecutionMetrics(
                calls=int(result.get("llm_calls") or 0),
                tokens=int(result.get("llm_tokens") or 0),
                rounds=int(view.get("round_number") or view.get("turn_count") or 0),
                cost_usd=float(result.get("cost_usd") or 0.0),
            ),
            safe_rationale_summary=(f"{pattern} coordination pattern ran on session {session_id}."),
        )

    async def _open_session(self, state: Any, tenant_ctx: Any, request: Any) -> str:
        service = getattr(state, "coordination_service", None)
        if service is None:
            raise PatternGoalFailedError("coordination_unavailable", "coordination unavailable")
        goal_id = str(request.goal_id)
        session = await service.create_session(
            tenant_ctx,
            SessionAdmission(
                civilization_id=f"goal:{goal_id}",
                goal_id=goal_id,
                policy_snapshot={"policy_ref": request.policy_ref},
                budget_snapshot={"budget_ref": request.budget_ref},
                authorization=AuthorizationContext(
                    actor_id=f"goal:{goal_id}",
                    permissions=frozenset({"coordination:create"}),
                ),
            ),
            idempotency_key=f"goal:{goal_id}",
        )
        if session.state == "pending":
            await service.start_session(
                tenant_ctx,
                session.session_id,
                expected_version=session.version,
                idempotency_key=f"goal:{goal_id}:start",
            )
        elif session.state not in {"active", "paused"}:
            raise PatternGoalFailedError(
                "coordination_session_closed", f"goal session is {session.state}"
            )
        return str(session.session_id)

    async def _finish_session(
        self, state: Any, tenant_ctx: Any, session_id: str, *, succeeded: bool
    ) -> None:
        service = getattr(state, "coordination_service", None)
        if service is None:
            return
        try:
            session = await service.get_session(tenant_ctx, session_id)
            if session.state == "paused":
                session = await service.resume_session(
                    tenant_ctx,
                    session_id,
                    expected_version=session.version,
                    idempotency_key=f"{session_id}:goal-resume",
                )
                session = await service.get_session(tenant_ctx, session_id)
            if session.state != "active":
                return
            command = service.complete_session if succeeded else service.fail_session
            await command(
                tenant_ctx,
                session_id,
                expected_version=session.version,
                idempotency_key=f"{session_id}:goal-finish",
            )
        except (InvalidTransitionError, KeyError) as exc:
            logger.warning("goal_coordination_session_finish_skipped", error=str(exc)[:200])

    async def _require_approval(
        self, state: Any, tenant_ctx: Any, goal_id: str, emit: Any, *, action: str
    ) -> None:
        from app.governance.hitl import ApprovalStatus

        gateway = getattr(state, "hitl_gateway", None)
        if gateway is None:
            raise PatternGoalFailedError(
                "approval_unavailable", "high-risk coordination goal needs an approval gateway"
            )
        try:
            request_id = await gateway.request_approval_async(
                goal_id=goal_id,
                action=action,
                step_description=action,
                risk_level="high",
                tenant_ctx=tenant_ctx,
            )
        except Exception as exc:
            raise PatternGoalFailedError("approval_unavailable", str(exc)) from exc
        await emit({"type": "waiting_approval", "request_id": request_id, "action": action})
        status = await gateway.wait_for_approval(
            request_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
        )
        if status != ApprovalStatus.APPROVED:
            raise PatternGoalFailedError(
                f"approval_{str(status).lower()}", f"coordination run approval {status}"
            )
        await emit({"type": "approval_granted", "request_id": request_id})

    async def _magentic_review(
        self,
        state: Any,
        runs: PatternRunService,
        tenant_ctx: Any,
        session_id: str,
        goal_id: str,
        emit: Any,
        result: dict[str, Any],
        observe: Any,
        provider: Any,
    ) -> dict[str, Any]:
        """Route Magentic's human review through the goal's HITL gateway."""
        review = dict(result.get("human_review") or {})
        try:
            await self._require_approval(
                state,
                tenant_ctx,
                goal_id,
                emit,
                action=(
                    "Magentic team exhausted its replans; approve one more replan "
                    f"(reason: {review.get('reason')})"
                ),
            )
            approved = True
        except PatternGoalFailedError:
            approved = False
        token = review.get("token")
        reviewer = getattr(state, "magentic_human_review", None)
        if token and reviewer is not None:
            await reviewer.submit(
                str(tenant_ctx.tenant_id),
                session_id,
                token=str(token),
                approved=approved,
                safe_note="decided through the goal's approval gate",
            )
        continued = await runs.apply_magentic_review(
            tenant_ctx,
            session_id,
            approved=approved,
            observer=observe,
            provider=provider,
        )
        return continued if continued is not None else result


def _progress_event(pattern: str, session_id: str, frame: dict[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "coordination_progress",
        "strategy_id": pattern,
        "session_id": session_id,
        "frame_type": frame.get("type"),
    }
    if frame.get("type") == "event":
        event["event_type"] = frame.get("event_type")
        event["payload"] = frame.get("payload")
    elif frame.get("type") == "message":
        message = dict(frame.get("message") or {})
        event["sender"] = message.get("sender_agent_id")
        event["message"] = str(message.get("safe_content") or "")[:2_000]
        event["sequence"] = message.get("sequence")
    return event


def _bounded(value: Any, *, default: int, high: int) -> int:
    try:
        return max(1, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _options(limits: Any) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if limits is not None:
        options["timeout_seconds"] = _bounded(
            getattr(limits, "duration_seconds", None), default=300, high=3_600
        )
        cost = getattr(limits, "cost_usd", None)
        if cost:
            options["max_cost_usd"] = float(cost)
        tokens = getattr(limits, "tokens", None)
        if tokens:
            options["max_tokens"] = int(tokens)
    return options


def build_pattern_state(
    *,
    db_factory: Any,
    provider: Any,
    redis: Any = None,
    hitl_gateway: Any = None,
) -> Any:
    """Postgres-backed pattern runtime state for a process without ``app.state``
    (the Celery worker running a queued goal)."""
    from types import SimpleNamespace

    from app.coordination.auction.repository import (
        PostgresAuctionRepository,
        PostgresSealedBidInbox,
    )
    from app.coordination.camel.repository import PostgresCamelRepository
    from app.coordination.generative.repository import PostgresGenerativeRepository
    from app.coordination.group_chat.repository import PostgresGroupChatRepository
    from app.coordination.ledger.repository import PostgresProgressLedgerRepository
    from app.coordination.live_bus import CoordinationLiveBus
    from app.coordination.magentic.human_review import MagenticHumanReviewService
    from app.coordination.magentic.repository import PostgresMagenticRunRepository
    from app.coordination.moa.repository import PostgresMoARepository, PostgresMoARunRepository
    from app.coordination.pattern_runs.moa import configured_provider_pool
    from app.coordination.service import CoordinationService
    from app.coordination.store import CoordinationStore
    from app.coordination.swarm.repository import PostgresSwarmRepository
    from app.coordination.transcript.repository import PostgresTranscriptRepository
    from app.coordination.transcript.service import TranscriptService

    review = MagenticHumanReviewService()
    review.set_db(db_factory)
    try:
        moa_providers = configured_provider_pool()
    except Exception:
        moa_providers = []
    return SimpleNamespace(
        llm_provider=provider,
        hitl_gateway=hitl_gateway,
        coordination_service=CoordinationService(CoordinationStore(db_factory)),
        transcript_service=TranscriptService(PostgresTranscriptRepository(db_factory)),
        coordination_live_bus=CoordinationLiveBus(lambda: redis),
        progress_ledger_repository=PostgresProgressLedgerRepository(db_factory),
        magentic_run_repository=PostgresMagenticRunRepository(db_factory),
        magentic_human_review=review,
        moa_repository=PostgresMoARepository(db_factory),
        moa_run_repository=PostgresMoARunRepository(db_factory),
        moa_providers=moa_providers,
        camel_repository=PostgresCamelRepository(db_factory),
        group_chat_repository=PostgresGroupChatRepository(db_factory),
        generative_repository=PostgresGenerativeRepository(db_factory),
        swarm_repository=PostgresSwarmRepository(db_factory),
        auction_repository=PostgresAuctionRepository(db_factory),
        auction_bid_inbox=PostgresSealedBidInbox(db_factory),
    )


def build_worker_distributed_loop(
    runtime_profile: Any,
    *,
    db_factory: Any,
    provider: Any,
    cost_controller: Any = None,
    hitl_gateway: Any = None,
    redis: Any = None,
    agent_id: str | None = None,
) -> Any | None:
    """A DistributedStrategyLoop for a worker-run goal whose primary strategy runs on
    the StrategyRunner (coordination patterns, supervisor, goal_tree, debate,
    voyager) — with the same budget reservation, per-call charging, HITL approval
    gate and (voyager) persistent skill library as the API process; ``None``
    (local fallback, recorded as a downgrade) when the worker cannot run it
    honestly (no database or no real LLM provider)."""
    from types import SimpleNamespace

    from app.memory.voyager_skills_pg import PostgresVoyagerSkillStore
    from app.orchestration.distributed_strategy_loop import DistributedStrategyLoop
    from app.orchestration.execution_drivers import (
        COORDINATION_PATTERN_STRATEGIES,
        STRATEGY_RUNNER_STRATEGIES,
    )
    from app.orchestration.strategy_context_store import StrategyGoalContextStore
    from app.orchestration.strategy_executor import (
        DistributedStrategyExecutor,
        default_distributed_admission,
    )
    from app.orchestration.strategy_registry import build_default_registry
    from app.orchestration.strategy_runner import StrategyRunner

    if runtime_profile is None or db_factory is None or provider is None:
        return None
    strategy_id = runtime_profile.primary_strategy.strategy_id
    if strategy_id not in STRATEGY_RUNNER_STRATEGIES:
        return None
    bridge = None
    if strategy_id in COORDINATION_PATTERN_STRATEGIES:
        pattern_state = build_pattern_state(
            db_factory=db_factory, provider=provider, redis=redis, hitl_gateway=hitl_gateway
        )
        bridge = CoordinationGoalBridge(lambda: pattern_state)
    context_store = StrategyGoalContextStore()

    async def _reserve(request: Any, _limits: Any) -> bool:
        # Same rule as the API process: an unverifiable budget never admits a run.
        check = getattr(cost_controller, "ahas_remaining_budget", None)
        if check is None:
            return False
        try:
            return bool(await check(tenant_ctx=SimpleNamespace(tenant_id=request.tenant_id)))
        except Exception:
            return False

    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(
            context_store=context_store,
            cost_controller=cost_controller,
            pattern_bridge=bridge,
            skill_store=PostgresVoyagerSkillStore(db_factory),
            hitl_gateway=hitl_gateway,
        ),
        admission=default_distributed_admission,
        reserve_budget=_reserve,
    )
    return DistributedStrategyLoop(
        strategy_runner=runner,
        context_store=context_store,
        profile=runtime_profile,
        provider=provider,
        agent_id=agent_id,
    )


__all__ = [
    "CoordinationGoalBridge",
    "PatternGoalFailedError",
    "build_pattern_state",
    "build_worker_distributed_loop",
]
