"""Personalization / style layer (Phase 11).

Gives the assistant a durable, per-principal sense of *you*: tone, standing
instructions ("always book aisle seats", "always CC my partner"), and structured
preferences. These are injected into every turn's context so replies and decisions
respect them across sessions and channels — retrieval-based personalization first
(zero training cost), with a path to per-principal fine-tune via the model router.

A *principal* is either an org member or a standalone individual; until the
dual-mode identity layer lands, ``principal_id`` defaults to the tenant id.

This module is storage-agnostic: ``PersonalizationStore`` is a small protocol with
an in-memory default; a durable Postgres-backed store swaps in later (same staged
pattern as the chat persistence swap) without touching callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class PersonalProfile:
    """A principal's durable style + preferences."""

    principal_id: str
    tone: str | None = None
    standing_instructions: list[str] = field(default_factory=list)
    preferences: dict[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=_now)

    def is_empty(self) -> bool:
        return not (self.tone or self.standing_instructions or self.preferences)


# ── Injection rendering ──────────────────────────────────────────────────────

def render_personalization_block(profile: PersonalProfile | None) -> str:
    """Render a profile as a system-prompt block for the context pipeline.

    Returns "" when there is nothing to say, so the injector becomes a no-op.
    """
    if profile is None or profile.is_empty():
        return ""
    lines: list[str] = ["User personalization — honor these in your reply:"]
    if profile.tone:
        lines.append(f"- Preferred tone: {profile.tone}")
    for instr in profile.standing_instructions:
        lines.append(f"- Standing instruction: {instr}")
    for key, val in profile.preferences.items():
        lines.append(f"- Preference — {key}: {val}")
    return "\n".join(lines)


# ── Preference learning (explicit) ───────────────────────────────────────────
#
# Capture durable standing instructions the user states in passing, e.g.
# "always book aisle seats", "from now on CC my partner", "please always use
# metric units". Kept intentionally conservative (explicit imperative phrasing)
# to avoid mislearning one-off requests.

_STANDING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfrom now on,?\s+(.*)", re.IGNORECASE),
    re.compile(r"\balways\s+(.*)", re.IGNORECASE),
    re.compile(r"\bnever\s+(.*)", re.IGNORECASE),
    re.compile(r"\bevery time,?\s+(.*)", re.IGNORECASE),
    re.compile(r"\bgoing forward,?\s+(.*)", re.IGNORECASE),
)


def extract_standing_instruction(text: str) -> str | None:
    """Detect a durable standing instruction in a user message.

    Returns a normalized instruction string, or None when the message states no
    lasting preference. The returned instruction preserves the always/never verb
    so intent survives (e.g. "always book aisle seats", "never call after 9pm").
    """
    if not text:
        return None
    stripped = text.strip().rstrip(".")
    # Prefer the "always/never" forms verbatim (they carry their own verb).
    for verb in ("always", "never"):
        m = re.search(rf"\b{verb}\s+(.+)", stripped, re.IGNORECASE)
        if m:
            tail = m.group(1).strip()
            if tail:
                return f"{verb} {tail}"
    for pat in _STANDING_PATTERNS:
        m = pat.search(stripped)
        if m:
            tail = m.group(1).strip()
            if tail:
                return tail
    return None


# ── Store ────────────────────────────────────────────────────────────────────

class PersonalizationStore(Protocol):
    async def get(self, principal_id: str) -> PersonalProfile | None: ...

    async def add_standing_instruction(
        self, principal_id: str, instruction: str
    ) -> PersonalProfile: ...

    async def set_tone(self, principal_id: str, tone: str) -> PersonalProfile: ...

    async def set_preference(
        self, principal_id: str, key: str, value: str
    ) -> PersonalProfile: ...


class InMemoryPersonalizationStore:
    """Default store — durable Postgres-backed store swaps in later."""

    def __init__(self) -> None:
        self._profiles: dict[str, PersonalProfile] = {}

    def _profile(self, principal_id: str) -> PersonalProfile:
        prof = self._profiles.get(principal_id)
        if prof is None:
            prof = PersonalProfile(principal_id=principal_id)
            self._profiles[principal_id] = prof
        return prof

    async def get(self, principal_id: str) -> PersonalProfile | None:
        return self._profiles.get(principal_id)

    async def add_standing_instruction(
        self, principal_id: str, instruction: str
    ) -> PersonalProfile:
        prof = self._profile(principal_id)
        # De-dupe case-insensitively; keep the most recent phrasing.
        lowered = instruction.strip().lower()
        prof.standing_instructions = [
            s for s in prof.standing_instructions if s.strip().lower() != lowered
        ]
        prof.standing_instructions.append(instruction.strip())
        prof.updated_at = _now()
        return prof

    async def set_tone(self, principal_id: str, tone: str) -> PersonalProfile:
        prof = self._profile(principal_id)
        prof.tone = tone.strip() or None
        prof.updated_at = _now()
        return prof

    async def set_preference(
        self, principal_id: str, key: str, value: str
    ) -> PersonalProfile:
        prof = self._profile(principal_id)
        prof.preferences[key.strip()] = value.strip()
        prof.updated_at = _now()
        return prof
