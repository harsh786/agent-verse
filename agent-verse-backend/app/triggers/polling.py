"""HTTP polling helpers for the API_POLL trigger (2.W-1).

The beat loop polls ``poll_url`` on schedule, extracts a value via a minimal
dotted JSONPath (``$.a.b`` / ``a.0.b``), and fires when that value changes (and,
if configured, matches ``poll_expected_value``). ``fetch_json`` is the network
seam tests monkeypatch; ``extract_path`` is pure and unit-tested.
"""

from __future__ import annotations

from typing import Any


def extract_path(obj: Any, path: str) -> Any:
    """Resolve a minimal dotted JSONPath against a decoded JSON object.

    Supports ``$.a.b``, ``a.b``, and numeric list indices (``items.0.id``).
    Returns None if any segment is missing.
    """
    if not path:
        return obj
    cur = obj
    for seg in path.lstrip("$").lstrip(".").split("."):
        if seg == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and seg.isdigit():
            idx = int(seg)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def poll_should_fire(
    current_value: Any, last_value: Any, expected_value: str = ""
) -> bool:
    """Fire when the polled value changed since the last dispatch and — when an
    ``expected_value`` is configured — matches it (string-compared)."""
    changed = str(current_value) != str(last_value) if last_value is not None else True
    if not changed:
        return False
    if expected_value:
        return str(current_value) == expected_value
    return True


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> Any:
    """Fetch a JSON endpoint. Raises on transport/HTTP/JSON error (caller logs)."""
    import httpx

    resp = httpx.request(
        method.upper() or "GET",
        url,
        headers=headers or {},
        json=body or None,
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.json()
