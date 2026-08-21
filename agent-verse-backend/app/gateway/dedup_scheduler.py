"""Command deduplication + scheduling — QA8 + QA9 of spec.

CommandDeduplicator (QA8):
  Prevents the same command from executing twice.
  Uses Redis 30-second window; falls back to in-memory.

ScheduledCommand (QA9):
  Users schedule commands for future execution via any channel.
  Stored in DB; Celery Beat fires them at the scheduled time.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── CommandDeduplicator (QA8) ─────────────────────────────────────────────────


class CommandDeduplicator:
    """
    Prevents duplicate command execution.
    Critical for: button double-taps, network retries, webhook replay.

    Dedup key = hash(tenant_id + org_id + channel + actor + text + time_bucket)
    Time bucket = floor(timestamp / 30s) — 30-second idempotency window.
    """

    WINDOW_SECONDS = 30
    TTL_SECONDS = 300  # 5 minutes

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._memory: dict[str, str] = {}  # key → command_id

    def _make_key(
        self,
        tenant_id: str,
        org_id: str,
        channel: str,
        actor_id: str,
        text: str,
    ) -> str:
        bucket = int(time.time()) // self.WINDOW_SECONDS
        raw = f"{tenant_id}:{org_id}:{channel}:{actor_id}:{text[:200]}:{bucket}"
        return "cmd_dedup:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def check_and_reserve(
        self,
        command_id: str,
        tenant_id: str,
        org_id: str,
        channel: str,
        actor_id: str,
        text: str,
    ) -> bool:
        """
        Returns True if command is new (safe to process).
        Returns False if command was already seen (skip).
        """
        with _tracer.start_as_current_span("dedup.check") as span:
            key = self._make_key(tenant_id, org_id, channel, actor_id, text)
            span.set_attribute("dedup_key", key[:16])

            if self._redis:
                # NX: only set if not exists; EX: auto-expire
                result = await self._redis.set(key, command_id, nx=True, ex=self.TTL_SECONDS)
                is_new = result is not None
            else:
                is_new = key not in self._memory
                if is_new:
                    self._memory[key] = command_id
                    # Schedule cleanup (fire and forget)
                    asyncio.create_task(self._expire_key(key))

            if not is_new:
                _log.debug("dedup.duplicate_skipped", channel=channel, actor=actor_id)

            return is_new

    async def _expire_key(self, key: str) -> None:
        await asyncio.sleep(self.TTL_SECONDS)
        self._memory.pop(key, None)


# ── ScheduledCommand (QA9) ────────────────────────────────────────────────────


@dataclass
class ScheduledCommand:
    """
    A command scheduled for future execution.
    Created by users via any channel with phrases like:
      "Remind me to check the Germany mission tomorrow at 9am"
      "Start market intelligence mission every weekday at 7am"
      "Send me a status update every Friday at 5pm"
    """

    scheduled_id: str
    org_id: str
    tenant_id: str
    command_text: str
    execute_at: datetime  # specific time (UTC)
    repeat: str | None = None  # "daily" | "weekly" | "monthly" | None
    channel: str = "rest"  # which channel to respond on
    actor_id: str = ""
    created_at: datetime = None  # type: ignore
    status: str = "pending"  # pending | executed | cancelled | failed

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


class CommandScheduler:
    """
    Manages scheduled commands (QA9).
    In production: backed by DB + Celery Beat.
    Development: in-memory list.
    """

    def __init__(self) -> None:
        self._scheduled: dict[str, ScheduledCommand] = {}

    async def schedule(self, cmd: ScheduledCommand) -> str:
        """Store a scheduled command for future execution."""
        self._scheduled[cmd.scheduled_id] = cmd
        _log.info(
            "command.scheduled",
            scheduled_id=cmd.scheduled_id,
            execute_at=cmd.execute_at.isoformat(),
            repeat=cmd.repeat,
            channel=cmd.channel,
        )
        return cmd.scheduled_id

    async def cancel(self, scheduled_id: str) -> bool:
        """Cancel a scheduled command."""
        cmd = self._scheduled.get(scheduled_id)
        if not cmd:
            return False
        cmd.status = "cancelled"
        _log.info("command.cancelled", scheduled_id=scheduled_id)
        return True

    async def list_pending(self, org_id: str) -> list[ScheduledCommand]:
        return [c for c in self._scheduled.values() if c.org_id == org_id and c.status == "pending"]

    async def get_due(self) -> list[ScheduledCommand]:
        """Return commands that are due for execution."""
        now = datetime.now(UTC)
        return [
            c for c in self._scheduled.values() if c.status == "pending" and c.execute_at <= now
        ]

    async def mark_executed(self, scheduled_id: str) -> None:
        cmd = self._scheduled.get(scheduled_id)
        if not cmd:
            return
        if cmd.repeat is None:
            cmd.status = "executed"
        else:
            # Reschedule for next occurrence
            from datetime import timedelta

            delta = {
                "daily": timedelta(days=1),
                "weekly": timedelta(weeks=1),
                "monthly": timedelta(days=30),
            }
            if cmd.repeat in delta:
                cmd.execute_at = cmd.execute_at + delta[cmd.repeat]
            _log.info(
                "command.rescheduled", scheduled_id=scheduled_id, next_at=cmd.execute_at.isoformat()
            )
