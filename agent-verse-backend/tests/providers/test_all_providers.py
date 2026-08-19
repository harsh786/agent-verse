"""Comprehensive tests for all new providers, ModelRouter, and registry auto-detection.

Runs without real API keys — all HTTP calls are mocked via unittest.mock.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import CompletionRequest, EmbedRequest, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_openai_chat_response(content: str = "hello", model: str = "test-model") -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


def _make_openai_embed_response(dim: int = 8) -> MagicMock:
    item = MagicMock()
    item.embedding = [0.1] * dim
    item.index = 0
    usage = MagicMock()
    usage.total_tokens = 4
    resp = MagicMock()
    resp.data = [item]
    resp.model = "embed-model"
    resp.usage = usage
    return resp


def _completion_request(content: str = "hi", model: str = "test") -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content=content)], model=model)


# ---------------------------------------------------------------------------
# 1. Instantiation smoke-tests
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_ollama_provider_instantiation(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider(base_url="http://localhost:11434", default_model="qwen3:8b")
        assert p._base == "http://localhost:11434"
        assert p._default_model == "qwen3:8b"
        assert p.provider_name == "ollama"

    def test_openrouter_provider_instantiation(self) -> None:
        from app.providers.openrouter_provider import OpenRouterProvider

        p = OpenRouterProvider(api_key="or-key", default_model="openai/gpt-4o")
        assert p._default_model == "openai/gpt-4o"
        assert p.provider_name == "openrouter"

    def test_nvidia_nim_provider_instantiation(self) -> None:
        from app.providers.nvidia_nim_provider import NvidiaNIMProvider

        p = NvidiaNIMProvider(api_key="ngc-key")
        assert "nvidia.com" in p._client.base_url.host or "integrate" in str(p._client.base_url)
        assert p.provider_name == "nvidia_nim"

    def test_mistral_provider_instantiation(self) -> None:
        from app.providers.simple_providers import MistralProvider

        p = MistralProvider(api_key="k")
        assert p.provider_name == "mistral"

    def test_deepseek_provider_instantiation(self) -> None:
        from app.providers.simple_providers import DeepSeekProvider

        p = DeepSeekProvider(api_key="k")
        assert p.provider_name == "deepseek"

    def test_perplexity_provider_instantiation(self) -> None:
        from app.providers.simple_providers import PerplexityProvider

        p = PerplexityProvider(api_key="k")
        assert p.provider_name == "perplexity"

    def test_fireworks_provider_instantiation(self) -> None:
        from app.providers.simple_providers import FireworksProvider

        p = FireworksProvider(api_key="k")
        assert p.provider_name == "fireworks"

    def test_xai_provider_instantiation(self) -> None:
        from app.providers.simple_providers import XAIProvider

        p = XAIProvider(api_key="k")
        assert p.provider_name == "xai"

    def test_moonshot_provider_instantiation(self) -> None:
        from app.providers.simple_providers import MoonshotProvider

        p = MoonshotProvider(api_key="k")
        assert p.provider_name == "moonshot"

    def test_cerebras_provider_instantiation(self) -> None:
        from app.providers.simple_providers import CerebrasProvider

        p = CerebrasProvider(api_key="k")
        assert p.provider_name == "cerebras"

    def test_yi_provider_instantiation(self) -> None:
        from app.providers.simple_providers import YiProvider

        p = YiProvider(api_key="k")
        assert p.provider_name == "yi"

    def test_huggingface_provider_instantiation(self) -> None:
        from app.providers.simple_providers import HuggingFaceProvider

        p = HuggingFaceProvider(api_key="k")
        assert p.provider_name == "huggingface"

    def test_sambanova_provider_instantiation(self) -> None:
        from app.providers.simple_providers import SambanovaProvider

        p = SambanovaProvider(api_key="k")
        assert p.provider_name == "sambanova"


# ---------------------------------------------------------------------------
# 2. complete() with mocked HTTP
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_openrouter_complete(self) -> None:
        from app.providers.openrouter_provider import OpenRouterProvider

        p = OpenRouterProvider(api_key="or-key", default_model="openai/gpt-4o")
        mock_resp = _make_openai_chat_response("OpenRouter says hi", "openai/gpt-4o")
        p._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await p.complete(_completion_request(model="openai/gpt-4o"))
        assert result.content == "OpenRouter says hi"
        assert result.model == "openai/gpt-4o"

    @pytest.mark.asyncio
    async def test_nvidia_nim_complete(self) -> None:
        from app.providers.nvidia_nim_provider import NvidiaNIMProvider

        p = NvidiaNIMProvider(api_key="ngc-key")
        mock_resp = _make_openai_chat_response("NIM says hi", "nvidia/llama-3.1-nemotron-70b-instruct")
        p._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await p.complete(_completion_request(model="nvidia/llama-3.1-nemotron-70b-instruct"))
        assert result.content == "NIM says hi"

    @pytest.mark.asyncio
    async def test_mistral_complete(self) -> None:
        from app.providers.simple_providers import MistralProvider

        p = MistralProvider(api_key="k")
        mock_resp = _make_openai_chat_response("Mistral says hi", "mistral-large-latest")
        p._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await p.complete(_completion_request(model="mistral-large-latest"))
        assert result.content == "Mistral says hi"

    @pytest.mark.asyncio
    async def test_deepseek_complete(self) -> None:
        from app.providers.simple_providers import DeepSeekProvider

        p = DeepSeekProvider(api_key="k")
        mock_resp = _make_openai_chat_response("DeepSeek says hi", "deepseek-chat")
        p._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await p.complete(_completion_request(model="deepseek-chat"))
        assert result.content == "DeepSeek says hi"

    @pytest.mark.asyncio
    async def test_ollama_complete_delegates_to_openai_compat(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider(base_url="http://localhost:11434", default_model="qwen3:8b")
        mock_resp = _make_openai_chat_response("Ollama says hi", "qwen3:8b")
        p._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await p.complete(_completion_request(model="qwen3:8b"))
        assert result.content == "Ollama says hi"


# ---------------------------------------------------------------------------
# 3. ModelRouter
# ---------------------------------------------------------------------------


class TestModelRouter:
    def test_all_task_types_have_cloud_route(self) -> None:
        from app.providers.model_router import (
            CLOUD_TASK_ROUTING,
            ModelRouter,
            TaskType,
        )

        router = ModelRouter(use_ollama_when_available=False)
        for task in TaskType:
            sel = router.select(task)
            assert sel.primary, f"No model for {task.value}"
            assert sel.provider == "openrouter"

    def test_all_task_types_have_ollama_route(self) -> None:
        from app.providers.model_router import ModelRouter, TaskType

        with patch.dict("os.environ", {"OLLAMA_BASE_URL": "http://localhost:11434"}):
            router = ModelRouter(use_ollama_when_available=True)
            for task in TaskType:
                sel = router.select(task, prefer_local=True)
                assert sel.primary, f"No Ollama model for {task.value}"
                assert sel.provider == "ollama"

    def test_critical_criticality_always_uses_cloud(self) -> None:
        from app.providers.model_router import Criticality, ModelRouter, TaskType

        with patch.dict("os.environ", {"OLLAMA_BASE_URL": "http://localhost:11434"}):
            router = ModelRouter(use_ollama_when_available=True)
            sel = router.select(TaskType.CODING, criticality=Criticality.CRITICAL, prefer_local=True)
        assert sel.provider == "openrouter"

    def test_low_criticality_prefers_local_when_available(self) -> None:
        from app.providers.model_router import Criticality, ModelRouter, TaskType

        with patch.dict("os.environ", {"OLLAMA_BASE_URL": "http://localhost:11434"}):
            router = ModelRouter(use_ollama_when_available=True)
            sel = router.select(TaskType.DRAFTING, criticality=Criticality.LOW)
        assert sel.provider == "ollama"
        assert sel.estimated_cost_per_1k == 0.0

    def test_override_model_bypasses_routing(self) -> None:
        from app.providers.model_router import ModelRouter, TaskType

        router = ModelRouter()
        sel = router.select(TaskType.REASONING, override_model="my-custom/model")
        assert sel.primary == "my-custom/model"
        assert sel.provider == "override"
        assert sel.fallbacks == []

    def test_reasoning_task_selects_high_quality_model(self) -> None:
        from app.providers.model_router import Criticality, ModelRouter, TaskType

        router = ModelRouter(use_ollama_when_available=False)
        sel = router.select(TaskType.REASONING, criticality=Criticality.CRITICAL)
        # Critical reasoning should use the best model (first in routing table)
        assert "claude" in sel.primary or "o3" in sel.primary or "opus" in sel.primary

    def test_long_context_steering(self) -> None:
        from app.providers.model_router import ModelRouter, TaskType

        router = ModelRouter(use_ollama_when_available=False)
        sel = router.select(TaskType.ANALYSIS, context_tokens=150_000)
        # Should be steered to long_context routing table
        assert "gemini" in sel.primary or "claude" in sel.primary

    def test_get_model_router_returns_singleton(self) -> None:
        from app.providers.model_router import get_model_router

        r1 = get_model_router()
        r2 = get_model_router()
        assert r1 is r2

    def test_string_task_type_accepted(self) -> None:
        from app.providers.model_router import ModelRouter

        router = ModelRouter(use_ollama_when_available=False)
        sel = router.select("coding")
        assert sel.primary

    def test_string_criticality_accepted(self) -> None:
        from app.providers.model_router import ModelRouter

        router = ModelRouter(use_ollama_when_available=False)
        sel = router.select("coding", criticality="high")
        assert sel.primary


# ---------------------------------------------------------------------------
# 4. Ollama-specific tests
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    @pytest.mark.asyncio
    async def test_embed_calls_api_embeddings(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider(base_url="http://localhost:11434")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await p.embed(EmbedRequest(texts=["hello"], model="nomic-embed-text"))

        assert len(result.embeddings) == 1
        assert result.embeddings[0] == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_list_local_models(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider(base_url="http://localhost:11434")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "qwen3:8b"}, {"name": "nomic-embed-text"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            models = await p.list_local_models()

        assert len(models) == 2
        assert models[0]["name"] == "qwen3:8b"

    def test_validate_ram_known_model_pass(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider()
        with patch("app.providers.ollama_provider._get_available_ram_gb", return_value=64.0):
            ok, msg = p.validate_ram("qwen3:32b")
        assert ok is True
        assert "GB available" in msg

    def test_validate_ram_known_model_fail(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider()
        with patch("app.providers.ollama_provider._get_available_ram_gb", return_value=4.0):
            ok, msg = p.validate_ram("qwen3:32b")  # requires 24 GB
        assert ok is False
        assert "Insufficient" in msg

    def test_validate_ram_unknown_model(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider()
        ok, msg = p.validate_ram("unknown-model:7b")
        assert ok is True
        assert "skipped" in msg

    def test_url_for_model_text(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider(base_url="http://localhost:11434")
        url = p._url_for_model("qwen3:8b")
        assert url.endswith("/v1")

    def test_url_for_model_embed(self) -> None:
        from app.providers.ollama_provider import OllamaProvider

        p = OllamaProvider(base_url="http://localhost:11434")
        url = p._url_for_model("nomic-embed-text")
        assert "/api" in url

    def test_model_catalog_has_expected_entries(self) -> None:
        from app.providers.ollama_provider import OLLAMA_MODEL_CATALOG

        assert "qwen3:8b" in OLLAMA_MODEL_CATALOG
        assert "nomic-embed-text" in OLLAMA_MODEL_CATALOG
        assert "qwen2.5-coder:32b" in OLLAMA_MODEL_CATALOG
        assert OLLAMA_MODEL_CATALOG["qwen3:32b"]["ram_gb"] == 24


# ---------------------------------------------------------------------------
# 5. OpenRouter-specific tests
# ---------------------------------------------------------------------------


class TestOpenRouterProvider:
    def test_popular_models_list(self) -> None:
        from app.providers.openrouter_provider import OpenRouterProvider

        assert "anthropic/claude-3-5-sonnet" in OpenRouterProvider.POPULAR_MODELS
        assert "deepseek/deepseek-r1" in OpenRouterProvider.POPULAR_MODELS

    def test_default_headers_set(self) -> None:
        from app.providers.openrouter_provider import OpenRouterProvider

        p = OpenRouterProvider(api_key="or-key")
        # _or_headers stores the headers that are passed to the openai client constructor
        assert p._or_headers.get("HTTP-Referer") == "https://agentverse.ai"
        assert p._or_headers.get("X-Title") == "AgentVerse"
        # Verify the openai client also has them in its default_headers merged view
        client_headers = dict(p._client.default_headers)
        assert any("referer" in k.lower() or k == "HTTP-Referer" for k in client_headers)

    @pytest.mark.asyncio
    async def test_get_available_models(self) -> None:
        from app.providers.openrouter_provider import OpenRouterProvider

        p = OpenRouterProvider(api_key="or-key")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "openai/gpt-4o"}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            models = await p.get_available_models()

        assert len(models) == 1
        assert models[0]["id"] == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# 6. Registry auto-detection tests
# ---------------------------------------------------------------------------


class TestRegistryDetection:
    def test_detects_openrouter(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            providers = _detect_providers()
        types = [p.provider_type for p in providers]
        assert "openrouter" in types

    def test_detects_nvidia_nim_via_ngc_key(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"NGC_API_KEY": "ngc-key"}, clear=False):
            providers = _detect_providers()
        types = [p.provider_type for p in providers]
        assert "nvidia_nim" in types

    def test_detects_nvidia_nim_via_base_url(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"NVIDIA_NIM_BASE_URL": "http://nim:8000"}, clear=False):
            providers = _detect_providers()
        types = [p.provider_type for p in providers]
        assert "nvidia_nim" in types

    def test_detects_mistral(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"MISTRAL_API_KEY": "m-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "mistral" for p in providers)

    def test_detects_deepseek(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "ds-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "deepseek" for p in providers)

    def test_detects_perplexity(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"PERPLEXITY_API_KEY": "px-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "perplexity" for p in providers)

    def test_detects_fireworks(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"FIREWORKS_API_KEY": "fw-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "fireworks" for p in providers)

    def test_detects_xai(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"XAI_API_KEY": "xai-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "xai" for p in providers)

    def test_detects_moonshot(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"MOONSHOT_API_KEY": "ms-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "moonshot" for p in providers)

    def test_detects_cerebras(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"CEREBRAS_API_KEY": "cb-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "cerebras" for p in providers)

    def test_detects_huggingface(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"HF_API_KEY": "hf-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "huggingface" for p in providers)

    def test_detects_sambanova(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"SAMBANOVA_API_KEY": "sn-key"}, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "sambanova" for p in providers)

    def test_detects_azure_openai(self) -> None:
        from app.providers.registry import _detect_providers

        env = {"AZURE_OPENAI_API_KEY": "az-key", "AZURE_OPENAI_RESOURCE": "my-resource"}
        with patch.dict("os.environ", env, clear=False):
            providers = _detect_providers()
        assert any(p.provider_type == "azure_openai" for p in providers)

    def test_detects_ollama_with_models(self) -> None:
        from app.providers.registry import _detect_providers

        with patch.dict("os.environ", {"OLLAMA_BASE_URL": "http://localhost:11434"}, clear=False):
            providers = _detect_providers()
        ollama = next((p for p in providers if p.provider_type == "ollama"), None)
        assert ollama is not None
        assert ollama.models is not None
        assert "qwen3:8b" in ollama.models

    def test_instantiate_openrouter(self) -> None:
        from app.providers.openrouter_provider import OpenRouterProvider
        from app.providers.registry import ProviderConfig, _instantiate_provider

        cfg = ProviderConfig(
            provider_type="openrouter",
            api_key="or-key",
            models=["anthropic/claude-3-5-sonnet"],
        )
        provider = _instantiate_provider(cfg)
        assert isinstance(provider, OpenRouterProvider)

    def test_instantiate_nvidia_nim(self) -> None:
        from app.providers.nvidia_nim_provider import NvidiaNIMProvider
        from app.providers.registry import ProviderConfig, _instantiate_provider

        cfg = ProviderConfig(
            provider_type="nvidia_nim",
            api_key="ngc-key",
        )
        provider = _instantiate_provider(cfg)
        assert isinstance(provider, NvidiaNIMProvider)

    def test_instantiate_ollama(self) -> None:
        from app.providers.ollama_provider import OllamaProvider
        from app.providers.registry import ProviderConfig, _instantiate_provider

        cfg = ProviderConfig(
            provider_type="ollama",
            base_url="http://localhost:11434",
        )
        provider = _instantiate_provider(cfg)
        assert isinstance(provider, OllamaProvider)

    def test_instantiate_mistral(self) -> None:
        from app.providers.registry import ProviderConfig, _instantiate_provider
        from app.providers.simple_providers import MistralProvider

        cfg = ProviderConfig(provider_type="mistral", api_key="m-key")
        provider = _instantiate_provider(cfg)
        assert isinstance(provider, MistralProvider)

    def test_instantiate_deepseek(self) -> None:
        from app.providers.registry import ProviderConfig, _instantiate_provider
        from app.providers.simple_providers import DeepSeekProvider

        cfg = ProviderConfig(provider_type="deepseek", api_key="ds-key")
        provider = _instantiate_provider(cfg)
        assert isinstance(provider, DeepSeekProvider)

    def test_instantiate_no_api_key_returns_none_for_simple_providers(self) -> None:
        from app.providers.registry import ProviderConfig, _instantiate_provider

        for ptype in ("mistral", "deepseek", "perplexity"):
            cfg = ProviderConfig(provider_type=ptype, api_key="")
            result = _instantiate_provider(cfg)
            assert result is None, f"Expected None for {ptype} with no API key"

    def test_openrouter_no_key_returns_none(self) -> None:
        from app.providers.registry import ProviderConfig, _instantiate_provider

        cfg = ProviderConfig(provider_type="openrouter", api_key="")
        result = _instantiate_provider(cfg)
        assert result is None
