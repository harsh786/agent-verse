"""Task 9 — Ambient collaboration tick ("the team talks").

Emits a small, strictly capped number of short "status / next-step" chatter
lines from department leads as org events, so the product surface can show a
lightweight sense of ongoing team activity without materially adding to LLM
spend. This is cosmetic ambient narration, not a decision-making pathway —
cost discipline is the whole point, so every guard below is checked before
any model call is attempted, and a single model error stops the entire tick.

Guards (checked first, cheapest first — return 0 immediately without calling
the model or publishing anything):
  * ``not settings.collaboration_enabled``
  * ``autonomy_level < 3`` (collaboration chatter is an L3+ perk)
  * no ``leads`` to speak for
  * today's tracked collaboration spend (``counters.snapshot_collab_spend()``)
    is already at/over ``settings.collaboration_daily_budget_usd`` — its own
    operator-set cap, tracked separately (``counters.record_collab_spend``)
    from the org's general mission spend so it actually bounds something
    instead of riding on ``daily_budget_usd`` (see Finding 2: that used to be
    a dead setting — the tick instead gated on the org's general daily budget
    and never tracked collaboration spend at all)

Emitted messages are capped at ``settings.collab_messages_per_tick`` no
matter how many leads are supplied. Each message asks the model for a single
short sentence via a small, fixed token budget (see ``_MAX_TOKENS_PER_MESSAGE``
below) — this is ambient flavor text, not a deliverable.

Fail-closed: any exception raised by ``model_gateway.complete_short`` stops
the tick immediately. Whatever was already published stays published; the
method returns the count emitted so far and does not raise.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

import structlog
from opentelemetry import trace

from app.org.brain_settings import AutonomySettings

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

# Ambient chatter only needs a single short sentence — keep the per-message
# token budget small and fixed so cost per tick is bounded by
# ``collab_messages_per_tick`` alone.
_MAX_TOKENS_PER_MESSAGE = 40

# Fixed per-message cost estimate charged against ``collaboration_daily_budget_usd``
# (via ``counters.record_collab_spend``). Matches the ``cost_budget_usd`` already
# asked of ``ModelGateway.select_model`` for this same chatter in
# ``LLMProviderCollaborationGateway.complete_short`` below — a "fast" profile
# pick at a fixed, tiny token budget, so a fixed per-message estimate (rather
# than metering real token usage) is an accurate-enough accounting unit here.
_EST_COST_USD_PER_MESSAGE = 0.001

EVENT_TYPE_COLLABORATION_MESSAGE = "org.collaboration.message"


def classify_message_kind(text: str) -> str:
    """Lightweight keyword classification of an ambient chatter message.

    Pure and deliberately simple — this is cosmetic typing for the Situation
    Room UX, not a decision-making pathway, so a small ordered set of
    substring checks is enough. Order matters where keywords could overlap
    (checked most-specific-first); falls back to ``"update"``.
    """
    if not text:
        return "update"
    lowered = text.lower()
    if "?" in text:
        return "question"
    if any(kw in lowered for kw in ("propose", "suggest", "recommend")):
        return "proposal"
    if any(kw in lowered for kw in ("risk", "concern", "warn")):
        return "risk"
    if any(kw in lowered for kw in ("block", "cannot", "can't", "stuck")):
        return "block"
    if any(kw in lowered for kw in ("done", "completed", "result")):
        return "result"
    if any(kw in lowered for kw in ("hand off", "handoff", "take over")):
        return "handoff"
    return "update"


@runtime_checkable
class CollaborationModelGateway(Protocol):
    """Duck-typed interface ``CollaborationTick`` needs from ``model_gateway``.

    ``app/org/model_gateway.py::ModelGateway`` only *selects* a model
    (``select_model`` returns a ``ModelSelection``, it does not itself call
    any LLM) — the actual short completion still has to go through an
    ``LLMProvider.complete`` (``app/providers/base.py``). Production wiring
    (``app/scaling/tasks.py::org_collaboration_loop``) supplies an adapter —
    ``LLMProviderCollaborationGateway`` below — that calls the real
    ``ModelGateway.select_model`` to pick a cheap profile and then the real
    provider's ``complete``. Tests substitute a fake that just returns a
    canned short line (or raises, to exercise the fail-closed path).

    Returns ``(text, latency_ms, tokens, cost_usd)``: ``latency_ms`` is
    wall-clock around the underlying model call; ``tokens``/``cost_usd`` are
    read from the provider response's real usage when available, else a
    rough estimate (see ``LLMProviderCollaborationGateway.complete_short``).
    """

    async def complete_short(
        self, prompt: str, *, max_tokens: int
    ) -> tuple[str, int, int, float]: ...


@runtime_checkable
class CollaborationEventPublisher(Protocol):
    """Matches the REAL, production-wired singleton's contract:
    ``app.org.events.OrgEventPublisher.publish`` (returned by
    ``app.org.events.get_org_event_publisher()``), which
    ``app/scaling/tasks.py::_collaboration_tick_for_org`` actually passes in.

    Note this is a *different* shape than the dead, unused
    ``app.org.event_publisher.OrgEventPublisher.publish`` (positional
    ``event_type, payload, tenant_id, org_id``) — that module is not wired
    into production anywhere. Calling the real publisher with those
    positional args silently sends the payload where ``tenant_id`` belongs
    and vice versa, so every argument here is keyword, matching real callers
    (``app/org/router.py``, ``app/org/service.py``,
    ``app/org/approval_chain.py``).
    """

    async def publish(
        self,
        *,
        event_type: str,
        org_id: str,
        tenant_id: str,
        payload: dict[str, Any] | None = None,
    ) -> Any: ...


class LLMProviderCollaborationGateway:
    """Adapts a real ``ModelGateway`` + ``LLMProvider`` pair into the
    ``complete_short`` shape ``CollaborationTick`` needs.

    Uses ``ModelGateway.select_model`` — the real, existing entrypoint on
    ``app/org/model_gateway.py`` — to pick a cheap ("fast" profile, tight
    cost budget) model for this ambient chatter, then calls the provider's
    own ``complete`` with a small, fixed ``max_tokens``.
    """

    def __init__(self, llm_provider: Any, gateway: Any | None = None) -> None:
        self._llm = llm_provider
        self._gateway = gateway

    async def complete_short(
        self, prompt: str, *, max_tokens: int
    ) -> tuple[str, int, int, float]:
        from app.providers.base import CompletionRequest, Message

        model = ""
        if self._gateway is not None:
            try:
                selection = await self._gateway.select_model(
                    role_profile="fast",
                    task_type="org_collaboration_chatter",
                    quality_req=0.60,
                    latency_budget_ms=5_000,
                    cost_budget_usd=0.001,
                )
                model = selection.model_id
            except Exception as exc:  # pragma: no cover - defensive, gateway is cheap
                _log.warning("collaboration_gateway.select_model_failed", error=str(exc))
        if not model:
            model = getattr(self._llm, "_default_model", "") or ""

        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model=model,
            max_tokens=max_tokens,
            temperature=0.7,
        )
        start = time.perf_counter()
        resp = await self._llm.complete(req)
        latency_ms = int((time.perf_counter() - start) * 1000)
        text = (resp.content or "").strip()

        # Prefer the response's normalised ``usage`` (``TokenUsage``); fall
        # back to the top-level ``input_tokens``/``output_tokens`` fields
        # (``app/providers/base.py::CompletionResponse``) when it is absent.
        usage = getattr(resp, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        if input_tokens is None:
            input_tokens = getattr(resp, "input_tokens", 0) or 0
        if output_tokens is None:
            output_tokens = getattr(resp, "output_tokens", 0) or 0
        total_tokens = int(input_tokens) + int(output_tokens)

        if total_tokens > 0:
            tokens = total_tokens
            try:
                from app.intelligence.cost_tracker import calculate_cost

                cost_usd = calculate_cost(
                    resp.model or model, int(input_tokens), int(output_tokens)
                )
            except Exception as exc:  # pragma: no cover - defensive, pricing lookup is pure
                _log.warning("collaboration_gateway.cost_calc_failed", error=str(exc))
                cost_usd = _EST_COST_USD_PER_MESSAGE
        else:
            # Provider reported no real usage -- rough length-based token
            # estimate and the fixed per-message cost estimate as a
            # conservative fallback.
            tokens = max(1, len(text) // 4) if text else 0
            cost_usd = _EST_COST_USD_PER_MESSAGE

        return text, latency_ms, tokens, cost_usd


class CollaborationTick:
    """Emits capped, low-cost lead "status chatter" as org events."""

    def __init__(
        self,
        model_gateway: CollaborationModelGateway,
        event_publisher: CollaborationEventPublisher,
        counters: Any = None,
    ) -> None:
        self._model_gateway = model_gateway
        self._event_publisher = event_publisher
        # Used to gate on + track ``collaboration_daily_budget_usd`` — its own
        # counter (``snapshot_collab_spend``/``record_collab_spend``), separate
        # from the general mission-spend counter the rest of ``OrgBrain`` uses.
        self._counters = counters

    async def run(
        self,
        *,
        org_id: str,
        tenant_id: str,
        settings: AutonomySettings,
        autonomy_level: int,
        leads: list[str],
        day_spend_usd: float,
    ) -> int:
        if not settings.collaboration_enabled:
            return 0
        if int(autonomy_level) < 3:
            return 0
        if not leads:
            return 0

        cap = max(0, int(settings.collab_messages_per_tick))
        if cap == 0:
            return 0

        # Gate on the collaboration-specific budget (operator-set, defaults to
        # $1/day — see ``brain_settings.py``), tracked by its own counter so it
        # actually bounds something instead of being a dead setting. When no
        # counters are wired (defensive — production always supplies one; see
        # ``app/scaling/tasks.py::_collaboration_tick_for_org``) or the read
        # fails, fall back to the caller's already-resolved ``day_spend_usd``
        # (the org's general spend) as a conservative, fail-closed proxy —
        # cutting collaboration chatter off no later than an unmetered budget
        # would.
        collab_spend_usd = day_spend_usd
        if self._counters is not None:
            try:
                collab_spend_usd = await self._counters.snapshot_collab_spend()
            except Exception as exc:
                _log.warning(
                    "collaboration_tick.spend_snapshot_failed", org_id=org_id, error=str(exc)
                )
                collab_spend_usd = day_spend_usd
        if collab_spend_usd >= settings.collaboration_daily_budget_usd:
            return 0

        emitted = 0
        with _tracer.start_as_current_span("collaboration_tick.run") as span:
            span.set_attribute("org_id", org_id)
            span.set_attribute("autonomy_level", autonomy_level)
            span.set_attribute("cap", cap)

            for lead in leads[:cap]:
                prompt = (
                    f"You are {lead}, a department lead inside an autonomous "
                    "organization. In one short sentence (under 20 words), share "
                    "a status update or next step with the rest of the team. "
                    "No preamble, no quotation marks."
                )
                try:
                    (
                        message,
                        latency_ms,
                        tokens,
                        cost_usd,
                    ) = await self._model_gateway.complete_short(
                        prompt, max_tokens=_MAX_TOKENS_PER_MESSAGE
                    )
                except Exception as exc:
                    # Fail-closed: stop the tick entirely. Whatever was already
                    # published above stays published; nothing further is
                    # attempted.
                    _log.warning(
                        "collaboration_tick.model_error",
                        org_id=org_id,
                        lead=lead,
                        error=str(exc),
                    )
                    break

                message = (message or "").strip()
                if not message:
                    continue

                await self._event_publisher.publish(
                    event_type=EVENT_TYPE_COLLABORATION_MESSAGE,
                    org_id=org_id,
                    tenant_id=tenant_id,
                    payload={
                        # Kept for backward-compat with existing consumers.
                        "lead": lead,
                        "message": message,
                        # Typed enrichment for the Situation Room UX.
                        "from_agent": lead,
                        "to": "team",
                        "kind": classify_message_kind(message),
                        "latency_ms": latency_ms,
                        "tokens": tokens,
                        "cost_usd": cost_usd,
                        # This ambient tick is not (currently) tied to any
                        # specific mission -- no mission context is threaded
                        # through here, so this is always None today.
                        "mission_id": None,
                    },
                )
                emitted += 1
                if self._counters is not None:
                    try:
                        # Record the real per-message cost when the gateway
                        # reported real usage; ``cost_usd`` already falls
                        # back to ``_EST_COST_USD_PER_MESSAGE`` otherwise, so
                        # this is always the right amount to charge.
                        await self._counters.record_collab_spend(cost_usd)
                    except Exception as exc:
                        _log.warning(
                            "collaboration_tick.spend_record_failed", org_id=org_id, error=str(exc)
                        )

            span.set_attribute("messages_emitted", emitted)

        return emitted
