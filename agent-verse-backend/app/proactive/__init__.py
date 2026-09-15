"""Proactive-outreach engine (Phase 9).

The assistant acts without being asked — reminders, follow-ups, "I noticed X,
want me to handle it?" — safely: a signal bus feeds a planner that proposes an
action, the proposal passes the per-principal consent/rate/quiet-hours gate
(``app.chat.proactive``), and only then is delivered over the multi-channel path
and logged to audit as ``source=proactive``. High-impact proposals are delivered
as a *confirmation request* (the engine never auto-executes) — the user's reply
runs the action through the normal governed chat flow.
"""

from __future__ import annotations

from app.proactive.engine import ProactiveEngine, ProactiveOutcome
from app.proactive.planner import ProactivePlanner, ProactiveProposal
from app.proactive.signals import ProactiveSignal, SignalBus

__all__ = [
    "ProactiveEngine",
    "ProactiveOutcome",
    "ProactivePlanner",
    "ProactiveProposal",
    "ProactiveSignal",
    "SignalBus",
]
