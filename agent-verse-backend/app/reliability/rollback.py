"""LIFO rollback engine with typed inverse operations.

Each executed action registers its inverse (e.g. create_branch → delete_branch).
On failure, rollback_all() executes all inverses in LIFO order so later-registered
actions are undone first, which is correct when later actions depend on earlier state.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RollbackReport:
    """Honest outcome of a rollback: what was undone, skipped, or failed.

    ``rollback_all_async`` used to return every registered action as "rolled
    back" even when the inverse skipped (no id to act on) or errored.
    """

    rolled_back: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def record(self, action: str, result: Any) -> None:
        """Classify one inverse's return value (``InverseResult`` or legacy None)."""
        from app.reliability.tool_inverses import FAILED, SKIPPED, InverseResult

        if isinstance(result, InverseResult):
            if result.outcome == SKIPPED:
                self.skipped.append({"action": action, "detail": result.detail})
                return
            if result.outcome == FAILED:
                self.failed.append({"action": action, "detail": result.detail})
                return
        self.rolled_back.append(action)

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": {
                "rolled_back": len(self.rolled_back),
                "skipped": len(self.skipped),
                "failed": len(self.failed),
            },
            "rolled_back": list(self.rolled_back),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
        }


class RollbackAction(enum.StrEnum):
    """Common reversible action types."""

    CREATE_FILE = "create_file"
    DELETE_FILE = "delete_file"
    MODIFY_FILE = "modify_file"
    CREATE_BRANCH = "create_branch"
    DELETE_BRANCH = "delete_branch"
    CREATE_PR = "create_pr"
    CLOSE_PR = "close_pr"
    CREATE_TICKET = "create_ticket"
    CLOSE_TICKET = "close_ticket"
    SEND_MESSAGE = "send_message"  # Generally not reversible
    CUSTOM = "custom"


class RollbackEngine:
    """Collects reversible action registrations and executes them in LIFO order."""

    def __init__(self) -> None:
        self._stack: list[tuple[str, Callable[[], Any]]] = []
        # Outcome of the most recent rollback_all_async() call.
        self.last_report: RollbackReport | None = None

    def register(self, *, action: str, inverse: Callable[[], Any]) -> None:
        """Register an action with its inverse function."""
        self._stack.append((action, inverse))
        logger.debug(
            "Registered rollback point for: %s (stack depth: %d)", action, len(self._stack)
        )

    def register_tool_call(
        self,
        *,
        action: str,
        tool_names: list[str],
        arguments: dict[str, Any],
        output: Any,
        server_id: str,
        tenant_ctx: Any,
        mcp_client: Any = None,
    ) -> None:
        """Register an executed tool call for undo.

        Captures the tool's OUTPUT (which carries the ids of created objects —
        the input args never do) and the goal's real tenant context, so the
        inverse can actually find and delete what the forward call created.
        """
        from app.reliability.tool_inverses import run_inverse

        _names = [n for n in tool_names if n]
        _args = dict(arguments)

        async def _undo() -> Any:
            return await run_inverse(
                _names,
                arguments=_args,
                output=output,
                server_id=server_id,
                tenant_ctx=tenant_ctx,
                mcp_client=mcp_client,
            )

        self._stack.append((action, _undo))

    def register_typed(
        self,
        *,
        action_type: RollbackAction,
        action_description: str,
        inverse_fn: Callable[[], None] | None = None,
    ) -> None:
        """Register a typed rollback action with an optional real inverse."""
        if inverse_fn is None:
            _type_val = action_type.value
            _desc_val = action_description

            def _noop_inverse() -> None:
                logger.warning(
                    "Rollback called for '%s' (%s) but no inverse function provided.",
                    _desc_val,
                    _type_val,
                )

            inverse_fn = _noop_inverse

        self._stack.append((f"{action_type.value}:{action_description}", inverse_fn))

    def rollback_all(self) -> list[str]:
        """Execute all inverse operations in LIFO order.

        In async contexts, prefer ``rollback_all_async()`` to ensure completion.
        This method schedules async inverses as tasks (fire-and-forget) and logs
        a warning — use ``rollback_all_async()`` when guaranteed completion matters.

        Returns list of rolled-back action names. Errors are logged but do not
        abort the remaining rollback sequence.
        """
        import asyncio

        rolled_back: list[str] = []
        while self._stack:
            action, inverse = self._stack.pop()
            try:
                result = inverse()
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)  # noqa: RUF006  # intentional fire-and-forget
                        logger.warning(
                            "rollback_fire_and_forget action=%s "
                            "use_rollback_all_async_for_guaranteed_completion",
                            action,
                        )
                    except RuntimeError:
                        # No running loop — cannot schedule; close the coroutine cleanly
                        logger.error("rollback_skipped_no_event_loop action=%s", action)
                        result.close()  # Prevent "coroutine never awaited" warning
                rolled_back.append(action)
                logger.info("Rolled back: %s", action)
            except Exception as exc:
                logger.error("Rollback failed for '%s': %s", action, exc)
        return rolled_back

    async def rollback_all_async(
        self,
        executed_tool_calls: list | None = None,
        tenant_ctx: object | None = None,
    ) -> list[str]:
        """Execute all inverse operations in LIFO order, awaiting each one.

        Two modes:

        **Tool-call mode** (``executed_tool_calls`` provided):
            Iterates over the supplied list in reverse order.  For each entry
            it looks up the inverse function by ``tool_call.tool_name`` via
            the :mod:`app.reliability.tool_inverses` registry, then *awaits*
            it with ``(tool_call=tool_call, mcp_client=_mcp_client)``.  This
            guarantees every MCP rollback call actually completes — no
            fire-and-forget tasks.

        **Stack mode** (``executed_tool_calls`` is ``None``, default):
            Falls back to the internal ``_stack`` accumulated via
            :meth:`register` / :meth:`register_typed`.  Coroutine inverses are
            awaited; sync inverses are called directly.

        Returns the names of actions that were ACTUALLY undone. Inverses that
        skipped (nothing identifiable to undo) or failed are excluded; the full
        breakdown is left on :attr:`last_report`. Errors are logged but do not
        abort the remaining rollback sequence.
        """
        import asyncio

        report = RollbackReport()
        self.last_report = report

        # ── Tool-call mode: use tool_inverses registry, fully awaited ───────
        if executed_tool_calls is not None:
            from app.reliability.tool_inverses import _mcp_client, get_inverse_fn

            for tool_call in reversed(list(executed_tool_calls)):
                tool_name = getattr(tool_call, "tool_name", "")
                inverse = get_inverse_fn(tool_name)
                if inverse is None:
                    logger.info("rollback_no_inverse tool=%s", tool_name)
                    report.skipped.append({"action": tool_name, "detail": "no inverse"})
                    continue
                try:
                    result = await inverse(tool_call=tool_call, mcp_client=_mcp_client)
                    report.record(tool_name, result)
                    logger.info("Rolled back async: %s", tool_name)
                except Exception as exc:
                    report.failed.append({"action": tool_name, "detail": str(exc)[:200]})
                    logger.warning(
                        "rollback_inverse_error tool=%s error=%s",
                        tool_name,
                        str(exc)[:80],
                    )
            return list(report.rolled_back)

        # ── Stack mode: legacy _stack path ──────────────────────────────────
        while self._stack:
            action, inverse = self._stack.pop()
            try:
                result = inverse()
                if asyncio.iscoroutine(result):
                    result = await result
                report.record(action, result)
                logger.info("Rolled back async: %s", action)
            except Exception as exc:
                report.failed.append({"action": action, "detail": str(exc)[:200]})
                logger.error("Async rollback failed for '%s': %s", action, exc)
        return list(report.rolled_back)

    def preview(self) -> list[str]:
        """Return list of registered actions without executing rollback (LIFO order)."""
        return [action for action, _ in reversed(self._stack)]

    def __len__(self) -> int:
        return len(self._stack)
