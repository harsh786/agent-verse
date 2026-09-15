"""On-prem model cluster — model→endpoint dispatch + settings backfill."""

from __future__ import annotations

from typing import Any

import pytest

from app.providers.onprem import MultiEndpointLLMProvider


class _FakeEndpoint:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    async def complete(self, request: Any) -> Any:
        self.calls.append("complete")

        class _R:
            content = f"answer from {self.name}"

        return _R()

    async def embed(self, request: Any) -> Any:
        self.calls.append("embed")
        return "EMB"

    def supports_vision(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return True

    def supports_structured_output(self) -> bool:
        return True


class _Req:
    def __init__(self, model: str = "") -> None:
        self.model = model


@pytest.fixture
def cluster() -> tuple[MultiEndpointLLMProvider, dict[str, _FakeEndpoint]]:
    qwen, gemma, embed = _FakeEndpoint("qwen"), _FakeEndpoint("gemma"), _FakeEndpoint("embed")
    prov = MultiEndpointLLMProvider(
        endpoints={"Qwen/Qwen3.5-4B": qwen, "google/gemma-4-E2B": gemma},  # type: ignore[dict-item]
        default_model="Qwen/Qwen3.5-4B",
        embed_provider=embed,  # type: ignore[arg-type]
    )
    return prov, {"qwen": qwen, "gemma": gemma, "embed": embed}


async def test_dispatches_by_model_name(cluster) -> None:
    prov, eps = cluster
    r = await prov.complete(_Req("google/gemma-4-E2B"))
    assert "gemma" in r.content
    assert eps["gemma"].calls == ["complete"] and eps["qwen"].calls == []


async def test_unknown_model_falls_back_to_default(cluster) -> None:
    prov, eps = cluster
    await prov.complete(_Req("some-unconfigured-model"))
    assert eps["qwen"].calls == ["complete"]  # default endpoint


async def test_embeddings_route_to_embed_endpoint(cluster) -> None:
    prov, eps = cluster
    out = await prov.embed(_Req())
    assert out == "EMB" and eps["embed"].calls == ["embed"]
    assert eps["qwen"].calls == [] and eps["gemma"].calls == []


def test_apply_onprem_settings_backfills_embedding_and_reranker() -> None:
    from app.core.config import Settings
    from app.main import _apply_onprem_settings

    s = Settings(
        onprem_enabled=True,
        onprem_qwen_base_url="http://host:30080/v1",
        onprem_embedding_base_url="http://host:30082/v1",
        onprem_reranker_url="http://host:30083/v1/rerank",
    )
    _apply_onprem_settings(s)
    assert s.default_llm_provider == "onprem"
    assert s.embedding_base_url == "http://host:30082/v1" and s.embedding_dim == 1024
    assert s.rag_hosted_reranker_url == "http://host:30083/v1/rerank"
    assert s.rag_hosted_reranker_allow_internal is True


def test_disabled_onprem_is_a_noop() -> None:
    from app.core.config import Settings
    from app.main import _apply_onprem_settings
    from app.providers.onprem import build_onprem_provider

    s = Settings(onprem_enabled=False, onprem_qwen_base_url="http://host:30080/v1")
    _apply_onprem_settings(s)
    assert s.default_llm_provider == "anthropic"  # unchanged
    assert build_onprem_provider(s) is None


def test_hybrid_cluster_puts_nvidia_on_top_with_onprem() -> None:
    from app.core.config import Settings
    from app.providers.onprem import build_onprem_provider

    s = Settings(
        onprem_enabled=True, onprem_qwen_base_url="http://host:30080/v1",
        onprem_gemma_base_url="http://host:30081/v1",
        nvidia_api_key="nvapi-x", nvidia_model="nvidia/llama-3.1-nemotron-70b-instruct",
    )
    p = build_onprem_provider(s)
    assert p is not None
    assert p._agentverse_provider_type == "hybrid"
    # Fast local Qwen fronts interactive chat; NVIDIA stays top via the router
    # (planning + fallback) and is present as an endpoint.
    assert p._default_model == "Qwen/Qwen3.5-4B"
    assert set(p._endpoints) == {
        "nvidia/llama-3.1-nemotron-70b-instruct", "Qwen/Qwen3.5-4B", "google/gemma-4-E2B",
    }


def test_nvidia_only_cluster_when_no_onprem() -> None:
    from app.core.config import Settings
    from app.providers.onprem import build_onprem_provider

    s = Settings(nvidia_api_key="nvapi-x")
    p = build_onprem_provider(s)
    assert p is not None and p._agentverse_provider_type == "nvidia"


def test_explicit_per_role_overrides_win_over_registry(monkeypatch) -> None:
    from app.agent.model_router import ModelRouter

    monkeypatch.setenv("DEFAULT_PLANNING_MODEL", "nvidia/nemotron")
    monkeypatch.setenv("DEFAULT_EXECUTION_MODEL", "Qwen/Qwen3.5-4B")
    monkeypatch.setenv("DEFAULT_VERIFICATION_MODEL", "google/gemma-4-E2B")
    r = ModelRouter(provider_name="hybrid")
    assert r.model_for("planning") == "nvidia/nemotron"
    assert r.model_for("execution") == "Qwen/Qwen3.5-4B"
    assert r.model_for("verification") == "google/gemma-4-E2B"
