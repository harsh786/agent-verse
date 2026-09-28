"""Resolve vault:// references in RPA tool arguments from tenant secret store.

P1.2: Auto-fill credentials referenced as ``vault://<server_id>/<key>`` so that
RPA steps never contain plaintext secrets in the agent plan.

Fail-closed contract: a reference that cannot be resolved raises
:class:`CredentialResolutionError`. The old behaviour returned the raw
``vault://...`` string, so a login step would type the reference itself into a
password field and carry on "successfully". The lookup previously called a
``get_secret`` method no secret store implements, so *every* reference was
silently left unresolved; it now uses the tenant-aware ``resolve`` API of the
connector secret store (``RedisConnectorSecretStore``).
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)
VAULT_PREFIX = "vault://"


class CredentialResolutionError(RuntimeError):
    """A ``vault://`` reference in RPA arguments could not be resolved."""


def contains_vault_ref(value: Any) -> bool:
    """True if ``value`` (recursively, for dicts/lists) holds a ``vault://`` string."""
    if isinstance(value, str):
        return value.startswith(VAULT_PREFIX)
    if isinstance(value, dict):
        return any(contains_vault_ref(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(contains_vault_ref(v) for v in value)
    return False


class CredentialInjector:
    """Auto-fill vault:// references in RPA arguments from the tenant secret store."""

    def __init__(
        self,
        secret_store: Any = None,
        vault: Any = None,
        tenant_id: str = "",
    ) -> None:
        self._secret_store = secret_store
        self._vault = vault  # retained for API compatibility; not a lookup source
        self._tenant_id = tenant_id

    def is_vault_ref(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(VAULT_PREFIX)

    async def resolve(self, credential_ref: str) -> str:
        """Resolve a vault:// reference to its plaintext value (fail closed)."""
        if not self.is_vault_ref(credential_ref):
            return credential_ref

        secret_path = credential_ref[len(VAULT_PREFIX) :]
        if self._secret_store is None:
            raise CredentialResolutionError(
                "vault:// reference in RPA arguments but no secret store is configured"
            )
        if not self._tenant_id:
            raise CredentialResolutionError("vault:// reference resolved without a tenant")

        parts = [p for p in secret_path.split("/") if p]
        if parts and parts[0] == "connectors":
            parts = parts[1:]
        if len(parts) < 2:
            raise CredentialResolutionError(
                "malformed credential reference (want vault://<server>/<key>): "
                f"{secret_path[:40]!r}"
            )
        server_id, key = parts[0], parts[-1]
        ref = f"vault://connectors/{server_id}/{key}"

        from app.providers.vault import resolve_connector_secret_ref_for_tenant
        from app.tenancy.context import PlanTier, TenantContext

        tenant_ctx = TenantContext(
            tenant_id=self._tenant_id, plan=PlanTier.FREE, api_key_id="rpa-injector"
        )
        try:
            val = await resolve_connector_secret_ref_for_tenant(
                ref, store=self._secret_store, tenant_ctx=tenant_ctx
            )
        except Exception as exc:
            raise CredentialResolutionError(f"secret store lookup failed: {exc}") from exc
        if not val:
            logger.warning("rpa_credential_unresolved", path=secret_path[:30])
            raise CredentialResolutionError(f"credential not found: {secret_path[:40]!r}")
        logger.info("rpa_credential_resolved_from_store", path=secret_path[:30])
        return val

    async def resolve_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve all vault:// refs in an arguments dict (recursive)."""
        resolved: dict[str, Any] = {}
        for k, v in arguments.items():
            if self.is_vault_ref(v):
                resolved[k] = await self.resolve(v)
            elif isinstance(v, dict):
                resolved[k] = await self.resolve_arguments(v)
            else:
                resolved[k] = v
        return resolved
