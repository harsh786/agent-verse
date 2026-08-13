"""One-time, session-scoped Magentic human-review responses."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HumanReviewDecision:
    tenant_id: str
    session_id: str
    approved: bool
    safe_note: str


class MagenticHumanReviewService:
    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._decisions: dict[tuple[str, str], HumanReviewDecision] = {}
        self._lock = asyncio.Lock()

    async def issue(self, tenant_id: str, session_id: str, token: str) -> None:
        async with self._lock:
            self._tokens[(tenant_id, session_id)] = hashlib.sha256(token.encode()).hexdigest()

    async def submit(
        self,
        tenant_id: str,
        session_id: str,
        *,
        token: str,
        approved: bool,
        safe_note: str,
    ) -> HumanReviewDecision:
        key = (tenant_id, session_id)
        async with self._lock:
            expected = self._tokens.get(key)
            supplied = hashlib.sha256(token.encode()).hexdigest()
            if expected is None or supplied != expected:
                raise PermissionError("stale or invalid human-review token")
            if key in self._decisions:
                raise PermissionError("human-review token already consumed")
            decision = HumanReviewDecision(
                tenant_id=tenant_id,
                session_id=session_id,
                approved=approved,
                safe_note=safe_note[:2_000],
            )
            self._decisions[key] = decision
            self._tokens.pop(key, None)
            return decision


__all__ = ["HumanReviewDecision", "MagenticHumanReviewService"]
