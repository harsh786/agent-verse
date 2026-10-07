"""Concurrent request load against the live API and its latency / error summary.

Pure aggregation (:func:`latency_summary`, :func:`error_rate`) is unit-tested
offline; :func:`run_load` drives real HTTP requests from N threads, each with its own
connection pool (a shared pool would queue requests client-side and inflate the
latency it is meant to measure). 429s are counted separately (plan rate limits are a
policy answer, not a failure) and never retried here: retrying would hide latency.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from tests.real_world.helpers import BASE_URL


def latency_summary(values: Sequence[float]) -> dict[str, float]:
    """n / mean / p50 / p95 / p99 / max (nearest-rank percentiles)."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return {"n": 0}

    def pct(p: float) -> float:
        idx = max(0, min(len(vals) - 1, math.ceil(p / 100 * len(vals)) - 1))
        return round(vals[idx], 2)

    return {"n": len(vals), "mean": round(statistics.fmean(vals), 2), "p50": pct(50),
            "p95": pct(95), "p99": pct(99), "max": round(vals[-1], 2)}


def error_rate(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Share of failed requests: 5xx, transport errors and unexpected 4xx (not 429)."""
    n = len(results)
    server = sum(1 for r in results if isinstance(r.get("status"), int) and r["status"] >= 500)
    transport = sum(1 for r in results if not isinstance(r.get("status"), int))
    limited = sum(1 for r in results if r.get("status") == 429)
    client = sum(1 for r in results if isinstance(r.get("status"), int)
                 and 400 <= r["status"] < 500 and r["status"] != 429)
    failed = server + transport + client
    return {"requests": n, "server_errors": server, "transport_errors": transport,
            "client_errors": client, "rate_limited": limited,
            "error_rate": round(failed / n, 4) if n else 0.0}


def run_load(api_key: str, make_request: Callable[[int], tuple[str, str, dict[str, Any]]],
             total: int, concurrency: int, *, timeout: float = 60.0,
             check: Callable[[int, httpx.Response], str | None] | None = None
             ) -> tuple[list[dict[str, Any]], float]:
    """Send ``total`` requests from ``concurrency`` threads; ``make_request(i)`` returns
    ``(method, path, httpx kwargs)``. ``check(i, resp)`` may return a correctness problem.
    Returns per-request rows and the wall time."""
    local = threading.local()
    clients: list[httpx.Client] = []
    lock = threading.Lock()

    def client() -> httpx.Client:
        c = getattr(local, "client", None)
        if c is None:
            c = httpx.Client(base_url=BASE_URL, headers={"X-API-Key": api_key},
                             timeout=timeout)
            local.client = c
            with lock:
                clients.append(c)
        return c

    def one(i: int) -> dict[str, Any]:
        method, path, kw = make_request(i)
        started = time.monotonic()
        try:
            resp = client().request(method, path, **kw)
        except httpx.HTTPError as exc:
            return {"i": i, "status": type(exc).__name__,
                    "ms": (time.monotonic() - started) * 1000}
        row: dict[str, Any] = {"i": i, "status": resp.status_code,
                               "ms": (time.monotonic() - started) * 1000}
        if check is not None and resp.status_code == 200:
            problem = check(i, resp)
            if problem:
                row["problem"] = problem
        return row

    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            rows = list(pool.map(one, range(total)))
    finally:
        for c in clients:
            c.close()
    return rows, time.monotonic() - started
