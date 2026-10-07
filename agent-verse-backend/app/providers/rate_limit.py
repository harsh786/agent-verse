"""LLM provider throttling (HTTP 429) — retry with backoff, never a breaker failure.

A 429 says "slow down", not "this provider is broken". It used to escape the
planner and the verifier as a raw ``openai.RateLimitError`` (the vendor SDK
retries only twice, under a second apart) and to count as a circuit-breaker
failure, so a handful of throttled calls opened a process-wide circuit and
refused every LLM caller for 60 s (P0 baseline §4.5, §4.13).

Now a throttled call is retried here with exponential backoff and full jitter,
honouring the provider's ``Retry-After`` / ``retry-after-ms``. The total wait is
bounded by ``llm_rate_limit_max_total_wait_seconds`` and by the goal's remaining
time budget (:func:`llm_deadline`): when the provider asks for longer than the
goal has left, the call fails at once with :class:`ProviderRateLimitedError`
instead of sleeping past the goal timeout. Exhaustion is an honest typed error,
never a fake answer.
"""

from __future__ import annotations

import asyncio
import contextvars
import email.utils
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Indirection so tests can observe/skip the waits.
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
_rng = random.Random()


class ProviderRateLimitedError(RuntimeError):
    """The provider kept throttling (HTTP 429) past the retry budget.

    A ``RuntimeError`` so the planner / verifier / executor fail the step with
    their existing "unavailable" handling. ``provider_failure = False``: being
    throttled is not a provider fault and must never open a circuit.
    """

    provider_failure = False

    def __init__(
        self, message: str, *, retry_after: float | None = None, attempts: int = 0
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.attempts = attempts


@dataclass(frozen=True)
class RateLimitPolicy:
    max_retries: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_total_wait: float = 120.0


def current_policy() -> RateLimitPolicy:
    try:
        from app.core.config import get_settings

        s = get_settings()
        return RateLimitPolicy(
            max_retries=int(s.llm_rate_limit_max_retries),
            base_delay=float(s.llm_rate_limit_base_delay_seconds),
            max_delay=float(s.llm_rate_limit_max_delay_seconds),
            max_total_wait=float(s.llm_rate_limit_max_total_wait_seconds),
        )
    except Exception:  # settings unreadable: safe defaults
        return RateLimitPolicy()


# ---------------------------------------------------------------- goal deadline

_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "llm_rate_limit_deadline", default=None
)


# The goal's ActiveTimeBudget (app.reliability.active_budget), when the run has one.
_ACTIVE_BUDGET: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "agentverse_llm_active_budget", default=None
)

@asynccontextmanager
async def llm_deadline(seconds: float) -> AsyncIterator[None]:
    """Bound every rate-limit wait in this context to ``seconds`` from now.

    Nested deadlines keep the earlier one (a step can only tighten the goal's).
    """
    new = time.monotonic() + max(0.0, float(seconds))
    prev = _DEADLINE.get()
    token = _DEADLINE.set(new if prev is None else min(prev, new))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


async def run_with_llm_deadline[T](coro: Coroutine[Any, Any, T], seconds: float) -> T:
    """Await ``coro`` with :func:`llm_deadline` set (for ``wait_for`` wrappers)."""
    async with llm_deadline(seconds):
        return await coro


async def run_with_llm_budget[T](coro: Coroutine[Any, Any, T], budget: Any) -> T:
    """Await ``coro`` with rate-limit waits bounded by an ``ActiveTimeBudget``
    (a goal budget that stops while the goal is paused, a08-F193-04)."""
    token = _ACTIVE_BUDGET.set(budget)
    try:
        return await coro
    finally:
        _ACTIVE_BUDGET.reset(token)


def remaining_budget() -> float | None:
    deadline = _DEADLINE.get()
    budget = _ACTIVE_BUDGET.get()
    candidates: list[float] = []
    if deadline is not None:
        candidates.append(deadline - time.monotonic())
    if budget is not None:
        candidates.append(float(budget.remaining()))
    if not candidates:
        return None
    return max(0.0, min(candidates))


# ---------------------------------------------------------------- classification


def is_rate_limit_error(exc: BaseException) -> bool:
    """True for a provider throttling response (HTTP 429), across vendor SDKs."""
    if isinstance(exc, ProviderRateLimitedError):
        return True
    for attr in ("status_code", "code", "status"):
        val = getattr(exc, attr, None)
        if val == 429 or val == "429":
            return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    name = type(exc).__name__
    return name in {"RateLimitError", "TooManyRequests", "ResourceExhausted"}


def _parse_retry_after(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - time.time())


def retry_after_seconds(exc: BaseException) -> float | None:
    """The provider's requested wait, from ``retry-after-ms`` / ``Retry-After``."""
    direct = getattr(exc, "retry_after", None)
    if isinstance(direct, (int, float)) and not isinstance(direct, bool):
        return max(0.0, float(direct))
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        ms = headers.get("retry-after-ms")
        if ms:
            return max(0.0, float(ms) / 1000.0)
    except (TypeError, ValueError):
        pass
    try:
        raw = headers.get("retry-after")
    except Exception:
        return None
    return _parse_retry_after(str(raw)) if raw else None


def backoff_delay(attempt: int, policy: RateLimitPolicy, retry_after: float | None) -> float:
    """Delay before retry ``attempt`` (0-based).

    Without a hint: full jitter over ``base * 2**attempt`` capped at ``max_delay``.
    With ``Retry-After``: at least that long, plus a little jitter so replicas
    throttled together do not retry in lockstep.
    """
    if retry_after is not None:
        return retry_after + _rng.uniform(0.0, min(1.0, 0.1 * retry_after + 0.1))
    ceiling = min(policy.max_delay, policy.base_delay * (2**attempt))
    return _rng.uniform(ceiling / 2.0, ceiling)


async def with_rate_limit_retry[T](
    call: Callable[[], Awaitable[T]],
    *,
    label: str,
    policy: RateLimitPolicy | None = None,
    should_retry: Callable[[], bool] | None = None,
) -> T:
    """Run ``call()``; on a 429 wait and retry within the policy and goal budget.

    Raises :class:`ProviderRateLimitedError` (chained to the last 429) when the
    retries, the total-wait cap or the goal deadline run out. Any other error
    propagates unchanged on the first occurrence.
    """
    pol = policy or current_policy()
    waited = 0.0
    attempt = 0
    while True:
        try:
            return await call()
        except Exception as exc:
            if not is_rate_limit_error(exc):
                raise
            hint = retry_after_seconds(exc)
            if attempt >= pol.max_retries or (should_retry is not None and not should_retry()):
                raise ProviderRateLimitedError(
                    f"LLM provider rate-limited {label} after {attempt + 1} attempt(s)",
                    retry_after=hint,
                    attempts=attempt + 1,
                ) from exc
            delay = backoff_delay(attempt, pol, hint)
            budget = remaining_budget()
            if waited + delay > pol.max_total_wait or (budget is not None and delay > budget):
                raise ProviderRateLimitedError(
                    f"LLM provider rate-limited {label}; the requested wait of "
                    f"{delay:.1f}s exceeds the remaining time budget",
                    retry_after=hint,
                    attempts=attempt + 1,
                ) from exc
            logger.warning(
                "llm_rate_limited_retrying target=%s attempt=%d delay_s=%.2f retry_after=%s",
                label, attempt + 1, delay, hint,
            )
            await _sleep(delay)
            waited += delay
            attempt += 1
