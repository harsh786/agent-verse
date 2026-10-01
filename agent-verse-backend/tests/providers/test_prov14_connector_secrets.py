"""PROV-14: connector secrets never fall back to a per-process dict.

With no store/resolver passed, connector secrets were stored and resolved in a
module-level dict: a secret saved on one replica could not be resolved on
another, and connector auth silently sent an empty value.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.providers import vault
from app.providers.vault import (
    ConnectorSecretUnavailableError,
    resolve_connector_secret_ref,
    resolve_connector_secret_ref_for_tenant,
    store_connector_secret,
    store_connector_secret_for_tenant,
)

REF = "vault://connectors/srv-1/token"


def test_there_is_no_process_global_secret_dict() -> None:
    assert not hasattr(vault, "_CONNECTOR_SECRET_STORE")


def test_storing_without_a_store_is_refused() -> None:
    with pytest.raises(ConnectorSecretUnavailableError):
        store_connector_secret(REF, "s3cret")


def test_resolving_an_unknown_ref_raises() -> None:
    with pytest.raises(ConnectorSecretUnavailableError):
        resolve_connector_secret_ref(REF, store={})
    with pytest.raises(ConnectorSecretUnavailableError):
        resolve_connector_secret_ref(REF)


def test_mapping_store_round_trip() -> None:
    store: dict[str, str] = {}
    store_connector_secret(REF, "s3cret", store=store)
    assert resolve_connector_secret_ref(REF, store=store) == "s3cret"


async def test_tenant_store_missing_secret_raises() -> None:
    class _Store:
        async def resolve(self, ref: str, *, tenant_ctx: Any = None) -> str | None:
            return None

        async def store(self, ref: str, value: str, *, tenant_ctx: Any = None) -> None:
            return None

    tenant = SimpleNamespace(tenant_id="t1")
    with pytest.raises(ConnectorSecretUnavailableError):
        await resolve_connector_secret_ref_for_tenant(REF, store=_Store(), tenant_ctx=tenant)
    with pytest.raises(ConnectorSecretUnavailableError):
        await store_connector_secret_for_tenant(REF, "x", store=None, tenant_ctx=tenant)


async def test_connector_auth_never_sends_an_empty_secret() -> None:
    from app.api.connectors import _resolve_auth_value

    with pytest.raises(ConnectorSecretUnavailableError):
        await _resolve_auth_value(REF, None)

    async def _missing(ref: str) -> None:
        return None

    with pytest.raises(ConnectorSecretUnavailableError):
        await _resolve_auth_value(REF, _missing)


async def test_mcp_client_auth_never_sends_an_empty_secret() -> None:
    from app.mcp.client import MCPClient

    client = MCPClient(registry=SimpleNamespace(), secret_resolver=lambda ref: None)
    with pytest.raises(ConnectorSecretUnavailableError):
        await client._resolve_auth_value(REF, None)
