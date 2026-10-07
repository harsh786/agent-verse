"""A connector credential that does not resolve says WHY (workflow tool step report).

Every failure used to read "Could not resolve the credential 'token' for
connector 'gmail'; re-enter the connector's credentials." — also when the value
WAS stored and the process could not decrypt it (a worker on another
VAULT_MASTER_KEY than the API: ``InvalidToken`` has an empty message, so even
the log said nothing), and when the credential store was briefly unreachable.
Re-entering helps in neither case. The causes are now told apart, the message
names VAULT_MASTER_KEY (by fingerprint) for a key mismatch, and a workflow tool
step classifies them: missing / undecryptable are not retried, an unreachable
store is.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

KEY_A = "unit-master-key-A-0123456789"
KEY_B = "unit-master-key-B-9876543210"


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t-creds", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _cfg(handler: Any) -> MCPServerConfig:
    return MCPServerConfig(
        server_id="builtin-gmail:gmail",
        name="gmail",
        url="builtin://gmail",
        builtin_handler=handler,
        auth_config={"token": "vault://connectors/builtin-gmail:gmail/token"},
    )


async def _dispatch(resolver: Any) -> tuple[Any, list[dict[str, Any]]]:
    from app.mcp.client import MCPClient

    calls: list[dict[str, Any]] = []

    async def handler(tool_name: str, args: dict[str, Any], credentials: dict[str, Any]) -> Any:
        calls.append(credentials)
        return {"ok": True}

    client = MCPClient(registry=MCPRegistry(redis=None), secret_resolver=resolver)
    result = await client._dispatch_builtin_tool(_cfg(handler), "gmail_send_message", {}, _ctx())
    return result, calls


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENTVERSE_VAULT_KEY", "AGENTVERSE_VAULT_KEY_FILE", "VAULT_MASTER_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    from app.providers.vault_canary import reset_last_canary_result

    reset_last_canary_result()
    yield
    reset_last_canary_result()


# ── MCP client: one message per cause ───────────────────────────────────────


async def test_resolved_credential_reaches_the_handler() -> None:
    async def resolver(ref: str, tenant_ctx: Any = None) -> str:
        assert tenant_ctx is not None and tenant_ctx.tenant_id == "t-creds"
        return "ya29.tok"

    result, calls = await _dispatch(resolver)
    assert result.success is True
    assert calls[0]["token"] == "ya29.tok"


async def test_missing_credential_asks_to_re_enter_it() -> None:
    from app.providers.vault import ConnectorSecretNotFoundError

    async def resolver(ref: str, tenant_ctx: Any = None) -> str:
        raise ConnectorSecretNotFoundError(f"connector secret {ref!r} could not be resolved")

    result, calls = await _dispatch(resolver)
    assert result.success is False and calls == []
    assert result.error.startswith("Could not resolve the credential 'token' for connector 'gmail'")
    assert "no value is stored" in result.error
    assert "re-enter the connector's credentials" in result.error
    assert result.output["status"] == "credentials_required"


async def test_undecryptable_credential_names_the_vault_key_not_re_enter() -> None:
    from app.providers.vault import ConnectorSecretUndecryptableError

    reason = (
        "vault key mismatch: the vault canary was written with vault key fingerprint aaaa, "
        "but this worker process has bbbb and cannot open it. Set the same VAULT_MASTER_KEY "
        "on the API and every worker and beat process."
    )

    async def resolver(ref: str, tenant_ctx: Any = None) -> str:
        raise ConnectorSecretUndecryptableError(reason)

    result, calls = await _dispatch(resolver)
    assert result.success is False and calls == []
    assert "cannot be decrypted here" in result.error
    assert "VAULT_MASTER_KEY" in result.error
    assert "re-enter the connector's credentials" not in result.error
    assert result.output["status"] == "credentials_undecryptable"


async def test_raw_invalid_token_is_explained_by_fingerprint(vault_key: None) -> None:
    """A store without its own diagnosis (InvalidToken has an EMPTY message)."""
    from cryptography.fernet import InvalidToken

    async def resolver(ref: str, tenant_ctx: Any = None) -> str:
        raise InvalidToken()

    result, _ = await _dispatch(resolver)
    assert result.output["status"] == "credentials_undecryptable"
    assert "VAULT_MASTER_KEY" in result.error
    assert "fingerprint" in result.error
    assert KEY_A not in result.error


async def test_unreachable_store_is_temporary_not_re_enter() -> None:
    async def resolver(ref: str, tenant_ctx: Any = None) -> str:
        raise RuntimeError("connection refused")

    result, calls = await _dispatch(resolver)
    assert result.success is False and calls == []
    assert "unavailable right now" in result.error
    assert "re-enter" not in result.error
    assert result.output is None  # no non-retryable status


def test_workflow_tool_step_classifies_each_cause() -> None:
    from app.mcp.client import ToolCallResult
    from app.workflow.steps.tool_step import tool_failure

    def _err(error: str, status: str | None) -> Any:
        return tool_failure(
            "gmail_send_message",
            ToolCallResult(
                tool_name="gmail_send_message",
                success=False,
                error=error,
                output={"status": status, "error": error} if status else None,
            ),
        )

    missing = _err("Could not resolve ...: no value is stored", "credentials_required")
    assert (missing.failure.kind, missing.failure.retryable) == ("unauthorized", False)
    undecryptable = _err(
        "Could not resolve ...: cannot be decrypted here: vault canary unavailable",
        "credentials_undecryptable",
    )
    assert (undecryptable.failure.kind, undecryptable.failure.retryable) == (
        "configuration",
        False,
    )
    transient = _err(
        "Could not resolve ...: the connector credential store is unavailable right now", None
    )
    assert transient.failure.retryable is True


# ── the durable store: stored-but-undecryptable vs missing vs unavailable ───


class _Store:
    """DurableConnectorSecretStore with its Postgres row replaced (no DB in a unit test)."""

    def __new__(cls, ciphertext: str | None, tenant_vault: Any = None) -> Any:
        from app.mcp.connector_secrets import DurableConnectorSecretStore

        class _Fake(DurableConnectorSecretStore):
            async def _pg_get(self, tenant_id: str, server_id: str, key: str) -> str | None:
                return ciphertext

            async def _tenant_vault(self, tenant_id: str) -> Any:
                if isinstance(tenant_vault, BaseException):
                    raise tenant_vault
                return tenant_vault

            async def _legacy_reads(self) -> bool:
                return False

        return _Fake(db_factory=lambda: None, redis=None)


def _seal(master_key: str, plaintext: str) -> str:
    from app.providers.vault import CredentialVault

    return CredentialVault(master_key).encrypt(plaintext)


async def test_store_opens_a_value_sealed_with_this_key(vault_key: None) -> None:
    store = _Store(_seal(KEY_A, "ya29.tok"))
    assert await store.resolve("vault://connectors/s/token", tenant_ctx=_ctx()) == "ya29.tok"


async def test_store_missing_row_is_none(vault_key: None) -> None:
    assert await _Store(None).resolve("vault://connectors/s/token", tenant_ctx=_ctx()) is None


async def test_store_value_sealed_with_another_key_is_undecryptable(
    vault_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.providers import vault_canary
    from app.providers.vault import ConnectorSecretUndecryptableError

    async def _mismatch(db_factory: Any, role: str = "worker") -> Any:
        return vault_canary._mismatch(("bbbb",), "aaaa", role)

    monkeypatch.setattr(vault_canary, "check_vault_canary", _mismatch)
    store = _Store(_seal(KEY_B, "ya29.tok"))  # sealed by a process on another key
    with pytest.raises(ConnectorSecretUndecryptableError) as exc:
        await store.resolve("vault://connectors/s/token", tenant_ctx=_ctx())
    message = str(exc.value)
    assert "VAULT_MASTER_KEY" in message and "aaaa" in message and "bbbb" in message
    assert "does not help" in message


async def test_store_unreadable_tenant_key_is_unavailable_not_undecryptable(
    vault_key: None,
) -> None:
    from app.providers.tenant_vault import TenantVaultReadError
    from app.providers.vault import (
        ConnectorSecretUnavailableError,
        ConnectorSecretUndecryptableError,
    )

    store = _Store("tv1:whatever", tenant_vault=TenantVaultReadError("db down"))
    with pytest.raises(ConnectorSecretUnavailableError) as exc:
        await store.resolve("vault://connectors/s/token", tenant_ctx=_ctx())
    assert not isinstance(exc.value, ConnectorSecretUndecryptableError)


async def test_missing_secret_raises_not_found_from_the_tenant_resolver() -> None:
    from app.providers.vault import (
        ConnectorSecretNotFoundError,
        ConnectorSecretUnavailableError,
        resolve_connector_secret_ref_for_tenant,
    )

    class _Empty:
        async def resolve(self, ref: str, *, tenant_ctx: Any = None) -> None:
            return None

    with pytest.raises(ConnectorSecretNotFoundError) as exc:
        await resolve_connector_secret_ref_for_tenant(
            "vault://connectors/s/token", store=_Empty(), tenant_ctx=_ctx()
        )
    assert isinstance(exc.value, ConnectorSecretUnavailableError)  # callers' contract


# ── explain_undecryptable_secret: which side to fix ─────────────────────────


@pytest.mark.parametrize(
    ("status", "expect", "absent"),
    [
        ("mismatch", "does not help", "Re-enter the connector"),
        ("ok", "Re-enter the connector's credentials", "does not help"),
        ("unavailable", "could not be checked", "does not help"),
    ],
)
async def test_explanation_follows_the_canary(
    monkeypatch: pytest.MonkeyPatch, status: str, expect: str, absent: str
) -> None:
    from cryptography.fernet import InvalidToken

    from app.providers import vault_canary

    results = {
        "mismatch": vault_canary._mismatch(("bbbb",), "aaaa", "worker"),
        "ok": vault_canary.VaultCanaryResult("ok", "aaaa", "aaaa", "ok"),
        "unavailable": vault_canary.VaultCanaryResult(
            "unavailable", "bbbb", None, "vault canary could not be read (OSError)"
        ),
    }

    async def _check(db_factory: Any, role: str = "worker") -> Any:
        return results[status]

    monkeypatch.setattr(vault_canary, "check_vault_canary", _check)
    message = await vault_canary.explain_undecryptable_secret(object(), InvalidToken())
    assert expect in message
    assert absent not in message
    assert "VAULT_MASTER_KEY" in message


async def test_explanation_for_a_tenant_key_problem_is_about_the_tenant_key() -> None:
    from app.providers import vault_canary
    from app.providers.tenant_vault import TenantVaultError

    message = await vault_canary.explain_undecryptable_secret(
        None, TenantVaultError("value is tenant-vault encrypted but the tenant key is not loaded")
    )
    assert "tenant's vault key" in message


# ── one worker credential path (workflow worker == goal worker) ─────────────


def test_build_worker_mcp_client_wires_tenant_resolver_and_oauth_manager() -> None:
    from app.mcp.connector_wiring import build_worker_mcp_client
    from app.mcp.oauth import OAuthFlowManager

    factory = object()
    client = build_worker_mcp_client(object(), db_factory=factory)
    assert client._secret_resolver_accepts_tenant is True
    assert isinstance(client._oauth_manager, OAuthFlowManager)
    assert client._oauth_manager._db_session_factory is factory
    assert MCPRegistry.get_builtin_handler("builtin-gmail") is not None


async def test_workflow_worker_runner_mcp_client_has_the_oauth_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workflow worker's own client lacked it: OAuth connectors had no token."""
    import app.workflow.celery_tasks as ct
    from app.mcp.oauth import OAuthFlowManager

    monkeypatch.setattr(ct, "_WORKER_RUNNER", None)
    runner = ct._build_worker_runner()
    try:
        client = runner._compiler.services.get("mcp_client")
        assert client is not None
        assert isinstance(client._oauth_manager, OAuthFlowManager)
        assert client._oauth_manager._db_session_factory is not None
    finally:
        await ct._close_worker_runner_clients()
        monkeypatch.setattr(ct, "_WORKER_RUNNER", None)


# ── OAuth refresh: a confidential client sends its (resolved) secret ────────


@pytest.mark.parametrize(
    ("secret", "sent"),
    [
        ("gocspx-plain", "gocspx-plain"),
        ("vault://connectors/s/client_secret", None),  # never the reference
        ("", None),
    ],
)
async def test_refresh_sends_a_plain_client_secret_only(
    monkeypatch: pytest.MonkeyPatch, vault_key: None, secret: str, sent: str | None
) -> None:
    import httpx

    import app.net.ssrf_guard as guard
    from app.mcp.oauth import OAuthFlowManager, OAuthToken
    from app.providers.vault import get_vault

    posted: list[dict[str, Any]] = []

    async def _request_public(client: Any, method: str, url: str, **kwargs: Any) -> Any:
        posted.append(dict(kwargs.get("data") or {}))
        return httpx.Response(
            200,
            json={"access_token": "new-at", "expires_in": 3600},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(guard, "request_public", _request_public)
    mgr = OAuthFlowManager(vault=get_vault())
    token = await mgr.refresh_token(
        server_id="s",
        tenant_id="t-creds",
        token=OAuthToken(access_token="old", refresh_token="rt", expires_in=-120),
        auth_config={
            "token_url": "https://oauth2.googleapis.com/token",
            "client_id": "cid",
            "client_secret": secret,
        },
    )
    assert token is not None and token.access_token == "new-at"
    assert posted[0].get("client_secret") == sent
    assert posted[0]["refresh_token"] == "rt"
