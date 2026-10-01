"""Tool-result outcomes the verifier cannot overrule.

The verifier is an LLM grading a summary; it may say "success" for a step whose
tool call failed, was denied by policy/grant/permission, or errored. A failed
tool result is a fact, not an opinion: it makes the step failed/unverified
whatever the verdict. The verifier can only DOWNGRADE a successful tool result,
never UPGRADE a failed one.

``ToolOutcomeLedger`` records the outcome of every tool call of one execute
pass from the events the executor emits (one place every dispatch path already
reports through). A tool whose LAST call in the pass failed is an unresolved
failure; a later successful call of the same tool in the same pass resolves it
(an in-step retry that worked).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Events that report a tool call that did not produce a usable result.
FAILED_TOOL_EVENT_TYPES = frozenset(
    {
        "tool_call_failed",
        "tool_call_blocked_by_policy",
        "tool_call_blocked_by_grant",
        "tool_call_blocked_by_agent_permission",
    }
)
COMPLETED_TOOL_EVENT_TYPE = "tool_call_complete"


class ToolResultFailedError(RuntimeError):
    """A tool call returned a failed result (it did not raise)."""

    def __init__(self, tool: str) -> None:
        super().__init__(f"tool '{tool}' returned a failed result")
        self.tool = tool


def is_failed_tool_result(result: Any) -> bool:
    """True when a dispatcher/MCP result reports failure rather than raising.

    Handles ``ToolCallResult``-like objects (``success=False``) and plain dicts
    (``{"success": False}`` or an MCP ``{"isError": true}`` payload).
    """
    if isinstance(result, Mapping):
        return result.get("success") is False or result.get("isError") is True
    return getattr(result, "success", True) is False


def tool_event_outcome(event: Mapping[str, Any]) -> tuple[str, bool, str] | None:
    """``(tool, succeeded, error)`` for a tool-result event, ``None`` otherwise."""
    kind = event.get("type")
    tool = str(event.get("tool") or "?")
    if kind in FAILED_TOOL_EVENT_TYPES:
        return tool, False, str(event.get("error") or event.get("reason") or kind)
    if kind == COMPLETED_TOOL_EVENT_TYPE:
        ok = event.get("success") is not False
        return tool, ok, "" if ok else str(event.get("error") or "tool call failed")
    return None


class ToolOutcomeLedger:
    """Last outcome per tool for one execute pass."""

    def __init__(self) -> None:
        self._last: dict[str, tuple[bool, str]] = {}

    def reset(self) -> None:
        self._last.clear()

    def record(self, event: Mapping[str, Any]) -> None:
        outcome = tool_event_outcome(event)
        if outcome is not None:
            tool, ok, error = outcome
            self._last[tool] = (ok, error)

    def unresolved_failures(self) -> list[tuple[str, str]]:
        """``(tool, error)`` for every tool whose last call in the pass failed."""
        return [(tool, error) for tool, (ok, error) in self._last.items() if not ok]


def ledger_for(owner: Any) -> ToolOutcomeLedger:
    """The ledger of a graph run, created on first use (partially built graphs)."""
    ledger = getattr(owner, "_tool_outcomes", None)
    if not isinstance(ledger, ToolOutcomeLedger):
        ledger = ToolOutcomeLedger()
        owner._tool_outcomes = ledger
    return ledger


__all__ = [
    "COMPLETED_TOOL_EVENT_TYPE",
    "FAILED_TOOL_EVENT_TYPES",
    "ToolOutcomeLedger",
    "ToolResultFailedError",
    "is_failed_tool_result",
    "ledger_for",
    "tool_event_outcome",
]
