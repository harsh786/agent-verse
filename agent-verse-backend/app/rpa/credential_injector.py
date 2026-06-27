"""Resolve vault:// references in RPA tool arguments from tenant secret store.

P1.2: Auto-fill credentials referenced as ``vault://<server_id>/<key>`` so that
RPA steps never contain plaintext secrets in the agent plan.
"""
from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)
VAULT_PREFIX = "vault://"


class CredentialInjector:
    """Auto-fill vault:// references in RPA arguments from the tenant secret store."""

    def __init__(
        self,
        secret_store: Any = None,
        vault: Any = None,
        tenant_id: str = "",
    ) -> None:
        self._secret_store = secret_store
        self._vault = vault
        self._tenant_id = tenant_id

    def is_vault_ref(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(VAULT_PREFIX)

    async def resolve(self, credential_ref: str) -> str:
        """Resolve a vault:// reference to its plaintext value."""
        if not self.is_vault_ref(credential_ref):
            return credential_ref

        secret_path = credential_ref[len(VAULT_PREFIX):]

        # Try secret store first (tenant-scoped Redis-encrypted)
        if self._secret_store is not None:
            try:
                from app.tenancy.context import PlanTier, TenantContext
                fake_ctx = TenantContext(
                    tenant_id=self._tenant_id,
                    plan=PlanTier.PROFESSIONAL,
                    api_key_id="rpa-injector",
                )
                parts = secret_path.split("/")
                if len(parts) >= 2:
                    server_id = parts[0]
                    key = parts[-1]
                    ref = f"vault://connectors/{server_id}/{key}"
                    val = await self._secret_store.get_secret(ref, tenant_ctx=fake_ctx)
                    if val:
                        logger.info("rpa_credential_resolved_from_store", path=secret_path[:30])
                        return val
            except Exception as exc:
                logger.warning("rpa_credential_store_lookup_failed", error=str(exc))

        # Try vault directly
        if self._vault is not None:
            try:
                val = await self._vault.get_secret(secret_path)
                if val:
                    logger.info("rpa_credential_resolved_from_vault", path=secret_path[:30])
                    return val
            except Exception as exc:
                logger.warning("rpa_credential_vault_lookup_failed", error=str(exc))

        logger.warning("rpa_credential_unresolved", path=secret_path[:30])
        return credential_ref  # Return original ref if unresolvable

    async def resolve_arguments(self, arguments: dict) -> dict:
        """Resolve all vault:// refs in an arguments dict (recursive)."""
        resolved = {}
        for k, v in arguments.items():
            if self.is_vault_ref(v):
                resolved[k] = await self.resolve(v)
            elif isinstance(v, dict):
                resolved[k] = await self.resolve_arguments(v)
            else:
                resolved[k] = v
        return resolved
