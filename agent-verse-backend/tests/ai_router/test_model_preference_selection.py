"""Selection honours the per-capability preference order and provider readiness.

Covers the resolvers each capability uses: reasoning fallbacks, OCR/vision order,
and the endpoint-bound embedding / reranker choice (only models of the endpoint's
own provider).
"""

_ISOLATE_PROVIDER_ENV = True

import pytest

from app.ai_router.model_catalog import (
    catalog_endpoints,
    provider_for_endpoint_url,
    provider_ready,
)
from app.ai_router.models import ModelCapability, ModelEndpoint, TaskType
from app.ai_router.registry import model_registry
from app.ai_router.selection import (
    ordered_configured_models,
    resolve_embed_model,
    resolve_fallback_models,
    resolve_ocr_fallback_models,
    resolve_ocr_model,
    resolve_rerank_model,
    select_configured_model_id,
)

_TG, _TU = ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE
_VI, _OC = ModelCapability.VISION, ModelCapability.OCR
_EM, _RR = ModelCapability.EMBEDDING, ModelCapability.RERANK


def _add(provider, model_id, caps, cost=0.0, source="override", vision=False):
    model_registry.register_configured(
        ModelEndpoint(provider=provider, model_id=model_id, display_name=model_id,
                      capabilities=list(caps), cost_per_1k_input=cost,
                      supports_tools=_TU in caps, supports_vision=vision or _VI in caps,
                      extra={"source": source})
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def test_preference_first_then_cheapest():
    _add("custom", "a", [_TG, _TU], 0.03)
    _add("custom", "b", [_TG, _TU], 0.01)
    _add("custom", "c", [_TG, _TU], 0.02)
    assert [m.model_id for m in ordered_configured_models(TaskType.PLANNING)] == ["b", "c", "a"]
    model_registry.set_preferences({"text_generation": ["custom/a"]})
    assert [m.model_id for m in ordered_configured_models(TaskType.PLANNING)] == ["a", "b", "c"]
    assert select_configured_model_id(TaskType.EXECUTION) == "a"
    assert resolve_fallback_models("planning", "a") == ["b", "c"]


def test_override_without_provider_key_is_skipped_env_seeded_is_not(monkeypatch):
    _add("groq", "llama-3.1-8b-instant", [_TG, _TU], 0.0)
    _add("openai", "env-gpt", [_TG, _TU], 0.5, source="env")  # deployment-configured
    assert select_configured_model_id("planning") == "env-gpt"
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    assert select_configured_model_id("planning") == "llama-3.1-8b-instant"


def test_ocr_uses_the_ocr_order_then_vision_models():
    _add("custom", "vis-1", [_TG, _VI], 0.0)
    _add("custom", "ocr-1", [_TG, _VI, _OC], 0.02)
    _add("custom", "ocr-2", [_TG, _VI, _OC], 0.01)
    model_registry.set_preferences({"ocr": ["custom/ocr-1", "custom/ocr-2"]})
    assert resolve_ocr_model("") == "ocr-1"
    assert resolve_ocr_fallback_models("ocr-1") == ["ocr-2", "vis-1"]


def test_embedding_and_reranker_only_pick_their_endpoint_providers_models(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    _add("ollama", "nomic-embed-text", [_EM], 0.0)
    _add("openai", "text-embedding-3-small", [_EM], 0.00002, source="env")
    assert resolve_embed_model("fallback", provider="openai") == "text-embedding-3-small"
    _add("nvidia", "nvidia/llama-3.2-nv-rerankqa-1b-v2", [_RR], 0.00002)
    _add("ollama", "some-reranker", [_RR], 0.0)
    assert (
        resolve_rerank_model("cfg", provider="nvidia") == "nvidia/llama-3.2-nv-rerankqa-1b-v2"
    )
    assert resolve_rerank_model("cfg", provider="cohere") == "cfg"


def test_endpoint_url_provider_inference():
    assert provider_for_endpoint_url("https://ai.api.nvidia.com/v1/retrieval/rerank") == "nvidia"
    assert provider_for_endpoint_url("https://api.voyageai.com/v1/rerank") == "voyage"
    assert provider_for_endpoint_url("https://api.openai.com/v1") == "openai"
    assert provider_for_endpoint_url("http://10.0.0.9:9000/rerank") is None
    assert provider_for_endpoint_url("") is None


def test_catalog_filters_and_provider_readiness(monkeypatch):
    groq = catalog_endpoints(["groq"])
    assert groq and {e["provider"] for e in groq} == {"groq"}
    one = catalog_endpoints(model_ids=["groq/openai/gpt-oss-120b"])
    assert [(e["provider"], e["model_id"]) for e in one] == [("groq", "openai/gpt-oss-120b")]
    both = catalog_endpoints(model_ids=["openai/gpt-oss-120b"])  # plain id: every provider
    assert {e["provider"] for e in both} == {"nvidia", "groq"}

    assert provider_ready("groq") is False
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    assert provider_ready("groq") is True
    assert provider_ready("custom") is True
