"""Per-tenant recipient allowlist for the agent email tool (owner decision a02-F036-02).

An entry is either a full address (``alice@example.com``), a domain
(``example.com``, also accepted as ``@example.com``) that matches that domain
only, or a wildcard domain (``*.example.com``) that matches its subdomains but
not the apex. Matching is case-insensitive.

An empty allowlist keeps today's behaviour (any valid recipient). A non-empty
one refuses the whole message when ANY recipient is outside it: nothing is
sent to the allowed ones either.
"""

from __future__ import annotations

import re

MAX_ALLOWLIST_ENTRIES = 500
_MAX_ENTRY_LEN = 254

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^(?:{_LABEL}\.)+[a-z]{{2,63}}$")
_LOCAL_RE = re.compile(r"^[a-z0-9._%+\-]+$")


class AllowlistEntryError(ValueError):
    """An allowlist entry is not an address, a domain or a ``*.`` domain."""


def _normalize_entry(raw: str) -> str:
    entry = str(raw).strip().lower()
    if not entry or len(entry) > _MAX_ENTRY_LEN or any(c.isspace() for c in entry):
        raise AllowlistEntryError(f"invalid allowlist entry: {str(raw)[:80]!r}")
    if entry.startswith("@"):
        entry = entry[1:]
    if entry.startswith("*."):
        if not _DOMAIN_RE.match(entry[2:]):
            raise AllowlistEntryError(f"invalid wildcard domain: {str(raw)[:80]!r}")
        return entry
    if "@" in entry:
        local, _, domain = entry.rpartition("@")
        if not local or not _LOCAL_RE.match(local) or not _DOMAIN_RE.match(domain):
            raise AllowlistEntryError(f"invalid email address: {str(raw)[:80]!r}")
        return entry
    if not _DOMAIN_RE.match(entry):
        raise AllowlistEntryError(f"invalid domain: {str(raw)[:80]!r}")
    return entry


def normalize_allowlist(entries: list[str]) -> list[str]:
    """Validated, lower-cased, de-duplicated entries (input order kept).

    Raises :class:`AllowlistEntryError` for a malformed entry or past
    :data:`MAX_ALLOWLIST_ENTRIES`.
    """
    out: list[str] = []
    for raw in entries:
        entry = _normalize_entry(raw)
        if entry not in out:
            out.append(entry)
    if len(out) > MAX_ALLOWLIST_ENTRIES:
        raise AllowlistEntryError(f"at most {MAX_ALLOWLIST_ENTRIES} allowlist entries")
    return out


def recipient_allowed(address: str, allowlist: list[str]) -> bool:
    """Whether *address* matches an entry of a non-empty *allowlist*."""
    addr = address.strip().lower()
    _, at, domain = addr.rpartition("@")
    if not at or not domain:
        return False
    for entry in allowlist:
        if entry.startswith("*."):
            if domain.endswith(entry[1:]):  # ".example.com": subdomains only
                return True
        elif "@" in entry:
            if addr == entry:
                return True
        elif domain == entry:
            return True
    return False


def disallowed_recipients(recipients: list[str], allowlist: list[str]) -> list[str]:
    """Recipients outside *allowlist* (none when the allowlist is empty)."""
    if not allowlist:
        return []
    return [r for r in recipients if not recipient_allowed(r, allowlist)]
