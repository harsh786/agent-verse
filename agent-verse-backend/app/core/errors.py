"""Platform error hierarchy and retry policy.

Every error knows its own HTTP status, severity, machine-readable ``code``, a unique
``error_id`` (for correlating an API response with logs/traces), and whether it is
``retryable``. ``retry_with_backoff`` consults the raised error rather than guessing,
so transience is decided at the raise site where the context is known.
"""

from __future__ import annotations

import asyncio
import enum
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Any


class Severity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PlatformError(Exception):
    """Base class for all platform errors."""

    code: str = "PLATFORM_ERROR"
    http_status: int = 500
    retryable: bool = False
    default_severity: Severity = Severity.MEDIUM

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        severity: Severity | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.severity = severity or type(self).default_severity
        self.details = details or {}
        self.error_id = uuid.uuid4().hex
        if cause is not None:
            self.__cause__ = cause

    def to_dict(self) -> dict[str, Any]:
        """Structured, client-safe error envelope (no stack traces, no internals)."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "severity": self.severity.value,
                "error_id": self.error_id,
                "retryable": self.retryable,
                "details": self.details,
            }
        }


# --- client errors (not retryable) ---


class ValidationError(PlatformError):
    code = "VALIDATION_ERROR"
    http_status = 422
    default_severity = Severity.LOW


class AuthenticationError(PlatformError):
    code = "AUTHENTICATION_ERROR"
    http_status = 401
    default_severity = Severity.MEDIUM


class AuthorizationError(PlatformError):
    code = "AUTHORIZATION_ERROR"
    http_status = 403
    default_severity = Severity.MEDIUM


class NotFoundError(PlatformError):
    code = "NOT_FOUND"
    http_status = 404
    default_severity = Severity.LOW


class ConflictError(PlatformError):
    code = "CONFLICT"
    http_status = 409
    default_severity = Severity.LOW


class RateLimitError(PlatformError):
    code = "RATE_LIMITED"
    http_status = 429
    retryable = True
    default_severity = Severity.LOW


class BudgetExceededError(PlatformError):
    code = "BUDGET_EXCEEDED"
    http_status = 402
    default_severity = Severity.HIGH


# --- server / upstream errors (retryable) ---


class ExternalServiceError(PlatformError):
    code = "EXTERNAL_SERVICE_ERROR"
    http_status = 502
    retryable = True
    default_severity = Severity.HIGH


class TimeoutError(PlatformError):  # noqa: A001 — intentional domain-specific shadow
    code = "TIMEOUT"
    http_status = 504
    retryable = True
    default_severity = Severity.MEDIUM


class CircuitOpenError(PlatformError):
    code = "CIRCUIT_OPEN"
    http_status = 503
    retryable = True
    default_severity = Severity.HIGH


class InternalError(PlatformError):
    code = "INTERNAL_ERROR"
    http_status = 500
    default_severity = Severity.CRITICAL


def _is_retryable(exc: BaseException, retry_on: tuple[type[BaseException], ...]) -> bool:
    if isinstance(exc, PlatformError):
        return exc.retryable
    return isinstance(exc, retry_on)


async def retry_with_backoff[T](
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 10.0,
    retry_on: tuple[type[BaseException], ...] = (PlatformError,),
    sleep: Callable[[float], Awaitable[None]] | None = None,
    rng: Callable[[], float] | None = None,
) -> T:
    """Run ``operation``, retrying transient failures with full-jitter exponential backoff.

    A :class:`PlatformError` is retried only when its ``retryable`` flag is set; other
    exception types are retried when they are instances of ``retry_on``. ``sleep`` and
    ``rng`` are injectable for deterministic testing.
    """
    do_sleep = sleep or asyncio.sleep
    jitter = rng or random.random

    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except BaseException as exc:
            last_exc = exc
            is_last = attempt == max_attempts - 1
            if is_last or not _is_retryable(exc, retry_on):
                raise
            capped = min(max_delay, base_delay * (2**attempt))
            await do_sleep(jitter() * capped)

    assert last_exc is not None  # pragma: no cover — loop always raises or returns
    raise last_exc
