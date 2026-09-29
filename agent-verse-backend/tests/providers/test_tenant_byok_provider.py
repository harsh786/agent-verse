"""BYOK provider construction: tenant keys go to the tenant's vendor or the goal fails.

Regressions:
* groq/together configs without base_url built ``OpenAICompatibleProvider(base_url=None)``
  which the OpenAI SDK sends to api.openai.com — leaking the tenant's key to OpenAI.
* gemini/nvidia/openrouter (and, on the API path, openai_compatible) configs were
  ignored and the goal ran on the PLATFORM provider (platform pays).
* A vault decrypt failure was swallowed and the goal ran on the platform provider.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.providers.tenant_provider import TenantProviderError, build_tenant_provider
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext


def _vault(value: str = "tenant-secret-key") -> MagicMock:
    v = MagicMock()
    v.decrypt = MagicMock(return_value=value)
    return v


def _inner(p: Any) -> Any:
    return getattr(p, "_inner", p)


def _platform_state(tenant_id: str, cfg: dict[str, Any]) -> Any:
    from app.providers.openai_compatible import OpenAICompatibleProvider

    state = MagicMock()
    state._llm_provider_override = None
    state._llm_configs = {tenant_id: cfg}
    state._app_provider = OpenAICompatibleProvider(
        api_key="platform-key", base_url="http://platform.invalid/v1", default_model="plat"
    )
    return state


def _make_loop(cfg: dict[str, Any], tenant_id: str = "byok-t1") -> Any:
    from app.governance.audit import AuditLog
    from app.governance.hitl import HITLGateway

    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")
    return svc._make_agent_loop_for_tenant(ctx, _platform_state(tenant_id, cfg))


@pytest.mark.parametrize(
    ("pname", "expected"),
    [
        ("groq", "https://api.groq.com/openai/v1"),
        ("together", "https://api.together.xyz/v1"),
        ("openai", "https://api.openai.com/v1"),
    ],
)
def test_api_path_sends_key_to_the_vendors_official_url(pname: str, expected: str) -> None:
    cfg = {"provider": pname, "encrypted_key": "enc", "default_model": "m-1", "base_url": ""}
    with patch("app.providers.vault.get_vault", return_value=_vault()):
        loop = _make_loop(cfg)
    provider = _inner(loop._planner)
    assert provider._base_url == expected
    assert str(provider._client.base_url).rstrip("/") == expected


@pytest.mark.parametrize("pname", ["azure", "openai_compatible", "ollama"])
def test_api_path_refuses_custom_endpoint_without_base_url(pname: str) -> None:
    cfg = {"provider": pname, "encrypted_key": "enc", "default_model": "m-1"}
    with (
        patch("app.providers.vault.get_vault", return_value=_vault()),
        pytest.raises(TenantProviderError, match="base_url"),
    ):
        _make_loop(cfg)


def test_api_path_openai_compatible_with_base_url_uses_tenant_provider(monkeypatch) -> None:
    # Placeholder host: resolve it to a public address (the base_url guard fails
    # closed on DNS errors).
    monkeypatch.setattr("app.net.ssrf_guard._resolve_host", lambda _h: ["93.184.216.34"])
    cfg = {
        "provider": "openai_compatible",
        "encrypted_key": "enc",
        "default_model": "m-1",
        "base_url": "https://llm.tenant.example/v1",
    }
    with patch("app.providers.vault.get_vault", return_value=_vault()):
        loop = _make_loop(cfg)
    provider = _inner(loop._planner)
    assert provider._base_url == "https://llm.tenant.example/v1"


def test_api_path_gemini_config_is_not_replaced_by_platform_provider() -> None:
    built: list[dict[str, Any]] = []

    class _FakeGemini:
        _default_model = "gemini-2.5-pro"

        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    cfg = {"provider": "gemini", "encrypted_key": "enc", "default_model": "gemini-2.5-pro"}
    with (
        patch("app.providers.vault.get_vault", return_value=_vault("g-key")),
        patch("app.providers.gemini_provider.GeminiProvider", _FakeGemini),
    ):
        loop = _make_loop(cfg)
    assert isinstance(_inner(loop._planner), _FakeGemini)
    assert built[0]["api_key"] == "g-key"


@pytest.mark.parametrize(
    ("pname", "cls_name", "url"),
    [
        ("nvidia", "NvidiaNIMProvider", "https://integrate.api.nvidia.com/v1"),
        ("openrouter", "OpenRouterProvider", "https://openrouter.ai/api/v1"),
    ],
)
def test_api_path_nvidia_openrouter_use_tenant_provider(
    pname: str, cls_name: str, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_NIM_BASE_URL", "http://internal-nim.platform.invalid/v1")
    cfg = {"provider": pname, "encrypted_key": "enc", "default_model": "m-1"}
    with patch("app.providers.vault.get_vault", return_value=_vault()):
        loop = _make_loop(cfg)
    provider = _inner(loop._planner)
    assert type(provider).__name__ == cls_name
    assert provider._base_url == url


def test_api_path_decrypt_failure_fails_instead_of_platform_fallback() -> None:
    cfg = {"provider": "anthropic", "encrypted_key": "enc"}
    broken = MagicMock()
    broken.decrypt = MagicMock(side_effect=ValueError("bad token"))
    with (
        patch("app.providers.vault.get_vault", return_value=broken),
        pytest.raises(TenantProviderError, match="decrypt"),
    ):
        _make_loop(cfg)


def test_api_path_unknown_provider_fails() -> None:
    with (
        patch("app.providers.vault.get_vault", return_value=_vault()),
        pytest.raises(TenantProviderError, match="not supported"),
    ):
        _make_loop({"provider": "mystery", "encrypted_key": "enc"})


def test_api_path_byok_provider_gets_tenant_scoped_circuit() -> None:
    from app.providers.circuit_breaker import breaker_key

    cfg = {"provider": "groq", "encrypted_key": "enc", "default_model": "m-1"}
    with patch("app.providers.vault.get_vault", return_value=_vault()):
        loop = _make_loop(cfg, tenant_id="byok-circuit")
    for role in (loop._planner, loop._executor, loop._verifier):
        assert breaker_key(role).startswith("tenant:byok-circuit:llm:")


def test_no_config_means_platform_provider() -> None:
    assert build_tenant_provider(None, tenant_id="t") is None
    assert build_tenant_provider({}, tenant_id="t") is None


# ── Worker path (Celery) ──────────────────────────────────────────────────────


class _Store:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg

    async def get_config(self, tenant_id: str, **_kw: Any) -> dict[str, Any]:
        return self._cfg


@pytest.fixture
def worker_store(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("REDIS_URL", raising=False)

    def _install(cfg: dict[str, Any]) -> None:
        monkeypatch.setattr(
            "app.services.llm_config_store.get_or_create_worker_llm_config_store",
            lambda: _Store(cfg),
        )

    return _install


def test_worker_together_without_base_url_goes_to_together(worker_store: Any) -> None:
    from app.scaling.tasks import _get_llm_provider

    worker_store({"provider": "together", "encrypted_key": "enc", "model": "m-1"})
    with patch("app.providers.vault.get_vault", return_value=_vault()):
        provider = _get_llm_provider("byok-w1")
    assert provider._base_url == "https://api.together.xyz/v1"


def test_worker_gemini_config_is_not_ignored(worker_store: Any) -> None:
    from app.scaling.tasks import _get_llm_provider

    class _FakeGemini:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    worker_store({"provider": "gemini", "encrypted_key": "enc", "model": "gemini-2.5-pro"})
    with (
        patch("app.providers.vault.get_vault", return_value=_vault()),
        patch("app.providers.gemini_provider.GeminiProvider", _FakeGemini),
    ):
        provider = _get_llm_provider("byok-w2")
    assert isinstance(provider, _FakeGemini)


def test_worker_run_goal_fails_on_decrypt_error(
    worker_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    ran: list[Any] = []

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            ran.append(kwargs)

    async def _claimed(goal_id: str, tenant_id: str) -> str:
        return "claimed"  # no goals table here; the worker's atomic claim is granted

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claimed)
    worker_store({"provider": "anthropic", "encrypted_key": "enc"})
    broken = MagicMock()
    broken.decrypt = MagicMock(side_effect=ValueError("bad token"))
    with patch("app.providers.vault.get_vault", return_value=broken):
        result = tasks.run_goal.run("goal-byok-bad", "byok-w3", "a goal", "normal", False)
    assert result["status"] == "failed"
    assert result["reason"] == "tenant_llm_provider_unavailable"
    assert ran == []  # never ran on the platform provider


def test_tenant_base_url_pointing_at_a_private_host_is_refused(monkeypatch) -> None:
    """Regression: a tenant base_url like http://169.254.169.254/ turned every LLM
    call into an SSRF from the platform into its own network."""
    import pytest

    from app.providers.tenant_provider import TenantProviderError, _assert_tenant_base_url_allowed

    monkeypatch.delenv("TENANT_LLM_ALLOWED_PRIVATE_HOSTS", raising=False)
    for url in ("http://169.254.169.254/v1", "http://127.0.0.1:8000/v1", "http://10.0.0.5/v1"):
        with pytest.raises(TenantProviderError):
            _assert_tenant_base_url_allowed(url)
    monkeypatch.setenv("TENANT_LLM_ALLOWED_PRIVATE_HOSTS", "10.0.0.5")
    _assert_tenant_base_url_allowed("http://10.0.0.5/v1")


async def test_byok_read_failure_is_not_treated_as_no_byok() -> None:
    """Regression: a DB error reading the tenant's config was indistinguishable
    from "not configured", so the goal silently ran on the platform provider."""
    import pytest

    from app.services.llm_config_store import LLMConfigReadError, LLMConfigStore

    def _broken() -> None:
        raise RuntimeError("db down")

    store = LLMConfigStore(db_factory=_broken)
    assert await store.get_config("t1") is None
    with pytest.raises(LLMConfigReadError):
        await store.get_config("t1", strict=True)
