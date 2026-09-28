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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from app.net.ssrf_guard import SSRFError, assert_public_url
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.ingestion.source_config import SourceConfig

_log = get_logger(__name__)

__all__ = [
    "ConnectorEgressBlockedError",
    "assert_source_dsn",
    "assert_source_host",
    "assert_source_url",
    "check_source_dsn",
    "check_source_host",
    "guarded_request",
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


def assert_source_url(url: str, *, context: str, config: SourceConfig | None = None) -> None:
    """Raise :class:`ConnectorEgressBlockedError` unless ``url`` is safe to fetch.

    Call this before *any* outbound request a connector makes to a host derived
    from ``connection_config`` — in ``validate_connection`` as well as
    ``get_delta``, since validation runs against the same attacker-supplied URL
    and returns its own response body/error to the caller.
    """
    del config  # tenant config must never widen the policy; kept for call-site clarity
    if not url:
        raise ConnectorEgressBlockedError(f"SSRF guard [{context}]: empty URL")

    allow_internal, allowed_domains = _operator_allowlist()
    # The allowlist is only honoured when the operator has *also* turned the
    # escape hatch on. Both halves are env-only: an allowlist alone must not be
    # able to punch a hole, and connection_config can never reach either.
    effective_allowlist = allowed_domains if (allow_internal and allowed_domains) else None
    try:
        assert_public_url(url, allowed_domains=effective_allowlist, context=context)
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


def assert_source_host(host: object, port: object = None, *, context: str) -> None:
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
    assert_source_url(f"http://{bracketed}{port_part}/", context=context)


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


def _srv_targets(name: str) -> list[str]:
    """Resolve ``mongodb+srv://name`` to the hosts the driver will actually dial."""
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
    return [str(a.target).rstrip(".") for a in answers]


def assert_source_dsn(dsn: object, *, context: str) -> None:
    """Egress check for a database DSN / connection URI a tenant supplied.

    Validates every host the driver could dial (see :func:`_dsn_hosts`). For
    ``mongodb+srv`` the SRV targets are resolved and each is checked, since the
    SRV name itself says nothing about where the driver will connect.

    Residual risk, stated plainly: database drivers do their own DNS lookup after
    this check, so a rebinding resolver with a ~0 TTL can still race it. Pinning
    the connection to the checked IP is not possible for every driver (TLS SNI /
    SRV); the check closes the direct and literal-IP cases.
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


async def guarded_request(
    client: Any,
    method: str,
    url: str,
    *,
    context: str,
    max_redirects: int = _MAX_REDIRECTS,
    **kwargs: Any,
) -> Any:
    """Send ``method url`` on an ``httpx.AsyncClient`` with every hop egress-checked.

    Redirects are followed manually so each ``Location`` is re-validated before it
    is requested: with ``follow_redirects=True`` a public URL can 302 straight to
    ``169.254.169.254`` past a check on the first URL only. Credentials
    (``auth``, ``Authorization`` / ``Cookie`` headers) are dropped once a redirect
    leaves the original host, so a redirect cannot harvest them either.
    """
    import asyncio

    import httpx

    # Same shape as app.net.ssrf_guard.request_public (DNS off the event loop,
    # every hop re-checked), but through the ingestion policy so the operator
    # on-prem allowlist applies, and with credentials dropped off-host.
    await asyncio.to_thread(assert_source_url, url, context=context)
    current = url
    origin = (urlsplit(url).hostname or "").lower()
    for _hop in range(max_redirects + 1):
        response = await client.request(method, current, follow_redirects=False, **kwargs)
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        if not location:
            return response
        nxt = str(response.url.join(location))
        await asyncio.to_thread(assert_source_url, nxt, context=context)
        if (urlsplit(nxt).hostname or "").lower() != origin:
            kwargs.pop("auth", None)
            kwargs["headers"] = {
                k: v
                for k, v in dict(kwargs.get("headers") or {}).items()
                if k.lower() not in ("authorization", "cookie", "proxy-authorization")
            }
        if response.status_code in (301, 302, 303) and method.upper() != "GET":
            method = "GET"
            for body_kw in ("content", "data", "json", "files"):
                kwargs.pop(body_kw, None)
        current = nxt
    raise httpx.TooManyRedirects(f"Exceeded {max_redirects} redirects")


async def check_source_host(host: object, port: object = None, *, context: str) -> None:
    """:func:`assert_source_host` with the DNS lookup off the event loop."""
    import asyncio

    await asyncio.to_thread(assert_source_host, host, port, context=context)


async def check_source_dsn(dsn: object, *, context: str) -> None:
    """:func:`assert_source_dsn` with the DNS lookup off the event loop."""
    import asyncio

    await asyncio.to_thread(assert_source_dsn, dsn, context=context)
