from __future__ import annotations

import enum


class NetworkMode(str, enum.Enum):
    NONE = "none"
    ALLOWLIST = "allowlist"
    TENANT_CONNECTORS_ONLY = "tenant_connectors_only"


class NetworkPolicy:
    def __init__(
        self,
        mode: NetworkMode = NetworkMode.NONE,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        self._mode = mode
        self._allowed = set(allowed_hosts or [])

    def is_allowed(self, url: str) -> bool:
        if self._mode == NetworkMode.NONE:
            return False
        if self._mode == NetworkMode.ALLOWLIST:
            return any(h in url for h in self._allowed)
        return True
