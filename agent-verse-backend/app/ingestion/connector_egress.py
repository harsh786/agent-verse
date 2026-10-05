"""Egress guard for ingestion connectors.

Most connectors fetch a host the **tenant** supplies — Jira/Confluence
``base_url``, a self-hosted GitLab, a ServiceNow ``instance``, an Elasticsearch
``url``, web-crawl ``seed_urls``, a list of PDF ``urls``. Without a guard that is
a Server-Side Request Forgery primitive with an unusually direct payoff: whatever
the platform fetches is parsed, chunked, embedded and indexed into *the
requesting tenant's own* knowledge collection, where they can then simply
retrieve it. A tenant pointing a web-crawl source at
``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` gets the
platform's cloud credentials delivered into their RAG index.

``app.net.ssrf_guard.assert_public_url`` already implements the check (private
ranges, link-local, metadata hostnames, scheme allowlist, and a post-DNS
re-check against rebinding). This module is the ingestion-side policy wrapper:
one import, one call, one consistent error, plus the operator-only escape hatch
an on-prem deployment needs when its Jira really does live on a LAN.

The escape hatch is deliberately **operator-scoped, never tenant-scoped**:
``INGESTION_ALLOW_INTERNAL_SOURCES`` plus an explicit
``INGESTION_INTERNAL_SOURCE_ALLOWLIST`` of hostnames. A value in
``connection_config`` can never widen it — that field is exactly what an attacker
controls.

Checking a URL and then fetching it with a plain ``httpx.AsyncClient`` leaves a
DNS-rebinding window (the client resolves the name again). Connectors build
their HTTP clients with :func:`source_client`, which pins every connection to an
address validated at connect time under this same policy.

Database drivers and vendor SDKs (pymysql, pymongo, neo4j, paho-mqtt, imaplib,
clickhouse-connect, influxdb-client, boto3, azure-storage-blob) resolve the host
themselves, so the same window existed there. They run inside
:func:`pin_source_hosts` / :func:`pin_source_dsn` / :func:`pin_source_urls`: the
host is resolved and checked **once**, and for the lifetime of the block every
``socket.getaddrinfo`` lookup of that name answers with the checked addresses
only, so the driver dials what was validated while TLS still sees the real
hostname (SNI + certificate checks unchanged). Drivers that bypass the Python
resolver get the checked IP directly (asyncpg, which may run on uvloop), and a
driver that cannot be pinned at all (librdkafka) is refused while
``INGESTION_EGRESS_STRICT_PINNING`` is on.
"""

from __future__ import annotations

import contextlib
import contextvars
import ipaddress
import socket
import ssl
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from app.net.ssrf_guard import SSRFError, assert_public_url, public_async_client
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.ingestion.source_config import SourceConfig

_log = get_logger(__name__)

__all__ = [
    "ConnectorEgressBlockedError",
    "GuardedFetch",
    "RedirectBlockedError",
    "assert_source_dsn",
    "assert_source_host",
    "assert_source_url",
    "check_source_dsn",
    "check_source_host",
    "egress_checked_lookups",
    "guarded_fetch",
    "guarded_request",
    "pin_source_dsn",
    "pin_source_hosts",
    "pin_source_urls",
    "pin_source_urls_sync",
    "require_pinnable_driver",
    "run_driver_call",
    "source_client",
    "source_url_is_allowed",
]

_MAX_REDIRECTS = 5


class ConnectorEgressBlockedError(SSRFError):
    """A connector tried to fetch a URL the egress policy forbids."""


def _operator_allowlist() -> tuple[bool, list[str]]:
    """Return ``(allow_internal, allowed_domains)`` from operator settings."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception:  # pragma: no cover - settings unavailable: fail closed
        return False, []
    allow = bool(getattr(settings, "ingestion_allow_internal_sources", False))
    raw = str(getattr(settings, "ingestion_internal_source_allowlist", "") or "")
    domains = [d.strip().lower() for d in raw.split(",") if d.strip()]
    return allow, domains


def _effective_allowlist() -> list[str] | None:
    """The operator allowlist, honoured only when the escape hatch is also on."""
    allow_internal, allowed_domains = _operator_allowlist()
    # Both halves are env-only: an allowlist alone must not be able to punch a
    # hole, and connection_config can never reach either.
    return allowed_domains if (allow_internal and allowed_domains) else None


def source_client(**httpx_kwargs: Any) -> Any:
    """An ``httpx.AsyncClient`` for connector fetches, pinned under the egress policy.

    Connections are dialled only to addresses checked at connect time (public,
    or an operator-allowlisted internal host), so a name that re-resolves to
    127.0.0.1 / 169.254.169.254 after :func:`assert_source_url` passed is
    refused. Redirects are never followed automatically — use
    :func:`guarded_request` for URLs that can redirect.
    """
    return public_async_client(allowed_domains=_effective_allowlist(), **httpx_kwargs)


def assert_source_url(url: str, *, context: str, config: SourceConfig | None = None) -> list[str]:
    """Raise :class:`ConnectorEgressBlockedError` unless ``url`` is safe to fetch.

    Call this before *any* outbound request a connector makes to a host derived
    from ``connection_config`` — in ``validate_connection`` as well as
    ``get_delta``, since validation runs against the same attacker-supplied URL
    and returns its own response body/error to the caller.

    Returns the checked addresses. A name that does not resolve is refused,
    allowlisted or not (SSRF-02: nothing would have been checked).
    """
    del config  # tenant config must never widen the policy; kept for call-site clarity
    if not url:
        raise ConnectorEgressBlockedError(f"SSRF guard [{context}]: empty URL")

    effective_allowlist = _effective_allowlist()
    try:
        return assert_public_url(url, allowed_domains=effective_allowlist, context=context)
    except (SSRFError, ValueError) as exc:
        _log.warning("connector_egress_blocked", context=context, error=str(exc)[:200])
        raise ConnectorEgressBlockedError(str(exc)) from exc


def source_url_is_allowed(url: str, *, context: str) -> bool:
    """Non-raising form, for loops that skip bad URLs instead of aborting a sync."""
    try:
        assert_source_url(url, context=context)
        return True
    except (ConnectorEgressBlockedError, SSRFError, ValueError):
        return False


def assert_source_host(host: object, port: object = None, *, context: str) -> list[str]:
    """Egress check for a connector that dials ``host[:port]`` directly (a database
    driver, a broker) rather than fetching a URL.

    The host is resolved and **every** resolved address checked — the same
    post-DNS, anti-rebinding check :func:`assert_source_url` applies — so a public
    name pointing at ``10.0.0.5`` is refused, not just a literal private IP. A
    Unix-socket path (``/var/run/postgresql``) is refused outright: it is the
    platform's own filesystem, never a tenant's database.
    """
    raw = str(host or "").strip()
    if not raw:
        raise ConnectorEgressBlockedError(f"SSRF guard [{context}]: empty host")
    if raw.startswith(("@", "\\")) or "/" in raw or "@" in raw:
        raise ConnectorEgressBlockedError(
            f"SSRF guard [{context}]: socket paths / path-like hosts are blocked"
        )
    bracketed = f"[{raw}]" if ":" in raw and not raw.startswith("[") else raw
    port_part = ""
    if port not in (None, ""):
        try:
            port_part = f":{int(str(port))}"
        except ValueError as exc:
            raise ConnectorEgressBlockedError(
                f"SSRF guard [{context}]: invalid port {port!r} blocked"
            ) from exc
    return assert_source_url(f"http://{bracketed}{port_part}/", context=context)


def _dsn_hosts(dsn: str) -> list[tuple[str, str]]:
    """Every (host, port) a URI or libpq keyword DSN would dial.

    Handles multi-host URIs (``postgresql://u:p@h1:5432,h2:5433/db``,
    ``mongodb://h1,h2/``), the libpq ``host=`` / ``hostaddr=`` keyword form, and a
    ``?host=`` query parameter, which libpq honours over the netloc.
    """
    text = dsn.strip()
    hosts: list[tuple[str, str]] = []
    if "://" not in text:
        for part in text.split():
            key, _, value = part.partition("=")
            if key.lower() in ("host", "hostaddr"):
                hosts.extend((h, "") for h in value.strip("'\"").split(",") if h)
        return hosts or [("", "")]
    parts = urlsplit(text)
    netloc = parts.netloc.rpartition("@")[2]
    for item in netloc.split(","):
        item = item.strip()
        if not item:
            continue
        if item.startswith("["):
            host, _, rest = item[1:].partition("]")
            hosts.append((host, rest.lstrip(":")))
        else:
            host, _, port = item.partition(":")
            hosts.append((unquote(host), port))
    for pair in parts.query.split("&"):
        key, _, value = pair.partition("=")
        if key.lower() in ("host", "hostaddr"):
            hosts.extend((unquote(h), "") for h in value.split(",") if h)
    return hosts or [("", "")]


def _srv_records(name: str) -> list[tuple[str, int]]:
    """Resolve ``mongodb+srv://name`` to the (host, port) pairs the driver would dial."""
    try:
        import dns.resolver
    except ImportError as exc:  # pragma: no cover - dnspython ships with the app
        raise ConnectorEgressBlockedError(
            "SSRF guard: cannot resolve SRV record (dnspython missing) — blocked"
        ) from exc
    try:
        answers = dns.resolver.resolve(f"_mongodb._tcp.{name}", "SRV")
    except Exception as exc:
        raise ConnectorEgressBlockedError(
            f"SSRF guard: cannot resolve SRV record for {name!r} — blocked"
        ) from exc
    return [(str(a.target).rstrip("."), int(getattr(a, "port", 27017))) for a in answers]


def _srv_targets(name: str) -> list[str]:
    """Resolve ``mongodb+srv://name`` to the hosts the driver will actually dial."""
    return [host for host, _port in _srv_records(name)]


def assert_source_dsn(dsn: object, *, context: str) -> None:
    """Egress check for a database DSN / connection URI a tenant supplied.

    Validates every host the driver could dial (see :func:`_dsn_hosts`). For
    ``mongodb+srv`` the SRV targets are resolved and each is checked, since the
    SRV name itself says nothing about where the driver will connect.

    This is the check only: a driver that dials the DSN afterwards resolves the
    names again. Connectors run their driver inside :func:`pin_source_dsn`, which
    performs this check and pins the checked addresses for the connection.
    """
    text = str(dsn or "").strip()
    if not text:
        raise ConnectorEgressBlockedError(f"SSRF guard [{context}]: empty DSN blocked")
    is_srv = text.lower().startswith("mongodb+srv://")
    for host, port in _dsn_hosts(text):
        if is_srv:
            for target in _srv_targets(host):
                assert_source_host(target, context=context)
        else:
            assert_source_host(host, port or None, context=context)


_PERMANENT_REDIRECTS = frozenset({301, 308})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class RedirectBlockedError(ConnectorEgressBlockedError):
    """A redirect pointed at a destination the egress policy forbids."""


@dataclass
class GuardedFetch:
    """A response reached through egress-checked redirects (USR-5).

    ``final_url`` is the URL the response came from; ``hops`` lists every
    redirect as ``(status, from, to)``; ``moved_to`` is where the requested URL
    moved *permanently* (the target of the leading 301/308 redirects), or None.
    """

    response: Any
    requested_url: str
    final_url: str
    hops: list[tuple[int, str, str]] = field(default_factory=list)

    @property
    def moved_to(self) -> str | None:
        target: str | None = None
        for status, _src, dst in self.hops:
            if status not in _PERMANENT_REDIRECTS:
                break
            target = dst
        return target

    @property
    def moved_status(self) -> int | None:
        return self.hops[0][0] if self.moved_to else None

    def move_notice(self) -> dict[str, Any] | None:
        """``{"from", "to", "status"}`` when the requested URL moved permanently."""
        if not self.moved_to:
            return None
        return {"from": self.requested_url, "to": self.moved_to, "status": self.moved_status}


async def guarded_fetch(
    client: Any,
    method: str,
    url: str,
    *,
    context: str,
    max_redirects: int = _MAX_REDIRECTS,
    stream: bool = False,
    **kwargs: Any,
) -> GuardedFetch:
    """Send ``method url`` following redirects safely; return the response and its path.

    Redirects are followed manually so each ``Location`` is re-validated before it
    is requested: with ``follow_redirects=True`` a public URL can 302 straight to
    ``169.254.169.254`` past a check on the first URL only. A blocked hop raises
    :class:`RedirectBlockedError` (the target is never requested); more than
    ``max_redirects`` hops raise ``httpx.TooManyRedirects``. Credentials
    (``auth``, ``Authorization`` / ``Cookie`` headers) are dropped once a redirect
    leaves the original host, so a redirect cannot harvest them either.

    ``stream=True`` returns the final response unread (``client.send(...,
    stream=True)``): the caller reads it within its own size cap and must
    ``aclose()`` it. Redirect responses are closed before the next hop.
    """
    import asyncio

    import httpx

    # Same shape as app.net.ssrf_guard.request_public (DNS off the event loop,
    # every hop re-checked), but through the ingestion policy so the operator
    # on-prem allowlist applies, and with credentials dropped off-host.
    await asyncio.to_thread(assert_source_url, url, context=context)
    current = url
    origin = (urlsplit(url).hostname or "").lower()
    hops: list[tuple[int, str, str]] = []
    from urllib.parse import urljoin

    for _hop in range(max_redirects + 1):
        if stream:
            send_kwargs: dict[str, Any] = {}
            if kwargs.get("auth") is not None:
                send_kwargs["auth"] = kwargs["auth"]
            request = client.build_request(
                method, current, **{k: v for k, v in kwargs.items() if k != "auth"}
            )
            response = await client.send(
                request, stream=True, follow_redirects=False, **send_kwargs
            )
        else:
            response = await client.request(method, current, follow_redirects=False, **kwargs)
        status = getattr(response, "status_code", None)
        location = ""
        if isinstance(status, int) and status in _REDIRECT_STATUSES:
            location = str((getattr(response, "headers", None) or {}).get("location", "") or "")
        if not location:
            return GuardedFetch(response=response, requested_url=url, final_url=current, hops=hops)
        if stream:
            await response.aclose()
        nxt = urljoin(current, location)
        try:
            await asyncio.to_thread(assert_source_url, nxt, context=context)
        except ConnectorEgressBlockedError as exc:
            raise RedirectBlockedError(
                f"{current} redirected ({status}) to {nxt}, a destination "
                f"blocked by the egress policy — not followed: {exc}"
            ) from exc
        hops.append((int(status or 0), current, nxt))
        if (urlsplit(nxt).hostname or "").lower() != origin:
            kwargs.pop("auth", None)
            kwargs["headers"] = {
                k: v
                for k, v in dict(kwargs.get("headers") or {}).items()
                if k.lower() not in ("authorization", "cookie", "proxy-authorization")
            }
        if status in (301, 302, 303) and method.upper() != "GET":
            method = "GET"
            for body_kw in ("content", "data", "json", "files"):
                kwargs.pop(body_kw, None)
        current = nxt
    raise httpx.TooManyRedirects(
        f"{url}: more than {max_redirects} redirects (last: {current}) — not followed"
    )


async def guarded_request(
    client: Any,
    method: str,
    url: str,
    *,
    context: str,
    max_redirects: int = _MAX_REDIRECTS,
    **kwargs: Any,
) -> Any:
    """:func:`guarded_fetch`, returning only the response."""
    fetched = await guarded_fetch(
        client, method, url, context=context, max_redirects=max_redirects, **kwargs
    )
    return fetched.response


async def check_source_host(host: object, port: object = None, *, context: str) -> None:
    """:func:`assert_source_host` with the DNS lookup off the event loop."""
    import asyncio

    await asyncio.to_thread(assert_source_host, host, port, context=context)


async def check_source_dsn(dsn: object, *, context: str) -> None:
    """:func:`assert_source_dsn` with the DNS lookup off the event loop."""
    import asyncio

    await asyncio.to_thread(assert_source_dsn, dsn, context=context)


# ── Resolver pinning for drivers / SDKs that resolve the host themselves ─────
#
# ``_pins`` maps a normalised hostname to the address lists currently pinned for
# it (one entry per active pin block; several syncs may pin the same name at
# once, and each list was checked). While a name is pinned, ``socket.getaddrinfo``
# answers with those addresses only — numerically, never from DNS — so a driver
# that looks the name up again cannot be steered to an address that was not
# checked. Every other lookup passes straight through.

_pins: dict[str, list[tuple[str, ...]]] = {}
_pins_lock = threading.Lock()

# Set (to the egress context name) only inside :func:`egress_checked_lookups`,
# i.e. on the worker thread running a driver's blocking work. While set, a
# lookup of a name that is *not* pinned is egress-checked before it is answered.
_checked_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "connector_egress_checked_scope", default=None
)


def _host_key(host: object) -> str:
    if isinstance(host, bytes | bytearray):
        host = bytes(host).decode("ascii", errors="replace")
    return str(host or "").strip().strip("[]").lower().rstrip(".")


def _numeric_answers(
    inner: Any,
    key: str,
    ips: Iterable[str],
    port: Any,
    family: int,
    type_: int,
    proto: int,
    flags: int,
) -> Any:
    """Answer a lookup of ``key`` with ``ips`` only — numerically, never from DNS."""
    results: list[Any] = []
    for ip in dict.fromkeys(ips):
        try:
            results.extend(inner(ip, port, family, type_, proto, flags | socket.AI_NUMERICHOST))
        except socket.gaierror:
            continue  # e.g. an IPv6 address asked for AF_INET only
    if results:
        return results
    raise socket.gaierror(socket.EAI_NONAME, f"no checked address for {key!r} matches the request")


def _pinned_ip(key: str) -> bool:
    """True when ``key`` is an address some active pin block already checked."""
    with _pins_lock:
        return any(key in entry for entries in _pins.values() for entry in entries)


def _checked_lookup(
    inner: Any,
    scope: str,
    host: Any,
    port: Any,
    family: int,
    type_: int,
    proto: int,
    flags: int,
) -> Any:
    """A lookup made inside :func:`egress_checked_lookups` of a name nobody pinned.

    This is how a driver reaches a host the *server* named — a Neo4j routing
    table entry, an HTTP redirect target inside an SDK, a cluster's advertised
    member. The name is checked under the egress policy right here and answered
    with the checked addresses only; an internal one raises
    :class:`ConnectorEgressBlockedError` instead of being dialled.
    """
    key = _host_key(host)
    if _is_ip_literal(key) and _pinned_ip(key):
        # A driver handed the address pinned for its checked seed (pins.ip()).
        return inner(host, port, family, type_, proto, flags)
    token = _checked_scope.set(None)  # the check's own lookup must not recurse
    try:
        ips = assert_source_host(key, context=scope)
    finally:
        _checked_scope.reset(token)
    if not ips:
        # Operator-allowlisted internal name that does not resolve from here.
        return inner(host, port, family, type_, proto, flags)
    return _numeric_answers(inner, key, ips, port, family, type_, proto, flags)


def _make_pinned_getaddrinfo(inner: Any) -> Any:
    """Wrap ``inner`` (the resolver in place at install time) with the pin table."""

    def _pinned_getaddrinfo(
        host: Any,
        port: Any,
        family: int = 0,
        type: int = 0,  # noqa: A002 - socket.getaddrinfo's own parameter name
        proto: int = 0,
        flags: int = 0,
    ) -> Any:
        if host is None:
            return inner(host, port, family, type, proto, flags)
        if _pins:
            key = _host_key(host)
            with _pins_lock:
                entries = list(_pins.get(key, ()))
            if entries:
                ips = [ip for entry in entries for ip in entry]
                return _numeric_answers(inner, key, ips, port, family, type, proto, flags)
        scope = _checked_scope.get()
        if scope is not None:
            return _checked_lookup(inner, scope, host, port, family, type, proto, flags)
        return inner(host, port, family, type, proto, flags)

    _pinned_getaddrinfo._egress_pin_wrapper = True  # type: ignore[attr-defined]
    return _pinned_getaddrinfo


def _ensure_resolver_pinning_installed() -> None:
    """Route ``socket.getaddrinfo`` through the pin table (idempotent).

    Re-checked on every registration: if something replaced ``socket.getaddrinfo``
    since (a test double, a library), a pin wrapper is layered over the current
    function instead of silently not applying. Each wrapper keeps its own inner
    resolver, so restoring an earlier ``socket.getaddrinfo`` restores a
    consistent one.
    """
    with _pins_lock:
        current = socket.getaddrinfo
        if getattr(current, "_egress_pin_wrapper", False):
            return
        socket.getaddrinfo = _make_pinned_getaddrinfo(current)


@contextlib.contextmanager
def egress_checked_lookups(context: str) -> Iterator[None]:
    """Egress-check every name resolved on *this thread* for the block.

    Pinning covers the hosts a connector knows up front (its seed). A driver can
    learn more from the server — a ``neo4j://`` routing table, a redirect an SDK
    follows, a cluster's advertised members — and would dial those with a plain
    ``socket.getaddrinfo``. Inside this block such a lookup is checked under the
    same policy (:func:`assert_source_host`) and answered with the checked
    addresses only; an internal target raises :class:`ConnectorEgressBlockedError`.

    The scope is a context variable, so it covers only the code running in this
    thread's context — the driver's blocking work — never the platform's own
    connections elsewhere in the process. Use :func:`run_driver_call`.
    """
    _ensure_resolver_pinning_installed()
    token = _checked_scope.set(context)
    try:
        yield
    finally:
        _checked_scope.reset(token)


async def run_driver_call[T](
    func: Callable[..., T], /, *args: Any, context: str, **kwargs: Any
) -> T:
    """Run a driver's blocking ``func`` off the event loop, egress-checked.

    The call runs on the bounded SDK pool (:mod:`app.ingestion.sdk_executor`)
    inside :func:`egress_checked_lookups`, so every host the driver resolves —
    not only the pinned seed — is checked.
    """
    from app.ingestion.sdk_executor import run_blocking

    def _call() -> T:
        with egress_checked_lookups(context):
            return func(*args, **kwargs)

    return await run_blocking(_call)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


@dataclass
class EgressPins:
    """The checked addresses a connector may dial, by hostname."""

    ips: dict[str, list[str]] = field(default_factory=dict)
    dsn: str = ""  # the DSN to hand the driver (SRV URIs are expanded)

    def ip(self, host: object) -> str:
        """A checked address for ``host`` (``host`` itself when nothing is pinned:
        a literal IP, or an operator-allowlisted name unresolvable from here)."""
        addrs = self.ips.get(_host_key(host))
        return addrs[0] if addrs else str(host or "")


def _register(pins: dict[str, list[str]]) -> list[tuple[str, tuple[str, ...]]]:
    _ensure_resolver_pinning_installed()
    tokens: list[tuple[str, tuple[str, ...]]] = []
    with _pins_lock:
        for host, ips in pins.items():
            if not ips or _is_ip_literal(host):
                continue
            entry = tuple(ips)
            _pins.setdefault(host, []).append(entry)
            tokens.append((host, entry))
    return tokens


def _release(tokens: list[tuple[str, tuple[str, ...]]]) -> None:
    with _pins_lock:
        for host, entry in tokens:
            entries = _pins.get(host)
            if not entries:
                continue
            with contextlib.suppress(ValueError):
                entries.remove(entry)
            if not entries:
                _pins.pop(host, None)


def _merge(checked: dict[str, list[str]], host: object, ips: list[str]) -> None:
    key = _host_key(host)
    checked[key] = list(dict.fromkeys([*checked.get(key, []), *ips]))


def _check_hosts(targets: Iterable[tuple[object, object]], context: str) -> dict[str, list[str]]:
    checked: dict[str, list[str]] = {}
    for host, port in targets:
        _merge(checked, host, assert_source_host(host, port, context=context))
    return checked


@contextlib.asynccontextmanager
async def _pinned(checked: dict[str, list[str]], dsn: str = "") -> AsyncIterator[EgressPins]:
    tokens = _register(checked)
    try:
        yield EgressPins(ips=checked, dsn=dsn)
    finally:
        _release(tokens)


@contextlib.asynccontextmanager
async def pin_source_hosts(
    targets: Iterable[tuple[object, object]], *, context: str
) -> AsyncIterator[EgressPins]:
    """Check every ``(host, port)`` (DNS off the loop) and pin it for the block.

    Raises :class:`ConnectorEgressBlockedError` exactly like
    :func:`assert_source_host`. Run *all* driver work for these hosts inside the
    block; lookups made after it ends are no longer pinned.
    """
    import asyncio

    checked = await asyncio.to_thread(_check_hosts, list(targets), context)
    async with _pinned(checked) as pins:
        yield pins


def _check_urls(urls: Iterable[str], context: str) -> dict[str, list[str]]:
    checked: dict[str, list[str]] = {}
    for url in urls:
        _merge(checked, urlsplit(url).hostname or "", assert_source_url(url, context=context))
    return checked


@contextlib.asynccontextmanager
async def pin_source_urls(urls: Iterable[str], *, context: str) -> AsyncIterator[EgressPins]:
    """:func:`pin_source_hosts` for SDKs configured with endpoint URLs."""
    import asyncio

    checked = await asyncio.to_thread(_check_urls, list(urls), context)
    async with _pinned(checked) as pins:
        yield pins


@contextlib.contextmanager
def pin_source_urls_sync(urls: Iterable[str], *, context: str) -> Iterator[EgressPins]:
    """Synchronous :func:`pin_source_urls`, for code already off the event loop."""
    tokens = _register(checked := _check_urls(list(urls), context))
    try:
        yield EgressPins(ips=checked)
    finally:
        _release(tokens)


_MONGODB_SRV_TXT_OPTIONS = frozenset({"authsource", "replicaset", "loadbalanced"})


def _srv_txt_options(name: str) -> list[tuple[str, str]]:
    """The URI options a ``mongodb+srv`` TXT record contributes (pymongo's subset)."""
    try:
        import dns.resolver

        answers = dns.resolver.resolve(name, "TXT")
    except Exception:
        return []  # no TXT record is normal
    options: list[tuple[str, str]] = []
    for answer in answers:
        text = b"".join(getattr(answer, "strings", [])).decode("utf-8", errors="replace")
        for key, value in parse_qsl(text, keep_blank_values=True):
            if key.lower() in _MONGODB_SRV_TXT_OPTIONS:
                options.append((key, value))
    return options


def _expand_mongodb_srv(uri: str, context: str) -> tuple[str, list[tuple[str, int]]]:
    """Rewrite ``mongodb+srv://name/...`` as a seed-list ``mongodb://`` URI.

    The driver would otherwise run its own SRV query, and a hostile DNS answer
    could name different (internal) targets than the ones checked here. SRV
    semantics are kept: TLS on unless the URI says otherwise, the TXT record's
    options (URI options win), and pymongo's rule that every target must sit
    under the SRV name's parent domain.
    """
    parts = urlsplit(uri)
    userinfo, _, name = parts.netloc.rpartition("@")
    name = unquote(name).strip().lower().rstrip(".")
    if not name or "," in name or ":" in name:
        raise ConnectorEgressBlockedError(
            f"SSRF guard [{context}]: a mongodb+srv URI names exactly one host, no port"
        )
    parent = name.split(".", 1)[1] if name.count(".") >= 2 else name
    records = _srv_records(name)
    if not records:
        raise ConnectorEgressBlockedError(
            f"SSRF guard [{context}]: SRV record for {name!r} names no hosts — blocked"
        )
    for host, _port in records:
        if not (host.lower() == parent or host.lower().endswith("." + parent)):
            raise ConnectorEgressBlockedError(
                f"SSRF guard [{context}]: SRV target {host!r} is outside {parent!r} — blocked"
            )
    query = parse_qsl(parts.query, keep_blank_values=True)
    present = {k.lower() for k, _ in query}
    query.extend((k, v) for k, v in _srv_txt_options(name) if k.lower() not in present)
    if not present & {"tls", "ssl"}:
        query.append(("tls", "true"))
    seeds = ",".join(f"{_netloc_host(h)}:{p}" for h, p in records)
    netloc = f"{userinfo}@{seeds}" if userinfo else seeds
    expanded = urlunsplit(("mongodb", netloc, parts.path or "/", urlencode(query), ""))
    return expanded, records


def _check_dsn(dsn: str, context: str) -> tuple[str, dict[str, list[str]]]:
    text = str(dsn or "").strip()
    if not text:
        raise ConnectorEgressBlockedError(f"SSRF guard [{context}]: empty DSN blocked")
    if text.lower().startswith("mongodb+srv://"):
        text, records = _expand_mongodb_srv(text, context)
        return text, _check_hosts(records, context)
    return text, _check_hosts(_dsn_hosts(text), context)


def hold_source_dsn_pins(dsn: object, *, context: str) -> tuple[EgressPins, Callable[[], None]]:
    """Check every host a DSN would dial and pin them until ``release()`` is called.

    For a driver client that outlives one call (a pooled MongoClient, C2): its
    monitor threads resolve the hosts for the client's whole life, so the pins
    must too. Blocking (DNS): call it off the event loop. ``release`` is
    idempotent.
    """
    text, checked = _check_dsn(str(dsn or ""), context)
    tokens = _register(checked)
    released = threading.Event()

    def _release_once() -> None:
        if not released.is_set():
            released.set()
            _release(tokens)

    return EgressPins(ips=checked, dsn=text), _release_once


@contextlib.asynccontextmanager
async def pin_source_dsn(dsn: object, *, context: str) -> AsyncIterator[EgressPins]:
    """Check every host a DSN would dial and pin them for the block.

    Hand the driver ``pins.dsn`` — for ``mongodb+srv`` that is the expanded
    seed-list URI of the checked SRV targets, so the driver runs no SRV query of
    its own. For drivers that bypass ``socket.getaddrinfo`` use
    :func:`dsn_with_pinned_hosts`.
    """
    import asyncio

    text, checked = await asyncio.to_thread(_check_dsn, str(dsn or ""), context)
    async with _pinned(checked, dsn=text) as pins:
        yield pins


def _netloc_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def dsn_with_pinned_hosts(dsn: str, pins: EgressPins) -> str:
    """``dsn`` (a URI) with every host replaced by its pinned address.

    For drivers whose connect path never calls ``socket.getaddrinfo`` (asyncpg
    under uvloop resolves in libuv). Pair it with :func:`pinned_hostname_ssl`
    when the DSN asks for certificate hostname verification.
    """
    parts = urlsplit(dsn)
    userinfo, sep, hostlist = parts.netloc.rpartition("@")
    rebuilt: list[str] = []
    for raw_item in hostlist.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if item.startswith("["):
            host, _, rest = item[1:].partition("]")
            port = rest.lstrip(":")
        else:
            host, _, port = item.partition(":")
            host = unquote(host)
        rebuilt.append(_netloc_host(pins.ip(host)) + (f":{port}" if port else ""))
    netloc = f"{userinfo}{sep}{','.join(rebuilt)}"
    query = [
        (k, ",".join(pins.ip(h) for h in v.split(",")) if k.lower() in ("host", "hostaddr") else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))


class _PinnedHostnameSSLContext(ssl.SSLContext):
    """TLS context that verifies the certificate against the *original* hostname
    (and sends it as SNI) while the socket is connected to a pinned IP."""

    _ip_to_host: dict[str, str]

    def _host_for(self, server_hostname: Any) -> Any:
        return self._ip_to_host.get(str(server_hostname), server_hostname)

    def wrap_bio(  # type: ignore[override]
        self,
        incoming: Any,
        outgoing: Any,
        server_side: bool = False,
        server_hostname: Any = None,
        session: Any = None,
    ) -> Any:
        return super().wrap_bio(
            incoming, outgoing, server_side, self._host_for(server_hostname), session
        )

    def wrap_socket(  # type: ignore[override]
        self,
        sock: Any,
        server_side: bool = False,
        do_handshake_on_connect: bool = True,
        suppress_ragged_eofs: bool = True,
        server_hostname: Any = None,
        session: Any = None,
    ) -> Any:
        return super().wrap_socket(
            sock,
            server_side,
            do_handshake_on_connect,
            suppress_ragged_eofs,
            self._host_for(server_hostname),
            session,
        )


def pinned_hostname_ssl(pins: EgressPins, *, cafile: str | None = None) -> ssl.SSLContext:
    """A verifying TLS client context for connections dialled to pinned IPs.

    Certificates are still checked against the hostname the tenant configured,
    never against the IP the socket was pinned to.
    """
    ctx = _PinnedHostnameSSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx._ip_to_host = {ips[0]: host for host, ips in pins.ips.items() if ips}
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if cafile:
        ctx.load_verify_locations(cafile=cafile)
    else:
        ctx.load_default_certs()
    return ctx


def require_pinnable_driver(driver: str, *, context: str) -> None:
    """Refuse a driver that resolves hosts outside Python while strict pinning is on.

    Such a driver (librdkafka) does its own DNS lookups — including for the
    broker addresses the cluster advertises — so a checked name could be
    re-resolved to an internal address. Operators can accept that risk by
    setting ``INGESTION_EGRESS_STRICT_PINNING=false``.
    """
    try:
        from app.core.config import get_settings

        strict = bool(getattr(get_settings(), "ingestion_egress_strict_pinning", True))
    except Exception:  # pragma: no cover - settings unavailable: fail closed
        strict = True
    if strict:
        raise ConnectorEgressBlockedError(
            f"SSRF guard [{context}]: {driver} resolves hosts itself, so its connections "
            "cannot be pinned to checked addresses (DNS rebinding); refused while "
            "INGESTION_EGRESS_STRICT_PINNING is on"
        )
