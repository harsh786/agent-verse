"""Shadow routing — evaluate a candidate model on sampled real traffic (PROV-24).

Behind a flag (``AGENTVERSE_SHADOW_MODEL`` = the candidate model id, served by
the same provider; ``AGENTVERSE_SHADOW_SAMPLE_RATE`` = 0.0-1.0, default 0), a
sampled :func:`app.providers.guarded_completion.complete_decision` call is
repeated against the candidate in the background. Only the primary response is
ever returned; the shadow:

* runs through ``complete_decision`` itself — circuit breaker, timeout, GenAI
  span and provider-health feed — as an *uncharged platform job* (system job
  ``shadow_eval``): the platform's evaluation is never billed to the tenant;
* is metered: its cost goes to the ``llm`` cost metric and into the log entry;
* is logged to a cross-replica shadow log (the Redis ModelRegistryStore when
  wired, else per process) read by ``GET /models/shadow-log`` (platform admin).

The previous ``ShadowRouter`` called providers directly (no metering, no
tracing, failures swallowed) and was wired nowhere.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import os
import random
import time
from typing import Any

_log = logging.getLogger(__name__)
_LOCAL_LOG: collections.deque[dict[str, Any]] = collections.deque(maxlen=200)
_TASKS: set[asyncio.Task[None]] = set()
_SHADOW_TIMEOUT_S = 60.0


def shadow_config() -> tuple[str, float]:
    """(candidate model, sample rate) from the environment; rate clamped to [0, 1]."""
    model = (os.getenv("AGENTVERSE_SHADOW_MODEL") or "").strip()
    try:
        rate = float(os.getenv("AGENTVERSE_SHADOW_SAMPLE_RATE", "0") or 0)
    except ValueError:
        rate = 0.0
    return model, min(1.0, max(0.0, rate))


def maybe_fire_shadow(
    provider: Any, request: Any, *, role: str, primary: Any, primary_latency_ms: float
) -> None:
    """Schedule a shadow of this call when the flag is on and the sample hits."""
    model, rate = shadow_config()
    if not model or rate <= 0.0 or role.startswith("shadow"):
        return
    if (getattr(request, "model", "") or "") == model or random.random() >= rate:
        return
    try:
        task = asyncio.get_running_loop().create_task(
            _run_shadow(provider, request, model, role, primary, primary_latency_ms)
        )
    except RuntimeError:  # no running loop
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def drain_shadow_tasks() -> None:
    """Wait for in-flight shadow calls (tests / graceful shutdown)."""
    while _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)


async def _run_shadow(
    provider: Any,
    request: Any,
    model: str,
    role: str,
    primary: Any,
    primary_latency_ms: float,
) -> None:
    from app.intelligence.cost_tracker import calculate_cost
    from app.observability.metrics import record_cost_usd
    from app.providers.guarded_completion import complete_decision, uncharged_platform_call

    entry: dict[str, Any] = {
        "fired_at": time.time(),
        "role": role,
        "primary_model": str(getattr(primary, "model", "") or getattr(request, "model", "")),
        "shadow_model": model,
        "latency_primary_ms": round(primary_latency_ms, 1),
        "latency_shadow_ms": None,
        "shadow_cost_usd": 0.0,
        "primary_content": str(getattr(primary, "content", "") or "")[:200],
        "shadow_content": None,
        "error": None,
    }
    started = time.monotonic()
    try:
        with uncharged_platform_call("shadow_eval"):
            resp = await complete_decision(
                provider,
                dataclasses.replace(request, model=model),
                role=f"shadow_{role}",
                timeout_seconds=_SHADOW_TIMEOUT_S,
            )
        cost = float(
            calculate_cost(
                str(getattr(resp, "model", "") or model),
                int(getattr(resp, "input_tokens", 0) or 0),
                int(getattr(resp, "output_tokens", 0) or 0),
            )
        )
        record_cost_usd("llm", cost)
        entry.update(
            latency_shadow_ms=round((time.monotonic() - started) * 1000, 1),
            shadow_cost_usd=cost,
            shadow_content=str(getattr(resp, "content", "") or "")[:200],
        )
    except Exception as exc:  # the shadow never affects the primary
        entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
    _record(entry)


def _record(entry: dict[str, Any]) -> None:
    from app.ai_router.registry_store import get_model_registry_store

    store = get_model_registry_store()
    if store is not None:
        try:
            store.push_shadow_result(entry)
            return
        except Exception as exc:
            _log.warning("shadow_log_store_write_failed: %s", str(exc)[:160])
    _LOCAL_LOG.append(entry)


def shadow_log(limit: int = 50) -> list[dict[str, Any]]:
    """Most recent shadow results, newest last."""
    from app.ai_router.registry_store import get_model_registry_store

    store = get_model_registry_store()
    if store is not None:
        try:
            return list(store.list_shadow_results())[-limit:]
        except Exception as exc:
            _log.warning("shadow_log_store_read_failed: %s", str(exc)[:160])
    return list(_LOCAL_LOG)[-limit:]
