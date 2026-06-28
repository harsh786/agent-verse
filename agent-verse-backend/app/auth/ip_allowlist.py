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


class IPAllowlistCache:
    """Redis-backed CIDR allowlist cache with 60-second TTL.

    Falls back to a DB query on cache miss.  If neither Redis nor DB is
    available the method returns an empty list (fail-open).
    """

    TTL = 60  # seconds
    PREFIX = "ip_wl:"

    def __init__(self, redis: Any) -> None:
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
          1. Redis cache (TTL=60 s)
          2. DB query → populate Redis cache
          3. Return [] if neither is available (fail-open)
        """
        cached = await self._r.get(self._key(tenant_id))
        if cached is not None:
            return json.loads(cached)

        if db_factory is None:
            return []

        try:
            from sqlalchemy import select

            from app.db.models.auth import IPAllowlistEntry

            async with db_factory() as db:
                result = await db.execute(
                    select(IPAllowlistEntry.cidr).where(
                        IPAllowlistEntry.tenant_id == tenant_id,
                        IPAllowlistEntry.is_active.is_(True),
                    )
                )
                cidrs = [row[0] for row in result.fetchall()]

            await self._r.setex(self._key(tenant_id), self.TTL, json.dumps(cidrs))
            return cidrs
        except Exception:
            # Fail-open: allow all IPs when DB is unreachable
            return []

    async def invalidate(self, tenant_id: str) -> None:
        """Remove the cached allowlist for a tenant."""
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
