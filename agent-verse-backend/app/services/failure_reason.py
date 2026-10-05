"""Tenant-facing failure reason of a goal (NF-14).

``GET /goals/{id}`` did not expose why a goal failed: the reason was only in the
goal row (``error_message``) and in a ``worker_failed`` / ``goal_failed`` event.
The API now returns it as ``failure_reason`` (plus a short ``terminal_reason``
code) — sanitized first: credentials are redacted (the shared event redactor)
and anything naming infrastructure (URLs / DSNs, IP addresses, ``host:port``,
fully-qualified internal host names) is replaced, because driver and provider
errors routinely embed them.
"""

from __future__ import annotations

import re

_MAX_LEN = 500

_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>()]+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_IPV6 = re.compile(r"\[?\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b\]?(?::\d{1,5})?")
# user:pass@host style fragments without a scheme
_USERINFO_HOST = re.compile(r"\b[\w.%+-]+:[^\s@/]+@[\w.-]+(?::\d{1,5})?")
_HOST_PORT = re.compile(r"\b(?=[\w.-]*[A-Za-z])[\w-]+(?:\.[\w-]+)*:\d{2,5}\b")
_FQDN = re.compile(r"\b(?:[a-zA-Z0-9-]+\.){2,}[a-zA-Z]{2,}\b")
# a python tuple address: ('db.internal', 5432)
_TUPLE_ADDR = re.compile(r"\(\s*'[^']*'\s*,\s*\d{1,5}\s*\)")

_TERMINAL = {"failed", "cancelled"}


def public_failure_reason(text: object) -> str | None:
    """The sanitized, bounded failure text, or ``None`` when there is none."""
    if text is None:
        return None
    from app.agent.sanitization import redact_sensitive_text

    out = redact_sensitive_text(str(text)).strip()
    if not out:
        return None
    out = _URL.sub("[url]", out)
    out = _USERINFO_HOST.sub("[host]", out)
    out = _TUPLE_ADDR.sub("[host]", out)
    out = _IPV4.sub("[host]", out)
    out = _IPV6.sub("[host]", out)
    out = _HOST_PORT.sub("[host]", out)
    out = _FQDN.sub("[host]", out)
    if len(out) > _MAX_LEN:
        out = out[: _MAX_LEN - 1].rstrip() + "…"
    return out


def terminal_reason_code(status: str, text: object) -> str | None:
    """A short machine-readable code for why a terminal goal ended (else ``None``)."""
    if status not in _TERMINAL:
        return None
    msg = str(text or "").strip().lower()
    if msg.startswith("blocked by emergency stop"):
        return "emergency_stop"
    if status == "cancelled":
        return "cancelled"
    if msg.startswith("approval expired"):
        return "approval_expired"
    if msg.startswith("goal runner lost"):
        return "runner_lost"
    if msg.startswith("dead lettered"):
        return "dead_lettered"
    if "timed out" in msg or msg.startswith("timeouterror"):
        return "timeout"
    if "no llm provider configured" in msg or "vault key mismatch" in msg:
        return "provider_unavailable"
    if "permissionerror" in msg or "requires human approval" in msg:
        return "governance_denied"
    return "error"
