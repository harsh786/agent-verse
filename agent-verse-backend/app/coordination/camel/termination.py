"""Deterministic CAMEL dialogue termination checks."""

from __future__ import annotations

import re
from datetime import UTC, datetime


def normalize_utterance(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def terminal_reason(
    *,
    completed: bool,
    agreement: bool,
    turn_count: int,
    maximum_turns: int,
    tokens: int,
    maximum_tokens: int,
    cost_usd: float,
    maximum_cost_usd: float,
    deadline: datetime,
    repeated_utterances: int,
) -> str | None:
    if datetime.now(UTC) >= deadline:
        return "deadline_exceeded"
    if tokens > maximum_tokens:
        return "token_limit"
    if cost_usd > maximum_cost_usd:
        return "cost_limit"
    if repeated_utterances >= 2:
        return "repeated_dialogue"
    if completed and agreement:
        return "completed"
    if turn_count >= maximum_turns:
        return "turn_limit"
    return None


__all__ = ["normalize_utterance", "terminal_reason"]
