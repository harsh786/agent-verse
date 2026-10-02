"""A local HTTP fixture server the live stack calls back into.

Workflow HTTP steps, RSS sources and scheduled jobs hit this server, so a scenario
can assert *side effects* (how many times an endpoint was called, what was
published) instead of trusting the platform's own report of what it did.

Routes (``<key>`` isolates one scenario run from another):

    GET  /orders/<key>.json        orders fixture (fixtures/orders.json)
    GET  /fx-rates/<key>           FX rates          GET /inventory/<key>   stock levels
    GET  /carrier-sla/<key>        carrier SLAs
    POST /flaky/<key>?fail=N       503 for the first N calls, then 200 {"attempt": n}
    POST /charge/<key>             side-effect counter {"charged": true, "count": n}
    GET  /switch/<key>             500 while the switch is on, else 200
    POST /publish/<key>            records the JSON body (``published[key]``)
    GET  /feed/<key>.xml           the RSS document set with :meth:`set_feed`
    GET  /health                   200 "ok"

The stack reaches it at :attr:`FixtureServer.public_base`: ``RW_FIXTURE_PUBLIC_URL``
when set (e.g. a tunnel — the workflow HTTP step's SSRF guard refuses private
addresses), else ``http://$RW_FIXTURE_HOST:<port>`` (default host.docker.internal).
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlparse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_orders() -> dict[str, Any]:
    with open(os.path.join(FIXTURES, "orders.json"), encoding="utf-8") as fh:
        return dict(json.load(fh))


FX_RATES = {"base": "INR", "rates": {"USD": 0.01198, "EUR": 0.01102, "SGD": 0.01611},
            "as_of": "2026-10-01"}
INVENTORY = {"warehouse": "WH-9 Hosur", "skus": {"LX-COLDBOX-40": 412, "LX-PALLET-EU": 1290,
                                                 "LX-EDGE-GUARD": 9800}}
CARRIER_SLA = {"carriers": [{"name": "Kestrel Cargo", "on_time_pct": 96.4},
                            {"name": "Osprey Movers", "on_time_pct": 91.8},
                            {"name": "Tern Express", "on_time_pct": 98.1}]}


class FixtureServer:
    def __init__(self, port: int | None = None, host: str = "0.0.0.0") -> None:
        self.host = host
        self.port = port if port is not None else int(os.getenv("RW_FIXTURE_PORT", "0"))
        self.hits: Counter[str] = Counter()
        self.published: dict[str, list[Any]] = {}
        self.switches: dict[str, bool] = {}
        self.feeds: dict[str, str] = {}
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> FixtureServer:
        if not self.port:
            with socket.socket() as s:
                s.bind((self.host, 0))
                self.port = s.getsockname()[1]
        self._server = http.server.ThreadingHTTPServer((self.host, self.port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> FixtureServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def local_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def public_base(self) -> str:
        explicit = os.getenv("RW_FIXTURE_PUBLIC_URL", "").strip().rstrip("/")
        if explicit:
            return explicit
        return f"http://{os.getenv('RW_FIXTURE_HOST', 'host.docker.internal')}:{self.port}"

    @staticmethod
    def is_public() -> bool:
        """Whether the stack's SSRF guards can reach it (an explicit public URL)."""
        return bool(os.getenv("RW_FIXTURE_PUBLIC_URL", "").strip())

    # ── scenario controls ──────────────────────────────────────────────────
    def count(self, method: str, path: str) -> int:
        with self._lock:
            return self.hits[f"{method} {path}"]

    def set_switch(self, key: str, on: bool) -> None:
        with self._lock:
            self.switches[key] = on

    def set_feed(self, key: str, xml: str) -> None:
        with self._lock:
            self.feeds[key] = xml

    # ── request handling ───────────────────────────────────────────────────
    def _respond(self, method: str, raw_path: str, body: bytes) -> tuple[int, Any, str]:
        url = urlparse(raw_path)
        path = url.path
        query = parse_qs(url.query)
        with self._lock:
            self.hits[f"{method} {path}"] += 1
            n = self.hits[f"{method} {path}"]
            self.requests.append({"method": method, "path": path, "n": n})
        parts = [p for p in path.split("/") if p]
        head = parts[0] if parts else ""
        key = parts[1] if len(parts) > 1 else ""
        payload: Any = None
        if body:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = body.decode(errors="replace")
        if method == "GET" and head == "health":
            return 200, "ok", "text/plain"
        if method == "GET" and head == "orders":
            return 200, load_orders(), "application/json"
        if method == "GET" and head == "fx-rates":
            return 200, FX_RATES, "application/json"
        if method == "GET" and head == "inventory":
            return 200, INVENTORY, "application/json"
        if method == "GET" and head == "carrier-sla":
            return 200, CARRIER_SLA, "application/json"
        if method == "POST" and head == "flaky":
            fail = int((query.get("fail") or ["2"])[0])
            if n <= fail:
                return 503, {"error": "ledger temporarily unavailable", "attempt": n}, \
                    "application/json"
            return 200, {"ok": True, "attempt": n, "received": payload}, "application/json"
        if method == "POST" and head == "charge":
            return 200, {"charged": True, "count": n, "received": payload}, "application/json"
        if method == "GET" and head == "switch":
            with self._lock:
                on = self.switches.get(key, False)
            if on:
                return 500, {"error": "downstream finance API is failing", "attempt": n}, \
                    "application/json"
            return 200, {"ok": True, "attempt": n}, "application/json"
        if method == "POST" and head == "publish":
            with self._lock:
                self.published.setdefault(key, []).append(payload)
            return 200, {"published": True, "id": f"pub-{key}-{n}"}, "application/json"
        if method == "GET" and head == "feed":
            feed_key = key.removesuffix(".xml")
            with self._lock:
                xml = self.feeds.get(feed_key)
            if xml is None:
                return 404, {"error": "no such feed"}, "application/json"
            return 200, xml, "application/rss+xml"
        return 404, {"error": f"no route {method} {path}"}, "application/json"

    def _handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                status, payload, ctype = server._respond(method, self.path, body)
                raw = payload if isinstance(payload, str) else json.dumps(payload)
                data = raw.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._serve("GET")

            def do_POST(self) -> None:
                self._serve("POST")

            def log_message(self, *args: Any) -> None:
                return

        return Handler


def rss_feed(items: list[dict[str, str]], title: str = "Larkspur Ops Bulletin") -> str:
    """An RSS 2.0 document; each item needs guid, title, description, pubDate."""
    body = "".join(
        f"<item><title>{i['title']}</title><link>https://bulletin.example/{i['guid']}</link>"
        f"<guid isPermaLink=\"false\">{i['guid']}</guid><pubDate>{i['pubDate']}</pubDate>"
        f"<description>{i['description']}</description></item>"
        for i in items
    )
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            f"<title>{title}</title><link>https://bulletin.example/</link>"
            f"<description>Operations bulletin</description>{body}</channel></rss>")
