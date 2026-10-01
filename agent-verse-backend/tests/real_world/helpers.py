"""Shared helpers for the real-world scenario suite (live local Docker stack).

Nothing here imports ``app``: the suite talks to the running stack over HTTP only,
exactly like an external client. The tenant API key is read from the environment
(``AGENTVERSE_API_KEY``) or a JSON file (``AGENTVERSE_TENANT_FILE`` with an
``api_key`` field) and is masked out of every string this module produces.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

BASE_URL = os.getenv("AGENTVERSE_BASE_URL", "http://localhost:8000").rstrip("/")
BACKEND_CONTAINERS = tuple(
    c
    for c in os.getenv(
        "RW_LOG_CONTAINERS",
        "agentverse-backend-backend-1,agentverse-backend-workflow-worker-1,"
        "agentverse-backend-worker-1,agentverse-backend-beat-1",
    ).split(",")
    if c
)

_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    if value and len(value) >= 8:
        _SECRETS.add(value)


def mask(text: Any) -> str:
    """``text`` as a string with every registered secret (and any av_ key) masked."""
    s = text if isinstance(text, str) else json.dumps(text, default=str)
    for secret in _SECRETS:
        s = s.replace(secret, secret[:4] + "***")
    # Belt and braces: any tenant API key shape that is not registered.
    return re.sub(r"\bav_[A-Za-z0-9_\-]{12,}", "av_***", s)


def load_api_key() -> str:
    key = os.getenv("AGENTVERSE_API_KEY", "").strip()
    if not key:
        path = os.getenv("AGENTVERSE_TENANT_FILE", "").strip()
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                key = str(json.load(fh).get("api_key") or "").strip()
    register_secret(key)
    return key


def same_id(a: Any, b: Any) -> bool:
    """UUIDs compare equal with or without dashes (the API returns both forms)."""
    return str(a or "").replace("-", "").lower() == str(b or "").replace("-", "").lower()


def tag() -> str:
    return uuid.uuid4().hex[:8]


class LiveAPI:
    """httpx client bound to the test tenant; keeps a masked request trail."""

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: float = 120.0) -> None:
        self.base_url = base_url
        self.client = httpx.Client(
            base_url=base_url, headers={"X-API-Key": api_key}, timeout=timeout
        )
        self.trail: list[str] = []

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """One API call; a plan rate-limit 429 is waited out (the free test tenant
        has a per-minute request budget that polling scenarios can exhaust)."""
        for attempt in range(8):
            started = time.monotonic()
            resp = self.client.request(method, path, **kw)
            self.trail.append(f"{method} {path} -> {resp.status_code} "
                              f"({int((time.monotonic() - started) * 1000)}ms)")
            del self.trail[:-60]
            if resp.status_code != 429 or attempt == 7:
                return resp
            self.rate_limited += 1
            try:
                wait = float(resp.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0.0
            time.sleep(min(max(wait, 5.0 * (attempt + 1)), 30.0))
        return resp

    rate_limited = 0

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("DELETE", path, **kw)

    def json_ok(self, method: str, path: str, expect: tuple[int, ...] = (200, 201, 202), **kw: Any
                ) -> Any:
        """Request and return JSON, failing with a masked body on an unexpected status."""
        resp = self.request(method, path, **kw)
        assert resp.status_code in expect, (
            f"{method} {path} -> {resp.status_code}: {mask(resp.text[:600])}"
        )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text


def body_of(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def wait_until(
    probe: Callable[[], Any],
    *,
    timeout: float,
    interval: float = 3.0,
    desc: str = "condition",
    done: Callable[[Any], bool] = bool,
) -> Any:
    """Poll ``probe`` until ``done(value)``; raise with the last observation on timeout."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while True:
        last = probe()
        if done(last):
            return last
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"timed out after {timeout:.0f}s waiting for {desc}; last={mask(last)[:800]}"
            )
        time.sleep(interval)


class SSECollector:
    """Read an SSE endpoint in a background thread, collecting ``data:`` payloads."""

    def __init__(self, api_key: str, path: str, max_seconds: float = 120.0) -> None:
        self.path = path
        self.api_key = api_key
        self.max_seconds = max_seconds
        self.status_code: int | None = None
        self.error: str | None = None
        self.events: list[str] = []
        self.first_body: str = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> SSECollector:
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            with httpx.Client(base_url=BASE_URL, headers={"X-API-Key": self.api_key},
                              timeout=httpx.Timeout(10.0, read=self.max_seconds)) as c, \
                    c.stream("GET", self.path) as resp:
                self.status_code = resp.status_code
                if resp.status_code != 200:
                    self.first_body = mask(resp.read().decode(errors="replace")[:300])
                    return
                started = time.monotonic()
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        self.events.append(line[5:].strip())
                    if self._stop.is_set() or time.monotonic() - started > self.max_seconds:
                        return
        except Exception as exc:  # the stream ending on stop/timeout is expected
            if not self._stop.is_set():
                self.error = f"{type(exc).__name__}: {mask(str(exc))[:200]}"

    def saw(self, needle: str) -> bool:
        return any(needle in e for e in self.events)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def docker_logs(needles: list[str], since: str = "15m", limit: int = 12) -> list[str]:
    """Recent backend/worker log lines containing any of ``needles`` (masked).

    Read-only (``docker logs``); used as failure evidence. Returns [] when docker
    is unavailable.
    """
    out: list[str] = []
    wanted = [n for n in needles if n]
    if not wanted:
        return out
    for container in BACKEND_CONTAINERS:
        with contextlib.suppress(Exception):
            proc = subprocess.run(
                ["docker", "logs", "--since", since, container],
                capture_output=True, text=True, timeout=30, check=False,
            )
            for raw in (proc.stdout + proc.stderr).splitlines():
                line = re.sub(r"\x1b\[[0-9;]*m", "", raw)
                if "HTTP/1.1" in line:  # uvicorn access lines carry no diagnosis
                    continue
                if any(n in line for n in wanted):
                    out.append(f"[{container.replace('agentverse-backend-', '')}] {line[:400]}")
    # Prefer errors/warnings, then the most recent lines.
    ranked = sorted(
        out, key=lambda ln: 0 if re.search(r"error|exception|traceback|fail", ln, re.I) else 1
    )
    return [mask(x) for x in ranked[:limit]]
