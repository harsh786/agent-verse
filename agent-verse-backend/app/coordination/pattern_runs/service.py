"""Run coordination patterns on a session and persist their read models.

``PatternRunService.run`` admits a run (real LLM provider, tenant-visible active
session, bounded limits), derives a deterministic execution id from the
Idempotency-Key, and drives the pattern. Each driver writes its read model through
the existing repositories (Magentic ledger, MoA layers/proposals, pattern state for
CAMEL / generative / swarm / auction) — all tenant-scoped under RLS — records its
steps in the canonical transcript and publishes them on the live bus.

A retried command resumes from the persisted checkpoint (or returns the stored
outcome once the run is terminal), on any replica.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.coordination.pattern_runs.auction import run_auction
from app.coordination.pattern_runs.camel import run_camel
from app.coordination.pattern_runs.context import PublishingTranscript, RunContext, RunOutcome
from app.coordination.pattern_runs.generative import run_generative
from app.coordination.pattern_runs.llm import PatternCallLimitError, PatternLLM
from app.coordination.pattern_runs.magentic import run_magentic
from app.coordination.pattern_runs.moa import run_moa
from app.coordination.pattern_runs.records import RunDocument
from app.coordination.pattern_runs.swarm import run_swarm
from app.providers.guarded_completion import DecisionBudgetExceededError

logger = structlog.get_logger(__name__)

Observer = Callable[[dict[str, Any]], Awaitable[None]]

_NAMESPACE = uuid.UUID("0b9f7c52-9d1e-4c55-8a6f-3e2d1c0b9a87")
TERMINAL_PHASES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True)
class PatternSpec:
    repository_attr: str
    default_participants: tuple[str, ...]
    min_participants: int
    max_calls: int


PATTERNS: dict[str, PatternSpec] = {
    "magentic": PatternSpec("magentic_run_repository", ("researcher", "analyst", "writer"), 1, 40),
    "mixture_of_agents": PatternSpec(
        "moa_run_repository", ("proposer-1", "proposer-2", "proposer-3"), 2, 24
    ),
    "camel": PatternSpec("camel_repository", ("ai_user", "ai_assistant"), 2, 30),
    "generative_agents": PatternSpec("generative_repository", ("resident",), 1, 30),
    "decentralized_swarm": PatternSpec(
        "swarm_repository", ("swarm-agent-1", "swarm-agent-2", "swarm-agent-3"), 2, 30
    ),
    "market_auction": PatternSpec(
        "auction_repository", ("bidder-1", "bidder-2", "bidder-3"), 2, 12
    ),
}


class PatternRunError(Exception):
    status_code = 400


class UnknownPatternError(PatternRunError):
    status_code = 404


class PatternRuntimeUnavailableError(PatternRunError):
    status_code = 503


class PatternSessionError(PatternRunError):
    status_code = 409


class PatternRequestError(PatternRunError):
    status_code = 422


def execution_id_for(tenant_id: str, session_id: str, pattern: str, key: str) -> str:
    return uuid.uuid5(_NAMESPACE, f"{tenant_id}:{session_id}:{pattern}:{key}").hex


class PatternRunService:
    def __init__(self, state: Any) -> None:
        # app.state: read per call so the lifespan's DB/Redis swaps apply.
        self._state = state

    def repository(self, pattern: str) -> Any:
        spec = PATTERNS.get(pattern)
        if spec is None:
            raise UnknownPatternError(f"unknown coordination pattern: {pattern}")
        repository = getattr(self._state, spec.repository_attr, None)
        if repository is None:
            raise PatternRuntimeUnavailableError(f"{pattern} read model unavailable")
        return repository

    async def list_runs(
        self, tenant_id: str, session_id: str, pattern: str
    ) -> list[dict[str, Any]]:
        records = await self.repository(pattern).list_session(tenant_id, session_id)
        return [public_record(pattern, record) for record in records]

    async def run(
        self,
        tenant_ctx: Any,
        session_id: str,
        pattern: str,
        *,
        objective: str,
        participants: tuple[str, ...],
        max_rounds: int,
        options: dict[str, Any],
        idempotency_key: str,
        goal_id: str | None = None,
        max_calls: int | None = None,
        observer: Observer | None = None,
        provider: Any = None,
    ) -> dict[str, Any]:
        """Run *pattern*; ``goal_id``/``max_calls`` bind it to a goal's budget and
        limits, ``observer`` receives every published frame (goal progress)."""
        spec = PATTERNS.get(pattern)
        if spec is None:
            raise UnknownPatternError(f"unknown coordination pattern: {pattern}")
        repository = self.repository(pattern)
        tenant_id = str(tenant_ctx.tenant_id)
        self._provider(provider)
        await self._require_active_session(tenant_ctx, session_id)
        members = tuple(dict.fromkeys(p.strip()[:100] for p in participants if p.strip()))
        members = members or spec.default_participants
        if len(members) < spec.min_participants:
            raise PatternRequestError(
                f"{pattern} needs at least {spec.min_participants} participants"
            )
        config = {
            "pattern": pattern,
            "objective": objective,
            "participants": list(members),
            "max_rounds": max_rounds,
            "options": options,
            "goal_id": goal_id,
            "max_calls": min(spec.max_calls, max_calls) if max_calls else spec.max_calls,
        }
        execution_id = execution_id_for(tenant_id, session_id, pattern, idempotency_key)
        document = await RunDocument(
            repository, tenant_id=tenant_id, session_id=session_id, execution_id=execution_id
        ).load()
        if document.exists:
            common = config.keys() & document.config.keys()
            if any(document.config[key] != config[key] for key in common):
                raise PatternSessionError("Idempotency-Key reused with a different run request")
            if document.view.get("phase") in TERMINAL_PHASES | {"awaiting_human"}:
                return await self._result(tenant_ctx, pattern, document, replay=True)
        else:
            if pattern == "magentic":
                await self._require_no_ledger(tenant_id, session_id)
            await document.create(config)
        return await self._execute(
            tenant_ctx, pattern, document, observer=observer, provider=provider
        )

    async def apply_magentic_review(
        self,
        tenant_ctx: Any,
        session_id: str,
        *,
        approved: bool,
        observer: Observer | None = None,
        provider: Any = None,
    ) -> dict[str, Any] | None:
        """Continue (approved) or close (rejected) the run awaiting human review."""
        tenant_id = str(tenant_ctx.tenant_id)
        repository = self.repository("magentic")
        for record in await repository.list_session(tenant_id, session_id):
            document = await RunDocument(
                repository,
                tenant_id=tenant_id,
                session_id=session_id,
                execution_id=record.execution_id,
            ).load()
            checkpoint = document.checkpoint or {}
            if checkpoint.get("phase") != "awaiting_human":
                continue
            if not approved:
                checkpoint |= {"phase": "failed", "terminal_reason": "human_rejected"}
                await document.update(
                    checkpoint=checkpoint,
                    view={**document.view, "phase": "failed", "terminal_reason": "human_rejected"},
                )
                return await self._result(tenant_ctx, "magentic", document, replay=True)
            config = document.config
            config["approved_resets"] = int(config.get("approved_resets", 0)) + 1
            checkpoint |= {"phase": "replanning", "terminal_reason": None}
            await document.update(
                config=config,
                checkpoint=checkpoint,
                view={**document.view, "phase": "replanning", "terminal_reason": None},
            )
            return await self._execute(
                tenant_ctx, "magentic", document, observer=observer, provider=provider
            )
        return None

    async def _execute(
        self,
        tenant_ctx: Any,
        pattern: str,
        document: RunDocument,
        *,
        observer: Observer | None = None,
        provider: Any = None,
    ) -> dict[str, Any]:
        provider = self._provider(provider)
        spec = PATTERNS[pattern]
        config = document.config
        tenant_id = str(tenant_ctx.tenant_id)
        llm = PatternLLM(
            provider,
            tenant_ctx=tenant_ctx,
            pattern=pattern,
            max_calls=int(config.get("max_calls") or spec.max_calls),
            goal_id=config.get("goal_id"),
        )
        publish = self._publisher(tenant_id, document.session_id, observer)
        transcript = getattr(self._state, "transcript_service", None)
        if transcript is None:
            raise PatternRuntimeUnavailableError("transcript service unavailable")
        options = dict(config.get("options") or {})
        ctx = RunContext(
            tenant_id=tenant_id,
            session_id=document.session_id,
            execution_id=document.execution_id,
            objective=str(config["objective"]),
            participants=tuple(config["participants"]),
            llm=llm,
            document=document,
            transcript=PublishingTranscript(transcript, publish),
            publish=publish,
            deadline=datetime.now(UTC)
            + timedelta(seconds=int(options.get("timeout_seconds", 300))),
            max_rounds=int(config["max_rounds"]),
            options=options,
        )
        await publish(_event(pattern, document, "started"))
        try:
            outcome = await self._drive(pattern, ctx, provider)
        except PatternCallLimitError:
            outcome = RunOutcome(phase="failed", terminal_reason="llm_call_limit")
        except DecisionBudgetExceededError:
            outcome = RunOutcome(phase="failed", terminal_reason="budget_exceeded")
        await document.update(
            view={
                **document.view,
                **outcome.view,
                "phase": outcome.phase,
                "terminal_reason": outcome.terminal_reason,
                "safe_output": outcome.safe_output,
                "llm_calls": int(document.view.get("llm_calls", 0)) + llm.calls,
                "llm_tokens": int(document.view.get("llm_tokens", 0)) + llm.tokens,
                "cost_usd": round(float(document.view.get("cost_usd", 0.0)) + llm.cost_usd, 6),
            }
        )
        await publish(_event(pattern, document, outcome.phase))
        return await self._result(tenant_ctx, pattern, document, replay=False)

    async def _drive(self, pattern: str, ctx: RunContext, provider: Any) -> RunOutcome:
        drivers: dict[str, Callable[[], Awaitable[RunOutcome]]] = {
            "magentic": lambda: run_magentic(
                ctx, ledger_repository=self._required("progress_ledger_repository")
            ),
            "mixture_of_agents": lambda: run_moa(
                ctx,
                repository=self._required("moa_repository"),
                provider=provider,
                configured_providers=list(getattr(self._state, "moa_providers", None) or ()),
            ),
            "camel": lambda: run_camel(ctx),
            "generative_agents": lambda: run_generative(ctx),
            "decentralized_swarm": lambda: run_swarm(ctx),
            "market_auction": lambda: run_auction(
                ctx, bid_inbox=self._required("auction_bid_inbox")
            ),
        }
        return await drivers[pattern]()

    def _provider(self, override: Any = None) -> Any:
        # app.state.llm_provider is None when only the no-key FakeProvider is wired:
        # canned output must never be presented as a pattern run. A goal passes its
        # own (tenant-resolved) provider, under the same rule.
        from app.providers.fake import FakeProvider

        provider = override if override is not None else getattr(self._state, "llm_provider", None)
        inner = getattr(provider, "inner", provider)
        if (
            provider is None
            or isinstance(provider, FakeProvider)
            or isinstance(inner, FakeProvider)
        ):
            raise PatternRuntimeUnavailableError(
                "no real LLM provider is configured; coordination patterns cannot run"
            )
        return provider

    def _required(self, attr: str) -> Any:
        value = getattr(self._state, attr, None)
        if value is None:
            raise PatternRuntimeUnavailableError(f"{attr} unavailable")
        return value

    async def _require_active_session(self, tenant_ctx: Any, session_id: str) -> None:
        service = self._required("coordination_service")
        try:
            session = await service.get_session(tenant_ctx, session_id)
        except KeyError as exc:
            raise KeyError(session_id) from exc
        if session.state != "active":
            raise PatternSessionError(
                f"coordination session is {session.state}; patterns run on active sessions"
            )

    async def _require_no_ledger(self, tenant_id: str, session_id: str) -> None:
        ledger = self._required("progress_ledger_repository")
        if await ledger.current(tenant_id, session_id) is not None:
            raise PatternSessionError("this session already has a Magentic ledger")

    def _publisher(
        self, tenant_id: str, session_id: str, observer: Observer | None = None
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        async def publish(frame: dict[str, Any]) -> None:
            if observer is not None:
                try:
                    await observer(frame)
                except Exception as exc:  # progress reporting never breaks the run
                    logger.warning("pattern_run_observer_failed", error=str(exc)[:200])
            bus = getattr(self._state, "coordination_live_bus", None)
            if bus is None:
                return
            try:
                await bus.publish(tenant_id, session_id, frame)
            except Exception as exc:  # live fan-out is best effort over durable state
                logger.warning("pattern_run_live_publish_failed", error=str(exc))

        return publish

    async def _result(
        self, tenant_ctx: Any, pattern: str, document: RunDocument, *, replay: bool
    ) -> dict[str, Any]:
        result = _public_document(pattern, document)
        result["replayed"] = replay
        checkpoint = document.checkpoint or {}
        if pattern == "magentic" and checkpoint.get("phase") == "awaiting_human":
            review = getattr(self._state, "magentic_human_review", None)
            if review is None:
                raise PatternRuntimeUnavailableError("Magentic human review unavailable")
            # One-time token, returned only to the operator who ran (or re-ran)
            # the command; only its hash is stored. Re-issuing rotates it.
            token = secrets.token_urlsafe(24)
            await review.issue(str(tenant_ctx.tenant_id), document.session_id, token)
            result["phase"] = "awaiting_human"
            result["human_review"] = {
                "token": token,
                "reason": checkpoint.get("terminal_reason"),
                "submit_path": (
                    f"/api/v1/coordination/sessions/{document.session_id}/magentic/human-review"
                ),
            }
        return result


def _event(pattern: str, document: RunDocument, phase: str) -> dict[str, Any]:
    return {
        "type": "event",
        "event_type": f"pattern_run.{phase}.v1",
        "payload": {
            "pattern": pattern,
            "session_id": document.session_id,
            "execution_id": document.execution_id,
            "phase": phase,
        },
    }


def _public_document(pattern: str, document: RunDocument) -> dict[str, Any]:
    view = document.view
    checkpoint = document.checkpoint or {}
    return {
        "pattern": pattern,
        "session_id": document.session_id,
        "execution_id": document.execution_id,
        "phase": view.get("phase") or checkpoint.get("phase") or "admitted",
        "terminal_reason": view.get("terminal_reason") or checkpoint.get("terminal_reason"),
        "safe_output": view.get("safe_output"),
        "objective": document.config.get("objective"),
        "participants": document.config.get("participants", []),
        "llm_calls": view.get("llm_calls", 0),
        "llm_tokens": view.get("llm_tokens", 0),
        "cost_usd": view.get("cost_usd", 0.0),
        "view": view,
        "checkpoint": checkpoint,
    }


def public_record(pattern: str, record: Any) -> dict[str, Any]:
    state = dict(record.state)
    view = dict(state.get("view") or {})
    checkpoint = dict(state.get("checkpoint") or {})
    config = dict(state.get("config") or {})
    return {
        "pattern": pattern,
        "session_id": record.session_id,
        "execution_id": record.execution_id,
        "version": record.version,
        "phase": view.get("phase") or checkpoint.get("phase") or "admitted",
        "terminal_reason": view.get("terminal_reason") or checkpoint.get("terminal_reason"),
        "safe_output": view.get("safe_output"),
        "objective": config.get("objective"),
        "participants": config.get("participants", []),
        "view": view,
        "checkpoint": checkpoint,
    }


def swarm_topology(records: tuple[Any, ...]) -> dict[str, list[dict[str, Any]]]:
    """Session-level swarm topology merged across runs (nodes by agent, edges summed)."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        view = dict(dict(record.state).get("view") or {})
        for node in view.get("nodes") or ():
            agent = str(node.get("agent_id"))
            merged = nodes.setdefault(agent, {**node, "completed_items": 0})
            merged.update({k: v for k, v in node.items() if k != "completed_items"})
            merged["completed_items"] = int(merged["completed_items"]) + int(
                node.get("completed_items", 0)
            )
        for edge in view.get("edges") or ():
            key = (str(edge.get("source")), str(edge.get("target")), str(edge.get("message_type")))
            merged_edge = edges.setdefault(key, {**edge, "count": 0})
            merged_edge["count"] = int(merged_edge["count"]) + int(edge.get("count", 1))
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


__all__ = [
    "PATTERNS",
    "TERMINAL_PHASES",
    "PatternRunError",
    "PatternRunService",
    "PatternRuntimeUnavailableError",
    "PatternSessionError",
    "UnknownPatternError",
    "execution_id_for",
    "public_record",
    "swarm_topology",
]
