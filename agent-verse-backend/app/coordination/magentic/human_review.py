"""One-time, session-scoped Magentic human-review responses."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any


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
        # Wired by the app lifespan; when set, tokens/decisions persist to Postgres
        # so a token issued on one pod can be consumed on another, and the
        # one-time-consume is enforced cluster-wide by an atomic conditional UPDATE.
        self._db_factory: Any = None

    def set_db(self, db_factory: Any) -> None:
        self._db_factory = db_factory

    async def issue(self, tenant_id: str, session_id: str, token: str) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if self._db_factory is not None:
            from sqlalchemy import text as _t

            async with self._db_factory() as s, s.begin():
                await s.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                await s.execute(
                    _t(
                        "INSERT INTO magentic_review_tokens (tenant_id, session_id, "
                        "token_hash) VALUES (:tid, :sid, :th) "
                        "ON CONFLICT (tenant_id, session_id) DO UPDATE SET "
                        "token_hash = EXCLUDED.token_hash, consumed = FALSE, "
                        "approved = NULL, safe_note = NULL, consumed_at = NULL"
                    ),
                    {"tid": tenant_id, "sid": session_id, "th": token_hash},
                )
            return
        async with self._lock:
            self._tokens[(tenant_id, session_id)] = token_hash

    async def submit(
        self,
        tenant_id: str,
        session_id: str,
        *,
        token: str,
        approved: bool,
        safe_note: str,
    ) -> HumanReviewDecision:
        supplied = hashlib.sha256(token.encode()).hexdigest()
        note = safe_note[:2_000]
        if self._db_factory is not None:
            from sqlalchemy import text as _t

            # Atomic one-time consume across pods: only an unconsumed row whose
            # token matches flips to consumed and returns — a stale/already-consumed
            # token or a wrong token returns nothing.
            async with self._db_factory() as s, s.begin():
                await s.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                row = (
                    await s.execute(
                        _t(
                            "UPDATE magentic_review_tokens SET consumed = TRUE, "
                            "approved = :appr, safe_note = :note, consumed_at = now() "
                            "WHERE tenant_id = :tid AND session_id = :sid "
                            "AND token_hash = :th AND consumed = FALSE RETURNING session_id"
                        ),
                        {
                            "appr": approved, "note": note, "tid": tenant_id,
                            "sid": session_id, "th": supplied,
                        },
                    )
                ).first()
            if row is None:
                raise PermissionError("stale, invalid, or already-consumed human-review token")
            return HumanReviewDecision(
                tenant_id=tenant_id, session_id=session_id, approved=approved, safe_note=note
            )

        key = (tenant_id, session_id)
        async with self._lock:
            expected = self._tokens.get(key)
            if expected is None or supplied != expected:
                raise PermissionError("stale or invalid human-review token")
            if key in self._decisions:
                raise PermissionError("human-review token already consumed")
            decision = HumanReviewDecision(
                tenant_id=tenant_id,
                session_id=session_id,
                approved=approved,
                safe_note=note,
            )
            self._decisions[key] = decision
            self._tokens.pop(key, None)
            return decision


__all__ = ["HumanReviewDecision", "MagenticHumanReviewService"]
