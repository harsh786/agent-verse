"""The URL-borne stream token only opens streaming GET endpoints.

It travels in ``?token=`` because EventSource cannot set headers, so it lands in
access logs, proxy logs and browser history. It was documented as read-only but
authenticated ANY method on ANY path (``roles=()`` was the only restraint, and
most endpoints only check for a tenant). The end-to-end sweep in
``tests/e2e_full/test_security_sweep_e2e.py`` proves this against every
operation in the schema; this pins the rule itself.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.tenancy.middleware import _stream_token_allowed


def _req(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "query_string": b"",
                    "headers": [], "scheme": "http", "server": ("t", 80)})


@pytest.mark.parametrize(
    "path",
    ["/goals/abc/stream", "/v1/org/o1/events/stream", "/governance/approvals/stream",
     "/v1/org/o1/events", "/chat/sessions/s1/stream/"],
)
def test_streaming_gets_are_allowed(path: str) -> None:
    assert _stream_token_allowed(_req("GET", path)) is True


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_no_write_method_is_allowed_even_on_a_stream_path(method: str) -> None:
    assert _stream_token_allowed(_req(method, "/goals/abc/stream")) is False


@pytest.mark.parametrize(
    "path", ["/goals", "/goals/abc", "/knowledge/collections", "/governance/audit", "/tenants/me"]
)
def test_non_streaming_reads_are_not_allowed(path: str) -> None:
    assert _stream_token_allowed(_req("GET", path)) is False
