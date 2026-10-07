"""Retry policy of workflow steps: failure classification and backoff schedule.

A step's ``retry`` block (``max_attempts``, ``backoff``, ``base_delay_ms``,
``max_delay_ms``, ``jitter``) is applied by the compiler's node wrapper. Only a
*retryable* failure is retried: a timeout, a transport error, a 5xx / unavailable
service, a rate limit. A failure that cannot get better by trying again — an
authorization denial, a refused operator, a validation error, any other
4xx-equivalent — is NOT retried: the step stops after that attempt with the
reason ``non_retryable``.

A step that knows why it failed raises :class:`StepAttemptError` carrying the
classification (the tool step does, from the connector's result); any other
exception is classified from its type, then from its message.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Failure kinds. Retryable ones may get better on another attempt.
RETRYABLE_KINDS = frozenset(
    {"timeout", "transport", "unavailable", "rate_limited", "server_error", "error"}
)
NON_RETRYABLE_KINDS = frozenset(
    {
        "unauthorized",
        "forbidden",
        "refused",
        "policy",
        "validation",
        "not_found",
        "client_error",
        "configuration",
    }
)

# Why a failing step stopped trying.
REASON_NON_RETRYABLE = "non_retryable"
REASON_EXHAUSTED = "retries_exhausted"

_MAX_EXPONENT = 30  # 2**30 * base is far past any sane max_delay


@dataclass(frozen=True)
class FailureClass:
    """How a step failure is classified."""

    kind: str
    retryable: bool
    error_id: str | None = None
    status_code: int | None = None


class StepAttemptError(RuntimeError):
    """A step failure the step classified itself (retryable or not)."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        retryable: bool,
        error_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = FailureClass(
            kind=kind,
            retryable=retryable,
            error_id=error_id if error_id is not None else extract_error_id(message),
            status_code=status_code,
        )


# ``... (error id 3b4863b2c150)``, ``error_id=...``, ``Error-ID: ...``
_ERROR_ID_RE = re.compile(r"error[\s_-]?id[\s:=#]+([0-9A-Za-z][0-9A-Za-z_-]{5,63})", re.I)
_HTTP_STATUS_RE = re.compile(r"\b(?:http|status(?:[ _]code)?)[\s:=]*([1-5]\d\d)\b", re.I)
_JSONRPC_RE = re.compile(r"json-rpc error\s*(-?\d+)", re.I)

# Checked in this order: a transport failure's text often also says "refused"
# ("connection refused"), so it is recognised before the refusal markers.
_TIMEOUT_MARKERS = ("timed out", "timeout", "deadline exceeded", "deadline expired")
_UNAVAILABLE_MARKERS = (
    "circuit breaker",
    "service unavailable",
    "temporarily unavailable",
    "unavailable",
    "try again later",
    "overloaded",
)
_RATE_MARKERS = ("too many requests", "rate limit", "rate-limit", "throttl")
_TRANSPORT_MARKERS = (
    "could not reach",
    "could not connect",
    "failed to connect",
    "connection refused",
    "connection reset",
    "connection aborted",
    "connection closed",
    "connection failed",
    "connection error",
    "the connection to",
    "econnrefused",
    "econnreset",
    "broken pipe",
    "network is unreachable",
    "host is unreachable",
    "name or service not known",
    "temporary failure in name resolution",
    "server selection",
    "no primary available",
)
_AUTH_MARKERS = (
    "not authorized",
    "unauthorized",
    "unauthorised",
    "authentication failed",
    "invalid credentials",
    "invalid api key",
    "no credentials",
    "no valid oauth token",
    "could not resolve the credential",
)
_FORBIDDEN_MARKERS = ("forbidden", "permission denied", "access denied", "insufficient privilege")
_REFUSED_MARKERS = (
    "not allowed",
    "refused",
    "blocked",
    "denied",
    "read-only",
    "readonly",
    "write stage",
    "is disabled",
    "kill switch",
)
_NOT_FOUND_MARKERS = ("not found", "does not exist", "does not expose tool", "unknown tool")
_VALIDATION_MARKERS = (
    "invalid",
    "validation",
    "must not be empty",
    "missing required",
    "required property",
    "bad request",
    "malformed",
    "duplicate key",
    "could not be encoded",
    "unprocessable",
)


def extract_error_id(text: Any) -> str | None:
    """The error id a message carries (``... (error id 3b4863b2c150)``), if any."""
    match = _ERROR_ID_RE.search(str(text or ""))
    return match.group(1) if match else None


def _status_class(status: int, error_id: str | None) -> FailureClass:
    if status in (408, 425):
        return FailureClass("timeout", True, error_id, status)
    if status == 429:
        return FailureClass("rate_limited", True, error_id, status)
    if status == 401:
        return FailureClass("unauthorized", False, error_id, status)
    if status == 403:
        return FailureClass("forbidden", False, error_id, status)
    if status in (404, 410):
        return FailureClass("not_found", False, error_id, status)
    if status in (501, 505):  # not implemented / unsupported: retrying cannot help
        return FailureClass("client_error", False, error_id, status)
    if status >= 500:
        return FailureClass("server_error", True, error_id, status)
    if status >= 400:
        kind = "validation" if status in (400, 422) else "client_error"
        return FailureClass(kind, False, error_id, status)
    return FailureClass("error", True, error_id, status)


def _has(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def classify_error_text(message: Any, *, status_code: int | None = None) -> FailureClass:
    """Classify a failure from its message (and an HTTP-like status when known)."""
    raw = str(message or "")
    text = raw.lower()
    error_id = extract_error_id(raw)
    if status_code is not None:
        return _status_class(int(status_code), error_id)
    rpc = _JSONRPC_RE.search(raw)
    if rpc:
        code = int(rpc.group(1))
        if code in (-32600, -32602, -32700):
            return FailureClass("validation", False, error_id)
        if code == -32601:
            return FailureClass("not_found", False, error_id)
        if code == -32603:
            return FailureClass("server_error", True, error_id)
    http = _HTTP_STATUS_RE.search(raw)
    if http:
        return _status_class(int(http.group(1)), error_id)
    if _has(text, _TIMEOUT_MARKERS):
        return FailureClass("timeout", True, error_id)
    if _has(text, _RATE_MARKERS):
        return FailureClass("rate_limited", True, error_id)
    if _has(text, _UNAVAILABLE_MARKERS):
        return FailureClass("unavailable", True, error_id)
    if _has(text, _TRANSPORT_MARKERS):
        return FailureClass("transport", True, error_id)
    if _has(text, _AUTH_MARKERS):
        return FailureClass("unauthorized", False, error_id)
    if _has(text, _FORBIDDEN_MARKERS):
        return FailureClass("forbidden", False, error_id)
    if _has(text, _REFUSED_MARKERS):
        return FailureClass("refused", False, error_id)
    if _has(text, _NOT_FOUND_MARKERS):
        return FailureClass("not_found", False, error_id)
    if _has(text, _VALIDATION_MARKERS):
        return FailureClass("validation", False, error_id)
    # Unknown: retry (the historical behaviour of every step type).
    return FailureClass("error", True, error_id)


def classify_failure(exc: BaseException) -> FailureClass:
    """Classify a step attempt's exception."""
    if isinstance(exc, StepAttemptError):
        return exc.failure
    message = str(exc)
    error_id = extract_error_id(message)
    from app.workflow.guardrails import WorkflowGuardrailBlockedError
    from app.workflow.state import WorkflowConfigurationError

    if isinstance(exc, WorkflowGuardrailBlockedError):
        return FailureClass("policy", False, error_id)  # a verdict (P8b-2)
    if isinstance(exc, WorkflowConfigurationError):
        return FailureClass("configuration", False, error_id)
    if isinstance(exc, PermissionError):  # before OSError: it is one
        return FailureClass("unauthorized", False, error_id)
    if isinstance(exc, TimeoutError):
        return FailureClass("timeout", True, error_id)
    name = type(exc).__name__
    if name == "CircuitBreakerOpenError":
        return FailureClass("unavailable", True, error_id)
    if "SSRF" in name:
        return FailureClass("refused", False, error_id)
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            return _status_class(exc.response.status_code, error_id)
        if isinstance(exc, httpx.TimeoutException):
            return FailureClass("timeout", True, error_id)
        if isinstance(exc, httpx.TransportError):
            return FailureClass("transport", True, error_id)
    except ImportError:  # pragma: no cover - httpx is a dependency
        pass
    if isinstance(exc, ConnectionError | OSError):
        return FailureClass("transport", True, error_id)
    return classify_error_text(message)


def should_retry(retry: Any, exc: BaseException, failure: FailureClass | None = None) -> bool:
    """Whether a failed attempt may be retried.

    ``fail_on`` (exception class names or failure kinds) never retries. A
    non-empty ``retry_on`` retries only what it names; naming a failure KIND
    there is an explicit opt-in that also overrides the non-retryable
    classification. Otherwise the classification decides.
    """
    failure = failure or classify_failure(exc)
    name = type(exc).__name__
    if failure.kind == "policy":
        return False  # a guardrail verdict is never retried (P8b-2)
    fail_on = set(getattr(retry, "fail_on", None) or [])
    if name in fail_on or failure.kind in fail_on:
        return False
    retry_on = set(getattr(retry, "retry_on", None) or [])
    if retry_on:
        if failure.kind in retry_on:
            return True
        return name in retry_on and failure.retryable
    return failure.retryable


def retry_delay(
    retry: Any, attempt: int, *, rand: Callable[[], float] = random.random
) -> float:
    """Seconds to wait after failed attempt ``attempt`` (1-based) before the next.

    ``fixed``: base; ``linear``: base x attempt; ``exponential``: base x 2^(attempt-1);
    capped at ``max_delay_ms`` (0 = no cap). ``jitter``: ``full`` picks uniformly in
    [0, delay], ``equal`` in [delay/2, delay].
    """
    base_ms = max(0, int(getattr(retry, "base_delay_ms", 0) or 0))
    backoff = getattr(retry, "backoff", "exponential")
    n = max(1, int(attempt))
    if backoff == "fixed":
        delay_ms = base_ms
    elif backoff == "linear":
        delay_ms = base_ms * n
    else:
        delay_ms = base_ms * (2 ** min(n - 1, _MAX_EXPONENT))
    cap_ms = int(getattr(retry, "max_delay_ms", 0) or 0)
    if cap_ms > 0:
        delay_ms = min(delay_ms, cap_ms)
    delay = delay_ms / 1000.0
    jitter = getattr(retry, "jitter", "none")
    if jitter == "full":
        delay = rand() * delay
    elif jitter == "equal":
        delay = delay / 2 + rand() * (delay / 2)
    return max(0.0, delay)


def backoff_schedule(retry: Any, *, rand: Callable[[], float] = random.random) -> list[float]:
    """The waits between the declared attempts (``max_attempts - 1`` values)."""
    attempts = max(1, int(getattr(retry, "max_attempts", 1) or 1))
    return [retry_delay(retry, i, rand=rand) for i in range(1, attempts)]


__all__ = [
    "NON_RETRYABLE_KINDS",
    "REASON_EXHAUSTED",
    "REASON_NON_RETRYABLE",
    "RETRYABLE_KINDS",
    "FailureClass",
    "StepAttemptError",
    "backoff_schedule",
    "classify_error_text",
    "classify_failure",
    "extract_error_id",
    "retry_delay",
    "should_retry",
]
