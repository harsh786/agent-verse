"""Redis-backed IP allowlist enforcement.

Cache key:  ip_wl:{tenant_id}
Value:      JSON list of CIDR strings
TTL:        60 seconds

Empty list = no allowlist configured = all IPs permitted.
Loopback addresses (127.x.x.x, ::1) are always allowed.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any


class IPAllowlistUnavailableError(Exception):
    """The tenant's allowlist could not be read (DB error).

    Raised instead of returning ``[]``: an empty list means "no allowlist", so
    swallowing a DB error used to turn every configured allowlist off for the
    duration of the outage (fail-open). Callers decide how to fail closed.
    """


class IPAllowlistCache:
    """CIDR allowlist lookup: Redis cache (60-second TTL) over the DB.

    ``redis`` may be ``None`` (no Redis wired): every lookup then goes to the
    DB. A Redis error is treated as a cache miss. A DB error raises
    :class:`IPAllowlistUnavailableError` — it never reads as "no allowlist".
    """

    TTL = 60  # seconds
    PREFIX = "ip_wl:"

    def __init__(self, redis: Any | None) -> None:
        self._r = redis

    def _key(self, tenant_id: str) -> str:
        return f"{self.PREFIX}{tenant_id}"

    async def get_cidrs(
        self,
        tenant_id: str,
        db_factory: Any = None,
    ) -> list[str]:
        """Return active CIDR list for the tenant.

        Priority:
          1. Redis cache (TTL=60 s) when Redis is available
          2. DB query → populate Redis cache
          3. ``[]`` only when there is no DB at all (in-memory mode: no
             allowlist can have been stored)

        Raises:
            IPAllowlistUnavailableError: the DB lookup failed.
        """
        import logging

        log = logging.getLogger(__name__)
        if self._r is not None:
            try:
                cached = await self._r.get(self._key(tenant_id))
            except Exception as exc:
                log.warning("ip_allowlist_cache_read_failed tenant=%s: %s", tenant_id, exc)
                cached = None
            if cached is not None:
                return list(json.loads(cached))

        if db_factory is None:
            return []

        try:
            from sqlalchemy import select

            from app.db.models.auth import IPAllowlistEntry
            from app.db.rls import sqlalchemy_rls_context

            # ip_allowlist_entries is FORCE-RLS: without the tenant GUC the
            # NOBYPASSRLS application role reads zero rows, so every allowlist
            # silently allowed all IPs.
            async with (
                db_factory() as db,
                db.begin(),
                sqlalchemy_rls_context(db, tenant_id),
            ):
                result = await db.execute(
                    select(IPAllowlistEntry.cidr).where(
                        IPAllowlistEntry.tenant_id == tenant_id,
                        IPAllowlistEntry.is_active.is_(True),
                    )
                )
                cidrs = [row[0] for row in result.fetchall()]
        except Exception as exc:
            log.warning("ip_allowlist_lookup_failed tenant=%s: %s", tenant_id, exc)
            raise IPAllowlistUnavailableError(str(exc)) from exc

        if self._r is not None:
            try:
                await self._r.setex(self._key(tenant_id), self.TTL, json.dumps(cidrs))
            except Exception as exc:
                log.warning("ip_allowlist_cache_write_failed tenant=%s: %s", tenant_id, exc)
        return cidrs

    async def invalidate(self, tenant_id: str) -> None:
        """Remove the cached allowlist for a tenant."""
        if self._r is not None:
            await self._r.delete(self._key(tenant_id))


def is_ip_allowed(client_ip: str, cidrs: list[str]) -> bool:
    """Return True if ``client_ip`` is permitted by the CIDR allowlist.

    Rules:
      - Empty list → no restrictions, all IPs permitted.
      - Loopback (127.x, ::1) → always permitted.
      - Otherwise → must match at least one CIDR.
      - Malformed IP or CIDR → denied (fail-safe).
    """
    if not cidrs:
        return True

    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    # Loopback is always permitted (dev environments, health probes)
    if addr.is_loopback:
        return True

    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            if addr in network:
                return True
        except ValueError:
            continue  # skip malformed CIDR entries

    return False
