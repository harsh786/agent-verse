from __future__ import annotations


class LegalHoldPolicy:
    def __init__(self) -> None:
        self._holds: dict[tuple[str, str], str] = {}

    def place_hold(self, tenant_id: str, record_id: str, reason: str) -> None:
        self._holds[(tenant_id, record_id)] = reason

    def release_hold(self, tenant_id: str, record_id: str) -> None:
        self._holds.pop((tenant_id, record_id), None)

    def has_hold(self, tenant_id: str, record_id: str) -> bool:
        return (tenant_id, record_id) in self._holds
