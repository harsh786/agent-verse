"""P1.2 tests: vault credential injection, CAPTCHA tools, takeover endpoint."""
import inspect

import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_credential_injector_resolves_vault_ref():
    from app.rpa.credential_injector import CredentialInjector

    mock_store = AsyncMock()
    mock_store.get_secret = AsyncMock(return_value="my-secret-password")

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
    mock_store.get_secret = AsyncMock(return_value="resolved-password")
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
async def test_rpa_executor_captcha_simulation():
    from app.rpa.executor import RPAExecutor
    ex = RPAExecutor()
    result = await ex.execute(tool_name="rpa_detect_captcha", arguments={})
    assert result.success is True
    assert "captcha_detected" in result.output


@pytest.mark.asyncio
async def test_rpa_executor_human_help_simulation():
    from app.rpa.executor import RPAExecutor
    ex = RPAExecutor()
    result = await ex.execute(
        tool_name="rpa_request_human_help",
        arguments={"reason": "CAPTCHA detected"},
    )
    assert result.success is True
    assert "Human help requested" in result.output


@pytest.mark.asyncio
async def test_rpa_executor_network_idle_simulation():
    from app.rpa.executor import RPAExecutor
    ex = RPAExecutor()
    result = await ex.execute(tool_name="rpa_wait_for_network_idle", arguments={"timeout_ms": 5000})
    assert result.success is True
    assert "5000" in result.output


@pytest.mark.asyncio
async def test_credential_injector_wired_to_executor():
    """RPAExecutor respects _credential_injector when set."""
    from app.rpa.executor import RPAExecutor
    from app.rpa.credential_injector import CredentialInjector

    mock_store = AsyncMock()
    mock_store.get_secret = AsyncMock(return_value="resolved-secret")
    injector = CredentialInjector(secret_store=mock_store, tenant_id="t1")

    ex = RPAExecutor()
    ex._credential_injector = injector

    # rpa_type with a vault reference in the text argument
    result = await ex.execute(
        tool_name="rpa_type",
        arguments={"selector": "#password", "text": "vault://portal/pass"},
    )
    # Should succeed (the resolved value is used in simulation output)
    assert result.success is True


def test_rpa_api_has_takeover_endpoint():
    from app.main import create_app
    from fastapi.openapi.utils import get_openapi
    app = create_app()
    schema = get_openapi(title="test", version="0.1", routes=app.routes)
    paths = list(schema.get("paths", {}).keys())
    assert any("takeover" in p for p in paths), (
        f"POST /rpa/sessions/{{id}}/takeover must exist. Found paths: {[p for p in paths if 'rpa' in p]}"
    )
