"""Deterministic speaker-selection policies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SpeakerDecision:
    agent_id: str
    policy: str
    safe_summary: str


class RoundRobinSpeakerPolicy:
    def select(self, active_agents: tuple[str, ...], *, turn: int, **_: Any) -> SpeakerDecision:
        if not active_agents:
            raise ValueError("no active group-chat participant")
        selected = active_agents[turn % len(active_agents)]
        return SpeakerDecision(selected, "round_robin", "selected by stable active order")


class RuleBasedSpeakerPolicy:
    def __init__(self, rule: Callable[[tuple[str, ...], dict[str, Any]], str]) -> None:
        self._rule = rule

    def select(
        self, active_agents: tuple[str, ...], *, context: dict[str, Any], **_: Any
    ) -> SpeakerDecision:
        selected = self._rule(active_agents, context)
        if selected not in active_agents:
            raise ValueError("rule selected an inactive participant")
        return SpeakerDecision(selected, "rule_based", "selected by configured rule")


class AgentBasedSpeakerPolicy:
    async def select(
        self, active_agents: tuple[str, ...], *, selector: Any, context: dict[str, Any], **_: Any
    ) -> SpeakerDecision:
        selected = await selector(active_agents, context)
        if selected not in active_agents:
            raise ValueError("agent selected an inactive participant")
        return SpeakerDecision(selected, "agent_based", "selected by governed selector")


__all__ = [
    "AgentBasedSpeakerPolicy",
    "RoundRobinSpeakerPolicy",
    "RuleBasedSpeakerPolicy",
    "SpeakerDecision",
]
