"""Generic HTTP request tool for calling arbitrary APIs."""

from __future__ import annotations

import contextlib
import ipaddress
import json
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "169.254.169.254",  # AWS metadata
        "metadata.google.internal",  # GCP metadata
        "100.100.100.200",  # Alibaba Cloud metadata
    }
)

_MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB


_MAX_REDIRECTS = 5


def _is_blocked(url: str) -> bool:
    """Block requests to internal/private/metadata endpoints (SSRF protection).

    Literal checks first, then the DNS-resolving guard (app.net.ssrf_guard): a
    hostname that RESOLVES to 169.254.169.254 / 10.x / 127.x used to pass,
    because only the literal host string was inspected. The agent-facing
    ``http_request`` tool is reachable by prompt injection, so this matters.
    """
    if _is_blocked_literal(url):
        return True
    try:
        from app.net.ssrf_guard import assert_public_url

        assert_public_url(url, context="http_request tool")
    except Exception:
        return True
    return False


def _is_blocked_literal(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return True

        # ALLOW_PRIVATE_NETWORK_ACCESS (default on): localhost, RFC-1918 and
        # *.internal / *.local hosts are reachable; only cloud-metadata and the
        # other never-reachable addresses are refused here (the DNS-resolving
        # guard in _is_blocked applies the same policy to what a name resolves to).
        from app.net.ssrf_guard import is_metadata_host, private_network_access_enabled

        if private_network_access_enabled():
            return is_metadata_host(host)

        # Check exact known-bad hostnames
        if host.lower() in _BLOCKED_HOSTS:
            return True

        # Check if it's a valid IP address
        try:
            addr = ipaddress.ip_address(host)
            # Block all private, loopback, link-local, reserved, and multicast addresses
            return (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_multicast
            )
        except ValueError:
            pass  # Not an IP address — hostname, continue

        # Block common internal hostname patterns
        lower_host = host.lower()
        return bool(lower_host.endswith(".internal") or lower_host.endswith(".local"))
    except Exception:
        return True  # Block on parse error (fail-safe)


class HttpRequestTool:
    """Make HTTP requests to external APIs.

    Security: blocks requests to localhost, metadata endpoints, RFC-1918 ranges.
    """

    name = "http_request"
    description = "Make HTTP requests (GET/POST/PUT/DELETE) to external APIs."
    _DEFAULT_TIMEOUT = 30.0

    async def execute(
        self,
        *,
        url: str,
        method: HttpMethod = "GET",
        headers: dict[str, str] | None = None,
        body: dict | str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if _is_blocked(url):
            return {"error": "Blocked: requests to internal addresses are not permitted."}

        _timeout = min(timeout or self._DEFAULT_TIMEOUT, 60.0)
        _headers = dict(headers or {})
        _headers.setdefault("User-Agent", "AgentVerse/1.0")

        try:
            # Redirects are followed manually so EVERY hop is re-validated: with
            # follow_redirects=True a public URL could 302 to an internal address.
            # The client pins each connection to an IP validated at connect time:
            # checking the URL and then letting a plain client re-resolve DNS left a
            # rebinding window (public at check, 169.254.169.254 at connect).
            from app.net.ssrf_guard import public_async_client

            async with public_async_client(timeout=_timeout) as client:
                send_kwargs: dict[str, Any] = {"headers": _headers}
                if body is not None:
                    if isinstance(body, dict):
                        send_kwargs["json"] = body
                    else:
                        send_kwargs["content"] = str(body).encode()

                current_url, current_method = url, method
                for _hop in range(_MAX_REDIRECTS + 1):
                    resp = await client.request(current_method, current_url, **send_kwargs)
                    if not resp.is_redirect:
                        break
                    location = resp.headers.get("location", "")
                    next_url = str(resp.url.join(location)) if location else ""
                    if not next_url or _is_blocked(next_url):
                        return {"error": "Blocked: redirect to an internal address."}
                    if resp.status_code in (301, 302, 303) and current_method != "GET":
                        current_method, send_kwargs = "GET", {"headers": _headers}
                    current_url = next_url
                else:
                    return {"error": f"Too many redirects (>{_MAX_REDIRECTS})"}

                content = resp.content[:_MAX_RESPONSE_BYTES]
                try:
                    body_text = content.decode("utf-8", errors="replace")
                except Exception:
                    body_text = "<binary>"

                # Try to parse JSON
                parsed: Any = None
                if "application/json" in resp.headers.get("content-type", ""):
                    with contextlib.suppress(Exception):
                        parsed = json.loads(body_text)

                return {
                    "status_code": resp.status_code,
                    "ok": resp.is_success,
                    "headers": dict(resp.headers),
                    "body": parsed if parsed is not None else body_text,
                    "truncated": len(resp.content) > _MAX_RESPONSE_BYTES,
                }
        except httpx.TimeoutException:
            return {"error": f"Request timed out after {_timeout}s"}
        except Exception as exc:
            logger.warning("http_request_failed", url=url, error=str(exc))
            return {"error": str(exc)}

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                        "default": "GET",
                    },
                    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "body": {"description": "Request body (object for JSON, string for raw)"},
                    "timeout": {"type": "number", "description": "Timeout in seconds (max 60)"},
                },
                "required": ["url"],
            },
        }
