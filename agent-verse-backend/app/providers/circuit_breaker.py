"""Circuit breaker for LLM provider calls."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.providers.rate_limit import is_rate_limit_error, with_rate_limit_retry
from app.providers.shared_circuit import (
    shared_admit,
    shared_is_open,
    shared_record_failure,
    shared_record_success,
    shared_release_probe,
)

logger = logging.getLogger(__name__)


class ProviderCircuitOpenError(RuntimeError):
    """The provider's circuit is open (too many recent failures): fail fast."""


class ProviderCircuitBreaker:
    """Per-provider circuit breaker to prevent cascading LLM failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        self._failures: dict[str, int] = defaultdict(int)
        self._last_failure: dict[str, float] = {}
        self._state: dict[str, str] = {}  # "closed" | "open" | "half-open"
        self._half_open_calls: dict[str, int] = defaultdict(int)
        # When the in-flight half-open probe started: a probe whose outcome never
        # arrives frees its slot after ``recovery_timeout`` (PROV-28).
        self._probe_started: dict[str, float] = {}

    def is_open(self, provider_name: str) -> bool:
        """Return True if the circuit is open (provider unavailable)."""
        state = self._state.get(provider_name, "closed")
        if state == "closed":
            return False
        if state == "open":
            elapsed = time.monotonic() - self._last_failure.get(provider_name, 0)
            if elapsed >= self._recovery_timeout:
                self._state[provider_name] = "half-open"
                self._half_open_calls[provider_name] = 0
                return False
            return True
        if state == "half-open":
            if self._half_open_calls[provider_name] < self._half_open_max:
                return False
            started = self._probe_started.get(provider_name, 0.0)
            if time.monotonic() - started >= self._recovery_timeout:
                # The probe never reported (lost task): admit a new one.
                self._half_open_calls[provider_name] = 0
                return False
            return True
        return False

    def record_success(self, provider_name: str) -> None:
        """Reset failure count and close the circuit."""
        self._failures[provider_name] = 0
        self._state[provider_name] = "closed"
        self._half_open_calls.pop(provider_name, None)
        self._probe_started.pop(provider_name, None)

    def record_failure(self, provider_name: str) -> None:
        """Increment failure count; open the circuit when threshold is reached."""
        self._failures[provider_name] += 1
        self._last_failure[provider_name] = time.monotonic()
        state = self._state.get(provider_name, "closed")
        if state == "half-open" or self._failures[provider_name] >= self._failure_threshold:
            self._state[provider_name] = "open"

    def before_call(self, provider_name: str) -> None:
        """Track half-open probe calls."""
        if self._state.get(provider_name) == "half-open":
            self._half_open_calls[provider_name] += 1
            self._probe_started[provider_name] = time.monotonic()

    def release_probe(self, provider_name: str) -> None:
        """A half-open probe ended with no provider verdict (cancelled, or a
        caller-side refusal): free its slot so the next call can probe.
        """
        if self._state.get(provider_name) == "half-open" and self._half_open_calls[provider_name]:
            self._half_open_calls[provider_name] -= 1


# Module-level singleton shared across all graph instances of this process. The
# fleet-wide view (every replica and worker) lives in Redis: see
# app.providers.shared_circuit (a01-F023-02).
_provider_cb = ProviderCircuitBreaker()


@dataclass(slots=True)
class CircuitAdmission:
    """One admitted call: report exactly one outcome for it."""

    key: str
    # Held when this call is the fleet-wide half-open probe.
    probe_token: str | None = None


async def circuit_open_anywhere(provider_name: str) -> bool:
    """True when this process's circuit or the fleet's is open for ``provider_name``.

    A cheap pre-check (it takes no probe slot): callers that skip to the next
    model use it; the call itself is admitted with :func:`admit_call`.
    """
    if _provider_cb.is_open(provider_name):
        return True
    return await shared_is_open(provider_name, recovery_timeout=_provider_cb._recovery_timeout)


async def admit_call(provider_name: str) -> CircuitAdmission:
    """Admit one provider call or raise :class:`ProviderCircuitOpenError`.

    Refused when this process's circuit is open, or when the fleet's is (failures
    reported by any replica/worker), or while another process holds the
    fleet-wide half-open probe.
    """
    if _provider_cb.is_open(provider_name):
        raise ProviderCircuitOpenError(
            f"LLM provider circuit open for {provider_name}. Too many recent failures."
        )
    verdict = await shared_admit(provider_name, recovery_timeout=_provider_cb._recovery_timeout)
    if verdict.open:
        raise ProviderCircuitOpenError(
            f"LLM provider circuit open for {provider_name} (fleet-wide). "
            "Too many recent failures."
        )
    _provider_cb.before_call(provider_name)
    return CircuitAdmission(provider_name, verdict.probe_token)


async def report_success(admission: CircuitAdmission) -> None:
    _provider_cb.record_success(admission.key)
    await shared_record_success(admission.key, probe_token=admission.probe_token)


async def report_failure(admission: CircuitAdmission) -> None:
    _provider_cb.record_failure(admission.key)
    await shared_record_failure(
        admission.key,
        failure_threshold=_provider_cb._failure_threshold,
        recovery_timeout=_provider_cb._recovery_timeout,
        probe_token=admission.probe_token,
    )


async def report_no_verdict(admission: CircuitAdmission) -> None:
    """Cancelled, or a caller-side refusal: free the probe slot (local and fleet)."""
    _provider_cb.release_probe(admission.key)
    with contextlib.suppress(BaseException):
        await shared_release_probe(admission.key, probe_token=admission.probe_token)


async def call_with_circuit_breaker(
    provider: Any,
    method_name: str,
    *args: Any,
    provider_name: str = "llm",
    timeout_seconds: float | None = None,
    **kwargs: Any,
) -> Any:
    """Wrap a provider call with circuit breaker protection.

    Raises ``ProviderCircuitOpenError`` (a ``RuntimeError``) when the circuit is
    open — in this process or anywhere in the fleet — so callers fail fast
    without hitting a broken downstream provider.
    """
    admission = await admit_call(provider_name)
    recorded = False
    try:
        timeout = timeout_seconds
        if timeout is None:
            timeout = float(os.getenv("AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS", "60"))
        result = await asyncio.wait_for(
            getattr(provider, method_name)(*args, **kwargs),
            timeout=timeout,
        )
        recorded = True
        await report_success(admission)
        return result
    except TimeoutError as exc:
        if not recorded:
            recorded = True
            await report_failure(admission)
        raise TimeoutError(
            f"LLM provider call timed out for {provider_name} after {timeout}s"
        ) from exc
    except Exception as exc:
        # A caller-side refusal (a budget denial raised by a metering wrapper) is
        # not a provider failure and must not open the circuit for everyone. Nor is
        # throttling (HTTP 429): the provider is healthy and asked us to slow down;
        # counting it opened the circuit for every caller for 60 s (P5-1).
        if (
            not recorded
            and getattr(exc, "provider_failure", True)
            and not is_rate_limit_error(exc)
        ):
            recorded = True
            await report_failure(admission)
        raise
    finally:
        # CancelledError (a BaseException) or a non-provider refusal: no verdict.
        # Without this the single half-open slot stayed used and the circuit
        # reported open until the process restarted (and, fleet-wide, until the
        # probe key expired).
        if not recorded:
            await report_no_verdict(admission)


def _provider_identity(provider: Any) -> str:
    """The vendor behind ``provider`` ("openai", "nvidia_nim", ...), or "".

    The same model name served by two vendors (or two endpoints) has independent
    health, so the circuit is keyed per provider + model (P5-1).
    """
    for attr in ("_system", "provider_name"):
        try:
            val = getattr(provider, attr, None)
        except Exception:
            val = None
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def breaker_key(provider: Any, request: Any = None) -> str:
    """Circuit identity for one model/endpoint.

    Callers used to pass ``type(provider).__name__`` — always "TracedProvider"
    for the agent roles — so a few timeouts on ONE slow model opened the circuit
    for every model, every role and every tenant in the process.

    Scope: a tenant's BYOK provider (``_circuit_scope`` set by
    ``app.providers.tenant_provider``) gets a tenant-keyed circuit. Keying only by
    model let one tenant's revoked/over-quota key open the circuit for that model
    for every tenant in the process. The platform provider stays unscoped on
    purpose: it is shared infrastructure, so its failures ARE everyone's failures.
    """
    model = (getattr(request, "model", "") or getattr(provider, "_default_model", "") or "").strip()
    vendor = _provider_identity(provider)
    if model:
        key = f"llm:{vendor}/{model}" if vendor else f"llm:{model}"
    else:
        key = f"llm:{vendor or type(provider).__name__}"
    scope = getattr(provider, "_circuit_scope", None)
    return f"{scope}:{key}" if isinstance(scope, str) and scope else key


def annotate_served_model(resp: Any, model: str, fallback_from: list[str]) -> Any:
    """Stamp *resp* with the model that actually served it (provenance).

    After a failover the request's original ``model`` is NOT the one that
    answered: callers used to attribute the call (role breakdown, traces) to
    the requested model — a dead preferred model was recorded as having served
    the goal. ``fallback_from`` lists the models tried before it, in order
    (empty when the first model answered). A response that already names its
    serving model (most providers echo it) keeps that name.
    """
    with contextlib.suppress(Exception):
        if model and not str(getattr(resp, "model", "") or "").strip():
            resp.model = model
    with contextlib.suppress(Exception):
        resp.fallback_from = list(fallback_from)
    return resp


def fallback_from_of(resp: Any) -> list[str]:
    """The models a response's call failed over from (``[]`` when none / unknown)."""
    raw = getattr(resp, "fallback_from", None)
    if not isinstance(raw, list | tuple):
        return []
    return [str(m) for m in raw if isinstance(m, str) and m]


async def complete_with_failover(
    provider: Any,
    request: Any,
    *,
    fallback_models: Any = (),
    timeout_seconds: float | None = None,
) -> Any:
    """``provider.complete(request)`` with per-model circuits and ordered failover.

    Tries ``request.model`` first, then each distinct entry of
    ``fallback_models`` (the provider routes by ``request.model``, e.g. the
    on-prem dispatcher sends each model to its own endpoint). A slow or broken
    model — a hosted reasoning model that exceeds the call timeout, an endpoint
    that is down, an empty completion — no longer fails the goal while another
    configured model is healthy. The last error is re-raised unchanged, so
    callers keep their existing error handling.

    Throttling (HTTP 429) is retried on the same model with backoff + jitter,
    honouring Retry-After and the goal's time budget (P5-1); it never counts
    against the circuit. A model still throttled after that fails over like any
    other error; if every model was throttled the caller gets
    :class:`ProviderRateLimitedError` (a ``RuntimeError``).
    """
    import dataclasses

    models = [getattr(request, "model", "") or ""]
    for m in fallback_models or ():
        if m and m not in models:
            models.append(m)
    last_exc: BaseException | None = None
    for i, model in enumerate(models):
        req = request if i == 0 else dataclasses.replace(request, model=model)
        key = breaker_key(provider, req)

        async def _attempt(_req: Any = req, _key: str = key) -> Any:
            return await call_with_circuit_breaker(
                provider,
                "complete",
                _req,
                provider_name=_key,
                timeout_seconds=timeout_seconds,
            )

        try:
            resp = await with_rate_limit_retry(_attempt, label=key)
            return annotate_served_model(resp, model, [m for m in models[:i] if m])
        except Exception as exc:
            last_exc = exc
            if i + 1 < len(models):
                logger.warning(
                    "llm_model_failover from=%s to=%s error=%s",
                    model, models[i + 1], str(exc)[:200],
                )
    if last_exc is None:  # pragma: no cover - models always has one entry
        raise RuntimeError("no model to call")
    raise last_exc
