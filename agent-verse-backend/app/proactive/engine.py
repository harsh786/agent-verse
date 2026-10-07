"""Proactive engine — orchestrates signal → proposal → consent → delivery (Phase 9).

Ties the pieces together safely:
1. kill switch (global off-switch) short-circuits everything;
2. the planner proposes a message (or stays silent);
3. the principal must have OPTED IN: its stored preferences
   (``app.proactive.preferences``) are loaded per (tenant, principal) — no record
   means no outreach (``not_opted_in``);
4. the consent gate (``app.chat.proactive``) checks enabled / channel / quiet
   hours, measured in the principal's own timezone;
5. one slot of the principal's daily cap is reserved atomically on the shared
   counter (``app.proactive.limits``); a full cap is ``rate_limited`` and an
   unavailable counter sends nothing (``rate_limit_unavailable``);
6. the message goes out via the injected ``deliver`` callback (a failed delivery
   gives the slot back) and is logged to audit as ``source=proactive``.

The engine never executes an action itself — high-impact proposals are delivered
as confirmation requests, and the user's reply runs through the normal governed
chat flow. All collaborators are injected so it is fully unit-testable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from app.chat.proactive import ProactivePreferences, evaluate_proactive
from app.proactive.limits import DailyCap, InMemoryDailyCap
from app.proactive.planner import ProactivePlanner, ProactiveProposal
from app.proactive.signals import ProactiveSignal

# (tenant_id, principal_id) -> the principal's stored preferences, or None when it
# never opted in (sync or awaitable). Raising means "cannot tell": nothing is sent.
PreferencesProvider = Callable[[str, str], Any]
# (signal, proposal) -> awaitable — the callback has the full signal (tenant_id,
# principal_id, channel, payload) so it can deliver into the right chat/channel.
# It raises when nothing was delivered; its result may carry ``channel_delivered``
# (``app.chat.proactive.ProactiveDelivery``).
DeliverFn = Callable[[ProactiveSignal, ProactiveProposal], Awaitable[Any]]
# audit sink: (event: dict) -> awaitable | None
AuditFn = Callable[[dict[str, Any]], Any]
Clock = Callable[[], datetime]

_log = structlog.get_logger(__name__)


def _no_opt_ins(_tenant_id: str, _principal_id: str) -> None:
    return None


def _local_now(now: datetime, timezone: str) -> datetime:
    try:
        return now.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return now.astimezone(UTC)


@dataclass(frozen=True)
class ProactiveOutcome:
    delivered: bool
    reason: str
    proposal: ProactiveProposal | None = None
    requires_confirmation: bool = False
    # None = only the web thread was targeted; True/False = whether the push to
    # the signal's external channel succeeded (False also when none is wired).
    channel_delivered: bool | None = None


class ProactiveEngine:
    def __init__(
        self,
        *,
        planner: ProactivePlanner | None = None,
        preferences_provider: PreferencesProvider | None = None,
        deliver: DeliverFn,
        audit: AuditFn | None = None,
        clock: Clock | None = None,
        kill_switch: bool = False,
        daily_cap: DailyCap | None = None,
    ) -> None:
        self._planner = planner or ProactivePlanner()
        # Opt-in: without a provider nobody has opted in, so nothing is sent.
        self._prefs: PreferencesProvider = preferences_provider or _no_opt_ins
        self._deliver = deliver
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self.kill_switch = kill_switch
        # Per-process fallback for tests; the app wires the shared AppStateDailyCap.
        self._cap: DailyCap = daily_cap or InMemoryDailyCap()

    async def _propose(self, signal: ProactiveSignal) -> ProactiveProposal | None:
        apropose = getattr(self._planner, "apropose", None)
        if callable(apropose):
            return await apropose(signal)
        return self._planner.propose(signal)

    async def _preferences(self, signal: ProactiveSignal) -> ProactivePreferences | None:
        result = self._prefs(signal.tenant_id, signal.principal_id)
        if hasattr(result, "__await__"):
            result = await result
        return result if isinstance(result, ProactivePreferences) else None

    async def handle(self, signal: ProactiveSignal) -> ProactiveOutcome:
        """Process one signal end-to-end under all guards."""
        if self.kill_switch:
            return ProactiveOutcome(False, "kill_switch")

        proposal = await self._propose(signal)
        if proposal is None:
            return ProactiveOutcome(False, "no_proposal")

        try:
            prefs = await self._preferences(signal)
        except Exception as exc:
            _log.warning(
                "proactive_preferences_unavailable",
                tenant_id=signal.tenant_id,
                error_type=type(exc).__name__,
            )
            return ProactiveOutcome(False, "preferences_unavailable", proposal)
        if prefs is None:
            return ProactiveOutcome(False, "not_opted_in", proposal)

        local = _local_now(self._clock(), prefs.timezone)
        day = local.date().isoformat()
        # The daily limit is enforced by the atomic reservation below, not by a
        # read-then-check count (which concurrent replicas would overshoot).
        decision = evaluate_proactive(
            prefs, now_hour=local.hour, sent_today=0, channel=signal.channel
        )
        if not decision.allow:
            return ProactiveOutcome(False, decision.reason, proposal)

        try:
            reserved = await self._cap.reserve(
                signal.tenant_id, signal.principal_id, day, prefs.max_per_day
            )
        except Exception as exc:
            _log.warning(
                "proactive_rate_limit_unavailable",
                tenant_id=signal.tenant_id,
                error_type=type(exc).__name__,
            )
            return ProactiveOutcome(False, "rate_limit_unavailable", proposal)
        if not reserved:
            return ProactiveOutcome(False, "rate_limited", proposal)

        try:
            result = await self._deliver(signal, proposal)
        except Exception as exc:
            # Nothing reached the principal: report it, never "delivered", and give
            # the reserved slot back.
            _log.warning(
                "proactive_delivery_failed",
                tenant_id=signal.tenant_id,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            await self._cap.release(signal.tenant_id, signal.principal_id, day)
            return ProactiveOutcome(False, "delivery_failed", proposal)
        channel_delivered = getattr(result, "channel_delivered", None)
        await self._write_audit(signal, proposal, channel_delivered=channel_delivered)
        # The thread got the message; when the signal named an external channel and
        # the push did not happen, say so instead of a bare "delivered".
        reason = "delivered" if channel_delivered is not False else "delivered_thread_only"
        return ProactiveOutcome(
            True,
            reason,
            proposal,
            requires_confirmation=proposal.requires_confirmation,
            channel_delivered=channel_delivered,
        )

    async def _write_audit(
        self,
        signal: ProactiveSignal,
        proposal: ProactiveProposal,
        *,
        channel_delivered: bool | None = None,
    ) -> None:
        if self._audit is None:
            return
        event = {
            "source": "proactive",
            "tenant_id": signal.tenant_id,
            "principal_id": signal.principal_id,
            "channel": signal.channel,
            "signal_kind": signal.kind,
            "action": proposal.action,
            "high_impact": proposal.high_impact,
            "requires_confirmation": proposal.requires_confirmation,
            "channel_delivered": channel_delivered,
        }
        result = self._audit(event)
        if hasattr(result, "__await__"):
            await result
