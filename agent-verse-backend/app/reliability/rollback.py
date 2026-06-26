"""LIFO rollback engine with typed inverse operations.

Each executed action registers its inverse (e.g. create_branch → delete_branch).
On failure, rollback_all() executes all inverses in LIFO order so later-registered
actions are undone first, which is correct when later actions depend on earlier state.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


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
        self._stack: list[tuple[str, Callable[[], None]]] = []

    def register(self, *, action: str, inverse: Callable[[], None]) -> None:
        """Register an action with its inverse function."""
        self._stack.append((action, inverse))
        logger.debug(
            "Registered rollback point for: %s (stack depth: %d)", action, len(self._stack)
        )

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

        Returns list of rolled-back action names. Errors are logged but do not
        abort the remaining rollback sequence.
        """
        rolled_back: list[str] = []
        while self._stack:
            action, inverse = self._stack.pop()
            try:
                inverse()
                rolled_back.append(action)
                logger.info("Rolled back: %s", action)
            except Exception as exc:
                logger.error("Rollback failed for '%s': %s", action, exc)
        return rolled_back

    def preview(self) -> list[str]:
        """Return list of registered actions without executing rollback (LIFO order)."""
        return [action for action, _ in reversed(self._stack)]

    def __len__(self) -> int:
        return len(self._stack)
