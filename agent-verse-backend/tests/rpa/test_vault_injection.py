"""P1.2 tests: vault credential injection, CAPTCHA tools, takeover endpoint."""
import inspect
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_credential_injector_resolves_vault_ref():
    from app.rpa.credential_injector import CredentialInjector

    mock_store = AsyncMock()
    mock_store.resolve = AsyncMock(return_value="my-secret-password")

    injector = CredentialInjector(secret_store=mock_store, tenant_id="t1")
    result = await injector.resolve("vault://jira-mcp/api_key")
    assert result == "my-secret-password"


@pytest.mark.asyncio
async def test_credential_injector_passthrough_non_vault():
    from app.rpa.credential_injector import CredentialInjector
    injector = CredentialInjector()
    result = await injector.resolve("plain-text-value")
    assert result == "plain-text-value"


@pytest.mark.asyncio
async def test_credential_injector_resolve_arguments_dict():
    from app.rpa.credential_injector import CredentialInjector
    mock_store = AsyncMock()
    mock_store.resolve = AsyncMock(return_value="resolved-password")
    injector = CredentialInjector(secret_store=mock_store, tenant_id="t1")

    args = {
        "selector": "#password",
        "text": "vault://vendor-portal/password",
        "slow_type": True,
    }
    resolved = await injector.resolve_arguments(args)
    assert resolved["text"] == "resolved-password"
    assert resolved["selector"] == "#password"
    assert resolved["slow_type"] is True


def test_credential_injector_is_vault_ref():
    from app.rpa.credential_injector import CredentialInjector
    inj = CredentialInjector()
    assert inj.is_vault_ref("vault://something") is True
    assert inj.is_vault_ref("plain-text") is False
    assert inj.is_vault_ref(42) is False


def test_rpa_tools_has_captcha_detection():
    from app.rpa.tools import RPA_TOOLS
    names = {t["name"] for t in RPA_TOOLS}
    assert "rpa_detect_captcha" in names, "rpa_detect_captcha tool must be defined"
    assert "rpa_request_human_help" in names, "rpa_request_human_help tool must be defined"
    assert "rpa_wait_for_network_idle" in names, "rpa_wait_for_network_idle tool must be defined"


def test_rpa_executor_handles_captcha_tool():
    from app.rpa import executor
    src = inspect.getsource(executor)
    assert "rpa_detect_captcha" in src, "executor.py must handle rpa_detect_captcha"
    assert "rpa_request_human_help" in src, "executor.py must handle rpa_request_human_help"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("rpa_detect_captcha", {}),
        ("rpa_request_human_help", {"reason": "CAPTCHA detected"}),
        ("rpa_wait_for_network_idle", {"timeout_ms": 5000}),
    ],
)
async def test_rpa_p12_tools_never_fake_success_without_browser(tool, args):
    """These used to return captcha_detected: false / "Human help requested" /
    "Network idle" with success=True without looking at any page."""
    from app.rpa.executor import RPAExecutor

    ex = RPAExecutor()
    ex._playwright_available = False
    result = await ex.execute(tool_name=tool, arguments=args)
    assert result.success is False
    assert "NOT IMPLEMENTED" in (result.error or "")


@pytest.mark.asyncio
async def test_credential_injector_wired_to_executor():
    """RPAExecutor resolves vault:// refs through the injector before dispatch."""
    from app.rpa.credential_injector import CredentialInjector
    from app.rpa.executor import RPAExecutor

    mock_store = AsyncMock()
    mock_store.resolve = AsyncMock(return_value="resolved-secret")
    injector = CredentialInjector(secret_store=mock_store, tenant_id="t1")

    ex = RPAExecutor()
    ex._playwright_available = False  # no real browser
    ex._credential_injector = injector

    result = await ex.execute(
        tool_name="rpa_type",
        arguments={"selector": "#password", "text": "vault://portal/pass"},
    )
    mock_store.resolve.assert_awaited_once()
    # No browser → honest failure; but it is NOT a credential failure.
    assert result.success is False
    assert "NOT IMPLEMENTED" in (result.error or "")


class _TenantStore:
    """Minimal tenant-aware store with the real ``resolve(ref, *, tenant_ctx)`` API."""

    def __init__(self, data):
        self._data = data

    async def resolve(self, ref, *, tenant_ctx=None):
        return self._data.get((getattr(tenant_ctx, "tenant_id", None), ref))


@pytest.mark.asyncio
async def test_injector_uses_real_store_api_and_is_tenant_scoped():
    """Regression: the injector called ``get_secret`` (no store has it), so every
    reference silently stayed unresolved. It must use ``resolve`` per tenant."""
    from app.rpa.credential_injector import CredentialInjector, CredentialResolutionError

    store = _TenantStore({("t1", "vault://connectors/portal/pass"): "s3cret"})
    assert await CredentialInjector(secret_store=store, tenant_id="t1").resolve(
        "vault://portal/pass"
    ) == "s3cret"
    with pytest.raises(CredentialResolutionError):
        await CredentialInjector(secret_store=store, tenant_id="t2").resolve(
            "vault://portal/pass"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "injector_kwargs",
    [{}, {"secret_store": _TenantStore({}), "tenant_id": "t1"}, {"secret_store": _TenantStore({})}],
)
async def test_injector_fails_closed_when_unresolvable(injector_kwargs):
    from app.rpa.credential_injector import CredentialInjector, CredentialResolutionError

    with pytest.raises(CredentialResolutionError):
        await CredentialInjector(**injector_kwargs).resolve("vault://portal/pass")


@pytest.mark.asyncio
async def test_executor_builds_tenant_injector_from_resolver_and_fails_closed():
    """With no explicit injector, a vault:// ref is resolved via the app's secret
    store for the calling tenant; unresolvable → the command is not run."""
    from app.rpa.executor import RPAExecutor

    store = _TenantStore({("t1", "vault://connectors/portal/pass"): "s3cret"})
    ex = RPAExecutor(secret_store_resolver=lambda: store)
    ex._playwright_available = False

    seen = {}

    async def _capture(*, tool_name, arguments):
        seen.update(arguments)
        from app.rpa.executor import RPAResult

        return RPAResult(success=True, output="ok")

    ex._execute_simulation = _capture  # type: ignore[method-assign]
    ok = await ex.execute(
        tool_name="rpa_type",
        arguments={"selector": "#p", "text": "vault://portal/pass"},
        tenant_id="t1",
    )
    assert ok.success is True
    assert seen["text"] == "s3cret"

    denied = await ex.execute(
        tool_name="rpa_type",
        arguments={"selector": "#p", "text": "vault://portal/pass"},
        tenant_id="t2",
    )
    assert denied.success is False
    assert "credential injection failed" in (denied.error or "")


def test_rpa_api_has_takeover_endpoint():
    from fastapi.openapi.utils import get_openapi

    from app.main import create_app
    app = create_app()
    schema = get_openapi(title="test", version="0.1", routes=app.routes)
    paths = list(schema.get("paths", {}).keys())
    assert any("takeover" in p for p in paths), (
        f"POST /rpa/sessions/{{id}}/takeover must exist. Found paths: {[p for p in paths if 'rpa' in p]}"
    )
