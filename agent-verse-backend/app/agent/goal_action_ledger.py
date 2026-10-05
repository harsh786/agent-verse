"""Goal-scoped ledger of executed side-effecting tool calls and approval decisions (OI-1).

In a supervised goal an approved ``mongodb_delete_one`` ran (``{'deleted': 1}``);
the verifier / replanner then planned the delete again, every replan filed new
approvals (19-22 in one goal) and re-ran the delete (``deleted: 0``) until the
goal failed although the work was done. Two records stop that:

* **executed calls** — keyed by the tool-call fingerprint (server, tool,
  canonical arguments) and recording the step that ran it. A non-read call that
  already succeeded in this goal is never dispatched again: its recorded result
  is replayed (a replanned or retried step completes on the real outcome).
* **approval decisions** — an explicit APPROVED decision for one exact action
  (a tool call's fingerprint, or a step's exact text) is reused for an identical
  request later in the same goal. Nothing broader: a different argument, a
  different step text, or another goal asks a human again. Rejections and
  timeouts are never reused (they fail the step).

Both live on ``AgentState.context`` (persisted with each step checkpoint, so a
crash-resume keeps them) and, when a Redis client is available, in one Redis
hash per goal so another replica / a later attempt of the goal sees them too.
A Redis read error is never treated as "already done" or "already approved":
the caller falls back to the normal gates (a human is asked again).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

LEDGER_CONTEXT_KEY = "_goal_action_ledger"
_EXECUTED = "executed"
_APPROVED = "approved"
_KEY_PREFIX = "agentverse:goal_action_ledger"
_DEFAULT_TTL_S = 7 * 24 * 3600
_MAX_OUTPUT_CHARS = 4000
_MAX_ARGS_CHARS = 300
# Bound the per-goal ledger kept on the (checkpointed) state.
_MAX_ENTRIES = 200


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def call_fingerprint(server_id: str, tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Stable fingerprint of one tool call: server, tool and canonical arguments."""
    payload = _canonical({"s": server_id or "", "t": tool_name or "", "a": arguments or {}})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def call_approval_key(fingerprint: str) -> str:
    return f"call:{fingerprint}"


def action_approval_key(
    action: str, tool_name: str = "", arguments: dict[str, Any] | None = None
) -> str:
    """Approval key for a gate whose request names ``action`` (and a tool call)."""
    digest = hashlib.sha256(
        _canonical({"x": action or "", "t": tool_name or "", "a": arguments or {}}).encode()
    ).hexdigest()
    return f"act:{digest}"


def step_approval_key(step_text: str) -> str:
    """Approval key for a step-level gate: the exact step text the human saw."""
    digest = hashlib.sha256(str(step_text or "").encode("utf-8")).hexdigest()
    return f"step:{digest}"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class GoalActionLedger:
    """Per-goal view over the context mirror and the shared Redis hash."""

    def __init__(
        self,
        context: dict[str, Any],
        *,
        tenant_id: str,
        goal_id: str,
        redis: Any = None,
        ttl_seconds: int = _DEFAULT_TTL_S,
    ) -> None:
        self._ctx = context
        self._tenant_id = str(tenant_id or "")
        self._goal_id = str(goal_id or "")
        self._redis = redis
        self._ttl = int(ttl_seconds)

    # ── storage helpers ────────────────────────────────────────────────────

    @property
    def _key(self) -> str:
        return f"{_KEY_PREFIX}:{self._tenant_id}:{self._goal_id}"

    def _section(self, name: str) -> dict[str, Any]:
        ledger = self._ctx.setdefault(LEDGER_CONTEXT_KEY, {})
        if not isinstance(ledger, dict):
            ledger = {}
            self._ctx[LEDGER_CONTEXT_KEY] = ledger
        section = ledger.setdefault(name, {})
        if not isinstance(section, dict):
            section = {}
            ledger[name] = section
        return section

    def _remember(self, section: str, key: str, entry: dict[str, Any]) -> None:
        store = self._section(section)
        store[key] = entry
        while len(store) > _MAX_ENTRIES:
            store.pop(next(iter(store)))

    async def _lookup(self, section: str, key: str) -> dict[str, Any] | None:
        local = self._section(section).get(key)
        if isinstance(local, dict):
            return local
        if self._redis is None or not self._goal_id:
            return None
        field = f"{section[0]}:{key}"
        try:
            raw = await _maybe_await(self._redis.hget(self._key, field))
        except Exception as exc:
            logger.warning(
                "goal_action_ledger_read_failed",
                goal_id=self._goal_id,
                error=f"{type(exc).__name__}: {str(exc)[:120]}",
            )
            return None
        if not raw or not isinstance(raw, str | bytes):
            return None
        try:
            entry = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(entry, dict):
            return None
        self._remember(section, key, entry)
        return entry

    async def _store(self, section: str, key: str, entry: dict[str, Any]) -> bool:
        self._remember(section, key, entry)
        if self._redis is None or not self._goal_id:
            return True
        field = f"{section[0]}:{key}"
        try:
            await _maybe_await(self._redis.hset(self._key, field, _canonical(entry)))
            await _maybe_await(self._redis.expire(self._key, self._ttl))
        except Exception as exc:
            logger.warning(
                "goal_action_ledger_write_failed",
                goal_id=self._goal_id,
                error=f"{type(exc).__name__}: {str(exc)[:120]}",
            )
            return False
        return True

    # ── executed side-effecting calls ──────────────────────────────────────

    async def executed(self, fingerprint: str) -> dict[str, Any] | None:
        return await self._lookup(_EXECUTED, fingerprint)

    async def record_executed(
        self,
        fingerprint: str,
        *,
        step_id: str,
        tool: str,
        server_id: str,
        arguments: dict[str, Any] | None,
        output: str,
    ) -> bool:
        entry = {
            "step_id": str(step_id or ""),
            "tool": str(tool or ""),
            "server_id": str(server_id or ""),
            "arguments": _canonical(arguments or {})[:_MAX_ARGS_CHARS],
            "output": str(output or "")[:_MAX_OUTPUT_CHARS],
            "at": time.time(),
        }
        return await self._store(_EXECUTED, fingerprint, entry)

    def executed_entries(self) -> list[dict[str, Any]]:
        """Executed calls known to this run (context mirror), oldest first."""
        return [e for e in self._section(_EXECUTED).values() if isinstance(e, dict)]

    # ── approval decisions ─────────────────────────────────────────────────

    async def approval(self, key: str) -> dict[str, Any] | None:
        entry = await self._lookup(_APPROVED, key)
        if entry is None or entry.get("status") != "approved":
            return None
        return entry

    async def record_approval(self, key: str, *, request_id: str, action: str) -> bool:
        entry = {
            "status": "approved",
            "request_id": str(request_id or ""),
            "action": str(action or "")[:_MAX_ARGS_CHARS],
            "at": time.time(),
        }
        return await self._store(_APPROVED, key, entry)


def replay_output(entry: dict[str, Any]) -> str:
    """Step output for a call that already ran earlier in this goal."""
    tool = entry.get("tool") or "tool"
    return (
        f"[ALREADY EXECUTED earlier in this goal — '{tool}' was not run again; "
        f"this is its recorded result] {entry.get('output', '')}"
    )


def executed_calls_planner_block(context: dict[str, Any]) -> str:
    """Planner context listing side-effecting calls this goal already ran."""
    ledger = context.get(LEDGER_CONTEXT_KEY)
    if not isinstance(ledger, dict):
        return ""
    executed = ledger.get(_EXECUTED)
    if not isinstance(executed, dict) or not executed:
        return ""
    lines = [
        f"- {e.get('tool')} {e.get('arguments', '')} -> {str(e.get('output', ''))[:300]}"
        for e in list(executed.values())[-20:]
        if isinstance(e, dict)
    ]
    if not lines:
        return ""
    return (
        "[Side-effecting tool calls ALREADY EXECUTED in this goal (approved where "
        "required). Do NOT plan them again; build on their recorded results.]\n" + "\n".join(lines)
    )
