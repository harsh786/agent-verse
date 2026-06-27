"""Typed exceptions for the AgentVerse SDK."""

from __future__ import annotations


class AgentVerseError(Exception):
    """Base exception for all AgentVerse SDK errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, status_code={self.status_code})"


class AuthError(AgentVerseError):
    """Raised when the API key is missing or invalid (HTTP 401/403)."""


class GoalFailedError(AgentVerseError):
    """Raised when `wait_for_goal` resolves to a FAILED status."""

    def __init__(self, goal_id: str, reason: str) -> None:
        super().__init__(f"Goal {goal_id} failed: {reason}")
        self.goal_id = goal_id
        self.reason = reason


class GoalTimeoutError(AgentVerseError):
    """Raised when `wait_for_goal` exceeds the specified timeout."""

    def __init__(self, goal_id: str, timeout: float) -> None:
        super().__init__(f"Goal {goal_id} did not complete within {timeout}s")
        self.goal_id = goal_id
        self.timeout = timeout


class RateLimitError(AgentVerseError):
    """Raised on HTTP 429."""


class NotFoundError(AgentVerseError):
    """Raised on HTTP 404."""
