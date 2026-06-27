"""Generic HTTP request tool for calling arbitrary APIs."""
from __future__ import annotations

import ipaddress
import json
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]

_BLOCKED_HOSTS = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "169.254.169.254",   # AWS metadata
    "metadata.google.internal",  # GCP metadata
    "100.100.100.200",   # Alibaba Cloud metadata
})

_MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB


def _is_blocked(url: str) -> bool:
    """Block requests to internal/private/metadata endpoints (SSRF protection)."""
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return True

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
        if lower_host.endswith(".internal") or lower_host.endswith(".local"):
            return True

        return False
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
            async with httpx.AsyncClient(timeout=_timeout, follow_redirects=True) as client:
                send_kwargs: dict[str, Any] = {"headers": _headers}
                if body is not None:
                    if isinstance(body, dict):
                        send_kwargs["json"] = body
                    else:
                        send_kwargs["content"] = str(body).encode()

                resp = await client.request(method, url, **send_kwargs)

                content = resp.content[:_MAX_RESPONSE_BYTES]
                try:
                    body_text = content.decode("utf-8", errors="replace")
                except Exception:
                    body_text = "<binary>"

                # Try to parse JSON
                parsed: Any = None
                if "application/json" in resp.headers.get("content-type", ""):
                    try:
                        parsed = json.loads(body_text)
                    except Exception:
                        pass

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
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "body": {"description": "Request body (object for JSON, string for raw)"},
                    "timeout": {"type": "number", "description": "Timeout in seconds (max 60)"},
                },
                "required": ["url"],
            },
        }
