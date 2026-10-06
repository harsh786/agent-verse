"""Tenant-owned SMTP sender: egress policy, connection test and delivery.

The host a tenant configures goes through the SSRF guard with the platform's
private-network policy (:func:`app.net.ssrf_guard.private_access_networks`):
cloud metadata, link-local and 0.0.0.0 are never reachable, and private
addresses only while ``ALLOW_PRIVATE_NETWORK_ACCESS`` is on. The socket is then
opened to an address that was checked (not to a fresh DNS answer), and TLS is
verified against the configured host name. Only the ports in
``TENANT_SMTP_ALLOWED_PORTS`` are allowed, so the test endpoint cannot probe
arbitrary services.

Errors returned to the tenant name the failing stage (and an SMTP reply code
when there is one), never the credentials.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import ssl
from dataclasses import dataclass
from email.message import Message
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

DEFAULT_TIMEOUT_S = 15.0
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?$")


class SMTPConfigError(ValueError):
    """The tenant SMTP settings are not acceptable (422)."""


class SMTPEgressRefusedError(SMTPConfigError):
    """The host is refused by the network egress policy."""


@dataclass(frozen=True)
class SMTPTarget:
    host: str
    port: int
    tls_mode: str  # "starttls" | "tls" | "none"
    username: str
    from_address: str


@dataclass(frozen=True)
class SMTPOutcome:
    ok: bool
    stage: str  # "policy" | "connect" | "tls" | "auth" | "send" | "done"
    message: str
    code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "stage": self.stage, "message": self.message}
        if self.code is not None:
            out["smtp_code"] = self.code
        return out


def allowed_ports() -> frozenset[int]:
    from app.core.config import get_settings

    raw = str(getattr(get_settings(), "tenant_smtp_allowed_ports", "") or "")
    ports = {int(p) for p in (x.strip() for x in raw.split(",")) if p.isdigit()}
    return frozenset(p for p in ports if 0 < p < 65536)


def _production() -> bool:
    return os.getenv("ENVIRONMENT", "development").strip().lower() == "production"


def validate_target(target: SMTPTarget) -> None:
    """Static checks (no DNS). Raises :class:`SMTPConfigError`."""
    host = target.host.strip().lower().strip(".")
    if not host or host != target.host:
        raise SMTPConfigError("host must be a bare host name or IP address (lower case)")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOST_RE.match(host):
            raise SMTPConfigError("host must be a host name or IP address") from None
    if target.port not in allowed_ports():
        raise SMTPConfigError(
            f"port {target.port} is not allowed; use one of {sorted(allowed_ports())}"
        )
    if target.tls_mode not in ("starttls", "tls", "none"):
        raise SMTPConfigError("tls_mode must be starttls, tls or none")
    if target.tls_mode == "none" and _production():
        raise SMTPConfigError("tls_mode 'none' (no encryption) is refused in production")


def check_host(host: str) -> list[str]:
    """Resolve *host* and validate every address against the egress policy;
    returns the checked addresses. Raises :class:`SMTPEgressRefusedError`."""
    from app.net.ssrf_guard import SSRFError, private_access_networks, resolve_and_check_host

    try:
        return resolve_and_check_host(host, allowed_networks=private_access_networks())
    except SSRFError as exc:
        raise SMTPEgressRefusedError(
            f"SMTP host {host!r} is refused by the network egress policy"
        ) from exc


async def _open_socket(ips: list[str], port: int, timeout: float) -> socket.socket:
    last: Exception | None = None
    for ip in ips:
        try:
            return await asyncio.to_thread(socket.create_connection, (ip, port), timeout)
        except OSError as exc:
            last = exc
    raise ConnectionError(f"could not connect: {type(last).__name__ if last else 'no address'}")


def _code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return int(code) if isinstance(code, int) and code > 0 else None


def _classify(exc: BaseException) -> SMTPOutcome:
    import aiosmtplib

    if isinstance(exc, SMTPEgressRefusedError):
        return SMTPOutcome(False, "policy", str(exc))
    if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
        return SMTPOutcome(False, "auth", "authentication failed", _code(exc))
    if isinstance(exc, ssl.SSLError):
        return SMTPOutcome(False, "tls", "TLS handshake or certificate verification failed")
    if isinstance(exc, aiosmtplib.SMTPNotSupported):
        return SMTPOutcome(False, "tls", "the server does not support a required extension")
    if isinstance(exc, (aiosmtplib.SMTPConnectTimeoutError, TimeoutError)):
        return SMTPOutcome(False, "connect", "timed out")
    if isinstance(exc, (aiosmtplib.SMTPConnectError, ConnectionError, OSError)):
        return SMTPOutcome(False, "connect", "could not connect to the SMTP server")
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        return SMTPOutcome(False, "send", "the server refused the recipients")
    if isinstance(exc, aiosmtplib.SMTPResponseException):
        return SMTPOutcome(False, "send", "the server refused the message", _code(exc))
    return SMTPOutcome(False, "send", "SMTP error")


async def _connected_client(target: SMTPTarget, secret: str | None, timeout: float) -> Any:
    import aiosmtplib

    ips = await asyncio.to_thread(check_host, target.host)
    sock = await _open_socket(ips, target.port, timeout)
    client = aiosmtplib.SMTP(
        hostname=target.host,  # TLS SNI / certificate name; the socket is pinned
        sock=sock,
        use_tls=target.tls_mode == "tls",
        start_tls=target.tls_mode == "starttls",
        username=target.username or None,
        password=(secret or None) if target.username else None,
        timeout=timeout,
    )
    try:
        await client.connect()
    except BaseException:
        sock.close()
        raise
    return client


async def probe(
    target: SMTPTarget,
    secret: str | None,
    *,
    message: Message | None = None,
    recipients: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> SMTPOutcome:
    """Connect, (STARTTLS / TLS), authenticate and optionally send *message*."""
    try:
        validate_target(target)
        client = await _connected_client(target, secret, timeout)
    except SMTPConfigError as exc:
        if isinstance(exc, SMTPEgressRefusedError):
            return _classify(exc)
        return SMTPOutcome(False, "policy", str(exc))
    except Exception as exc:
        return _classify(exc)
    try:
        if message is not None:
            await client.send_message(
                message, sender=target.from_address, recipients=recipients or []
            )
        else:
            await client.noop()
    except Exception as exc:
        return _classify(exc)
    finally:
        try:
            await client.quit()
        except Exception:
            client.close()
    return SMTPOutcome(True, "done", "test message sent" if message is not None else "connected")


async def deliver(
    target: SMTPTarget,
    secret: str | None,
    message: Message,
    recipients: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> SMTPOutcome:
    """Send *message* through the tenant SMTP server."""
    return await probe(target, secret, message=message, recipients=recipients, timeout=timeout)
