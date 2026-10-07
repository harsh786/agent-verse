"""Proactive engine — orchestrates signal → proposal → consent → delivery (Phase 9).

Ties the pieces together safely:
1. kill switch (global off-switch) short-circuits everything;
2. the planner proposes a message (or stays silent);
3. the per-principal consent/rate/quiet-hours gate (``app.chat.proactive``) decides;
4. allowed messages go out via the injected multi-channel ``deliver`` callback and
   are logged to audit as ``source=proactive``; the daily counter increments.

The engine never executes an action itself — high-impact proposals are delivered
as confirmation requests, and the user's reply runs through the normal governed
chat flow. All collaborators are injected so it is fully unit-testable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from app.chat.proactive import ProactivePreferences, evaluate_proactive
from app.proactive.planner import ProactivePlanner, ProactiveProposal
from app.proactive.signals import ProactiveSignal

# principal_id -> preferences
PreferencesProvider = Callable[[str], ProactivePreferences]
# (signal, proposal) -> awaitable — the callback has the full signal (tenant_id,
# principal_id, channel, payload) so it can deliver into the right chat/channel.
# It raises when nothing was delivered; its result may carry ``channel_delivered``
# (``app.chat.proactive.ProactiveDelivery``).
DeliverFn = Callable[[ProactiveSignal, ProactiveProposal], Awaitable[Any]]
# audit sink: (event: dict) -> awaitable | None
AuditFn = Callable[[dict[str, Any]], Any]
Clock = Callable[[], datetime]
# Durable rate-limit counter hooks (tenant_id, principal_id, iso-day). When
# omitted the engine uses an in-memory counter — which resets on restart and is
# NOT shared across replicas, so the daily rate limit (a consent control) is only
# advisory. Production MUST inject a shared/durable counter (Redis/DB). The key
# carries the tenant: an explicit principal id is only unique within its tenant.
CountProvider = Callable[[str, str, str], Any]  # -> int | Awaitable[int]
CountRecorder = Callable[[str, str, str], Any]  # -> None | Awaitable[None]

_log = structlog.get_logger(__name__)


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
        count_provider: CountProvider | None = None,
        count_recorder: CountRecorder | None = None,
    ) -> None:
        self._planner = planner or ProactivePlanner()
        self._prefs = preferences_provider or (lambda _pid: ProactivePreferences())
        self._deliver = deliver
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self.kill_switch = kill_switch
        self._count_provider = count_provider
        self._count_recorder = count_recorder
        # In-memory fallback counter (tenant_id, principal_id, iso-date) -> sent.
        self._sent: dict[tuple[str, str, str], int] = {}

    async def _sent_today(self, tenant_id: str, principal_id: str, day: str) -> int:
        if self._count_provider is not None:
            result = self._count_provider(tenant_id, principal_id, day)
            if hasattr(result, "__await__"):
                return int(await result)
            return int(result)
        return self._sent.get((tenant_id, principal_id, day), 0)

    async def _record_sent(self, tenant_id: str, principal_id: str, day: str) -> None:
        if self._count_recorder is not None:
            result = self._count_recorder(tenant_id, principal_id, day)
            if hasattr(result, "__await__"):
                await result
            return
        key = (tenant_id, principal_id, day)
        self._sent[key] = self._sent.get(key, 0) + 1

    async def _propose(self, signal: ProactiveSignal) -> ProactiveProposal | None:
        apropose = getattr(self._planner, "apropose", None)
        if callable(apropose):
            return await apropose(signal)
        return self._planner.propose(signal)

    async def handle(self, signal: ProactiveSignal) -> ProactiveOutcome:
        """Process one signal end-to-end under all guards."""
        if self.kill_switch:
            return ProactiveOutcome(False, "kill_switch")

        proposal = await self._propose(signal)
        if proposal is None:
            return ProactiveOutcome(False, "no_proposal")

        now = self._clock()
        day = now.date().isoformat()
        prefs = self._prefs(signal.principal_id)
        decision = evaluate_proactive(
            prefs,
            now_hour=now.hour,
            sent_today=await self._sent_today(signal.tenant_id, signal.principal_id, day),
            channel=signal.channel,
        )
        if not decision.allow:
            return ProactiveOutcome(False, decision.reason, proposal)

        try:
            result = await self._deliver(signal, proposal)
        except Exception as exc:
            # Nothing reached the principal: report it, never "delivered".
            _log.warning(
                "proactive_delivery_failed",
                tenant_id=signal.tenant_id,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            return ProactiveOutcome(False, "delivery_failed", proposal)
        channel_delivered = getattr(result, "channel_delivered", None)
        await self._record_sent(signal.tenant_id, signal.principal_id, day)
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
