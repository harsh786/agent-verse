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

from app.chat.proactive import ProactivePreferences, evaluate_proactive
from app.proactive.planner import ProactivePlanner, ProactiveProposal
from app.proactive.signals import ProactiveSignal

# principal_id -> preferences
PreferencesProvider = Callable[[str], ProactivePreferences]
# (principal_id, channel, message, proposal) -> awaitable
DeliverFn = Callable[[str, str, str, ProactiveProposal], Awaitable[Any]]
# audit sink: (event: dict) -> awaitable | None
AuditFn = Callable[[dict[str, Any]], Any]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ProactiveOutcome:
    delivered: bool
    reason: str
    proposal: ProactiveProposal | None = None
    requires_confirmation: bool = False


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
    ) -> None:
        self._planner = planner or ProactivePlanner()
        self._prefs = preferences_provider or (lambda _pid: ProactivePreferences())
        self._deliver = deliver
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self.kill_switch = kill_switch
        # (principal_id, iso-date) -> count sent today
        self._sent: dict[tuple[str, str], int] = {}

    def _sent_today(self, principal_id: str, day: str) -> int:
        return self._sent.get((principal_id, day), 0)

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
            sent_today=self._sent_today(signal.principal_id, day),
            channel=signal.channel,
        )
        if not decision.allow:
            return ProactiveOutcome(False, decision.reason, proposal)

        await self._deliver(signal.principal_id, signal.channel, proposal.message, proposal)
        self._sent[(signal.principal_id, day)] = (
            self._sent_today(signal.principal_id, day) + 1
        )
        await self._write_audit(signal, proposal)
        return ProactiveOutcome(
            True, "delivered", proposal, requires_confirmation=proposal.requires_confirmation
        )

    async def _write_audit(
        self, signal: ProactiveSignal, proposal: ProactiveProposal
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
        }
        result = self._audit(event)
        if hasattr(result, "__await__"):
            await result
