"""LIFO rollback engine — register inverse operations; undo in reverse order.

Each executed action registers its inverse (e.g. create_branch → delete_branch).
On failure, rollback_all() executes all inverses in LIFO order so later-registered
actions are undone first, which is correct when later actions depend on earlier state.
"""

from __future__ import annotations

from collections.abc import Callable


class RollbackEngine:
    """Collects reversible action registrations and executes them in LIFO order."""

    def __init__(self) -> None:
        self._stack: list[tuple[str, Callable[[], None]]] = []

    def register(self, *, action: str, inverse: Callable[[], None]) -> None:
        self._stack.append((action, inverse))

    def rollback_all(self) -> None:
        while self._stack:
            _action, inverse = self._stack.pop()
            inverse()

    def __len__(self) -> int:
        return len(self._stack)
