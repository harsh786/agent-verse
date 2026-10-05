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

Programmable web site (P1d, URL ingest and web crawl): any path registered through
the control API is served exactly as registered (status, headers, raw bytes, an
optional delay, "fail the first N requests"), including ``/robots.txt`` and
redirects. Every request to a registered path is logged with its time, Host header,
User-Agent and conditional headers, so a scenario can assert crawl scope, robots.txt,
politeness gaps and SSRF behaviour from the server's side:

    PUT    /_control/route         {"path", "status", "headers", "body_b64" | "body",
                                    "repeat", "delay_s", "fail_first", "fail_status"}
    DELETE /_control/route?path=…  the path answers 404 again
    GET    /_control/hits?prefix=… the request log of matching paths
    DELETE /_control/hits          clear the request log

``path`` may carry a query string (``/a?id=1``): an exact path+query match wins over
the bare path. Run it standalone (the live stack reaches it on the compose network,
see ``web_site.py``): ``python fixture_server.py --serve --port 8080``.

The stack reaches it at :attr:`FixtureServer.public_base`: ``RW_FIXTURE_PUBLIC_URL``
when set (e.g. a tunnel — the workflow HTTP step's SSRF guard refuses private
addresses), else ``http://$RW_FIXTURE_HOST:<port>`` (default host.docker.internal).
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import socket
import threading
import time
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
        # Programmable site: "path[?query]" -> route spec; and its request log.
        self.routes: dict[str, dict[str, Any]] = {}
        self.route_hits: Counter[str] = Counter()
        self.site_log: list[dict[str, Any]] = []
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

    # ── programmable site ───────────────────────────────────────────────────
    def set_route(self, spec: dict[str, Any]) -> None:
        path = str(spec["path"])
        with self._lock:
            self.routes[path] = dict(spec)
            self.route_hits.pop(path, None)

    def remove_route(self, path: str) -> None:
        with self._lock:
            self.routes.pop(path, None)

    def site_hits(self, prefix: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [h for h in self.site_log if h["path"].startswith(prefix)]

    def _control(self, method: str, raw_path: str, body: bytes) -> tuple[int, Any]:
        url = urlparse(raw_path)
        query = parse_qs(url.query)
        what = url.path.removeprefix("/_control/")
        if what == "route" and method == "PUT":
            self.set_route(json.loads(body))
            return 200, {"ok": True}
        if what == "route" and method == "DELETE":
            self.remove_route((query.get("path") or [""])[0])
            return 200, {"ok": True}
        if what == "hits" and method == "GET":
            return 200, self.site_hits((query.get("prefix") or [""])[0])
        if what == "hits" and method == "DELETE":
            with self._lock:
                self.site_log.clear()
            return 200, {"ok": True}
        return 404, {"error": f"no control route {method} {url.path}"}

    def _route_for(self, raw_path: str) -> tuple[str, dict[str, Any]] | None:
        path = urlparse(raw_path).path
        with self._lock:
            if raw_path in self.routes:
                return raw_path, self.routes[raw_path]
            if path in self.routes:
                return path, self.routes[path]
        return None

    def _serve_route(self, method: str, key: str, spec: dict[str, Any],
                     headers: Any, raw_path: str) -> tuple[int, dict[str, str], bytes, float]:
        with self._lock:
            self.route_hits[key] += 1
            n = self.route_hits[key]
            self.site_log.append({
                "method": method, "path": raw_path, "route": key, "n": n,
                "t": time.time(), "host": headers.get("Host", ""),
                "ua": headers.get("User-Agent", ""),
                "if_none_match": headers.get("If-None-Match", ""),
                "if_modified_since": headers.get("If-Modified-Since", ""),
            })
        delay = float(spec.get("delay_s") or 0)
        if n <= int(spec.get("fail_first") or 0):
            status = int(spec.get("fail_status") or 503)
            return status, {"Content-Type": "text/plain", **dict(spec.get("fail_headers") or {})}, \
                f"temporarily failing ({n})".encode(), delay
        if "body_b64" in spec:
            data = base64.b64decode(spec["body_b64"])
        else:
            data = str(spec.get("body") or "").encode("utf-8")
        data = data * max(1, int(spec.get("repeat") or 1))
        out_headers = {"Content-Type": "text/html; charset=utf-8"}
        out_headers.update({str(k): str(v) for k, v in dict(spec.get("headers") or {}).items()})
        return int(spec.get("status") or 200), out_headers, data, delay

    def _handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, status: int, headers: dict[str, str], data: bytes,
                      method: str) -> None:
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if method != "HEAD":
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        return  # the client gave up (a size cap or a timeout)

            def _serve(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if self.path.startswith("/_control/"):
                    status, payload = server._control(method, self.path, body)
                    self._send(status, {"Content-Type": "application/json"},
                               json.dumps(payload).encode(), method)
                    return
                route = server._route_for(self.path)
                if route is not None:
                    status, headers, data, delay = server._serve_route(
                        method, route[0], route[1], self.headers, self.path)
                    if delay:
                        time.sleep(delay)
                    self._send(status, headers, data, method)
                    return
                status, payload, ctype = server._respond(method, self.path, body)
                raw = payload if isinstance(payload, str) else json.dumps(payload)
                self._send(status, {"Content-Type": ctype}, raw.encode(), method)

            def do_GET(self) -> None:
                self._serve("GET")

            def do_HEAD(self) -> None:
                self._serve("HEAD")

            def do_POST(self) -> None:
                self._serve("POST")

            def do_PUT(self) -> None:
                self._serve("PUT")

            def do_DELETE(self) -> None:
                self._serve("DELETE")

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


if __name__ == "__main__":  # pragma: no cover - the containerised web fixture
    import argparse

    parser = argparse.ArgumentParser(description="real-world fixture server")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    srv = FixtureServer(port=args.port).start()
    print(f"fixture server on :{srv.port}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
