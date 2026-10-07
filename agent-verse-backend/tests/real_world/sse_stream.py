"""SSE reading with ids, resume (``Last-Event-ID``) and gap / duplicate accounting.

``GET /goals/{id}/stream`` writes ``id: <durable sequence>`` before each stored
event and resumes after the ``Last-Event-ID`` header; ``GET /api/v1/runs/{id}/stream``
writes plain ``data:`` lines (no ids). :func:`parse_sse` is a pure parser (unit-tested
offline); :class:`StreamReader` reads one connection in a thread.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.real_world.helpers import BASE_URL, mask


@dataclass
class SSEEvent:
    id: int | None
    event: str
    data: Any


def parse_sse(lines: list[str]) -> list[SSEEvent]:
    """Events of an SSE byte stream already split into lines (comments dropped)."""
    out: list[SSEEvent] = []
    ev_id: int | None = None
    ev_name = "message"
    data: list[str] = []

    def flush() -> None:
        nonlocal ev_id, ev_name, data
        if data:
            raw = "\n".join(data)
            try:
                value: Any = json.loads(raw)
            except ValueError:
                value = raw
            out.append(SSEEvent(ev_id, ev_name, value))
        ev_id, ev_name, data = None, "message", []

    for line in lines:
        if line == "":
            flush()
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "id":
            ev_id = int(value) if value.isdigit() else None
        elif name == "event":
            ev_name = value
        elif name == "data":
            data.append(value)
    flush()
    return out


def gaps_and_duplicates(first: list[int], second: list[int], full: list[int]
                        ) -> dict[str, list[int]]:
    """Compare two connections (before / after a reconnect) with a full replay.

    ``missing``: in the full replay but seen on neither connection; ``duplicated``:
    delivered on both connections (the resume replayed what was already seen);
    ``unknown``: delivered but absent from the full replay; ``out_of_order``: ids that
    went backwards within one connection.
    """
    seen = set(first) | set(second)
    backwards = [b for conn in (first, second) for a, b in itertools.pairwise(conn)
                 if b <= a]
    return {"missing": sorted(set(full) - seen),
            "duplicated": sorted(set(first) & set(second)),
            "unknown": sorted(seen - set(full)),
            "out_of_order": backwards}


@dataclass
class StreamReader:
    """One SSE connection in a background thread (``last_id`` drives a resume)."""

    api_key: str
    path: str
    last_event_id: int | None = None
    max_seconds: float = 900.0
    events: list[SSEEvent] = field(default_factory=list)
    status_code: int | None = None
    error: str | None = None
    ended_cleanly: bool = False

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> StreamReader:
        self._thread.start()
        return self

    def _run(self) -> None:
        headers = {"X-API-Key": self.api_key, "Accept": "text/event-stream"}
        if self.last_event_id is not None:
            headers["Last-Event-ID"] = str(self.last_event_id)
        buf: list[str] = []
        try:
            with httpx.Client(base_url=BASE_URL, headers=headers,
                              timeout=httpx.Timeout(10.0, read=60.0)) as c, \
                    c.stream("GET", self.path) as resp:
                self.status_code = resp.status_code
                if resp.status_code != 200:
                    self.error = mask(resp.read().decode(errors="replace")[:300])
                    return
                started = time.monotonic()
                for line in resp.iter_lines():
                    buf.append(line)
                    if line == "":
                        self.events.extend(parse_sse(buf))
                        buf = []
                    if self._stop.is_set() or time.monotonic() - started > self.max_seconds:
                        return
                self.events.extend(parse_sse(buf))
                self.ended_cleanly = True
        except Exception as exc:  # the backend being killed mid-stream lands here
            self.events.extend(parse_sse(buf))
            if not self._stop.is_set():
                self.error = f"{type(exc).__name__}: {mask(str(exc))[:200]}"

    @property
    def ids(self) -> list[int]:
        return [e.id for e in self.events if e.id is not None]

    @property
    def last_id(self) -> int | None:
        ids = self.ids
        return ids[-1] if ids else self.last_event_id

    def alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout=timeout)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def types(self) -> list[str]:
        out = []
        for e in self.events:
            if isinstance(e.data, dict):
                out.append(str(e.data.get("type") or e.data.get("event") or e.event))
            else:
                out.append(str(e.data)[:40])
        return out
