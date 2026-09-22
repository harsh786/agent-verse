"""Deepened coverage for content-type routing in app/embedding/orchestrator.py.

The prior suite had exactly one happy-path test each for text and code content.
This adds: malformed/empty input, very long text, code with syntax errors,
mixed-language code, and content-type misdetection (text labelled as code and
vice versa) — documenting that the orchestrator does not itself validate or
parse content; it only routes by the *declared* ContentType.
"""
from __future__ import annotations

import pytest

from app.embedding.orchestrator import EmbeddingOrchestrator, RoutedEmbeddingResult
from app.ingestion.content_classifier import ContentType
from app.providers.base import EmbedRequest, EmbedResponse
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def prof_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


class RecordingProvider:
    """Embedding provider that records exactly what it was asked to embed."""

    provider_name = "recording"

    def __init__(self) -> None:
        self.requested_models: list[str] = []
        self.requested_texts: list[list[str]] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.requested_models.append(request.model)
        self.requested_texts.append(list(request.texts))
        return EmbedResponse(
            embeddings=[[0.1, 0.2, 0.3] for _ in request.texts],
            model=request.model,
        )


class TestMalformedOrEmptyInput:
    async def test_empty_text_list(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        result = await orch.embed_for_content(
            [],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert isinstance(result, RoutedEmbeddingResult)
        assert result.embeddings == []

    async def test_single_empty_string(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        result = await orch.embed_for_content(
            [""],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        # The orchestrator does not itself reject blank strings — it is a
        # pure routing layer; it passes the text through to the provider.
        assert len(result.embeddings) == 1
        assert provider.requested_texts == [[""]]

    async def test_list_of_blank_and_whitespace_strings(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        result = await orch.embed_for_content(
            ["", "   ", "\n\t"],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert len(result.embeddings) == 3


class TestVeryLongText:
    async def test_extremely_long_single_text_passes_through_unchanged(self, prof_ctx) -> None:
        """No truncation/chunking happens at the orchestrator layer: it is a
        routing layer, not a length-enforcing one. This pins current behaviour
        so a future change to enforce EmbeddingModelSpec.max_input_tokens is a
        deliberate, visible change rather than a silent one."""
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        huge_text = "word " * 50_000  # ~250k chars, way over any max_input_tokens

        result = await orch.embed_for_content(
            [huge_text],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert len(result.embeddings) == 1
        assert provider.requested_texts[0][0] == huge_text  # untouched, not truncated

    async def test_many_long_texts_batch_all_survive(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        texts = ["x" * 10_000 for _ in range(5)]
        result = await orch.embed_for_content(
            texts,
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert len(result.embeddings) == 5


class TestCodeContentEdgeCases:
    async def test_code_with_syntax_errors_still_routes_to_code_model(self, prof_ctx) -> None:
        """The orchestrator never parses code — a syntactically broken snippet
        is routed exactly like valid code, based solely on the declared
        ContentType."""
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        broken_code = "def foo(:\n    return\nclass :::"

        result = await orch.embed_for_content(
            [broken_code],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert result.model_id == "voyage-code-3"
        assert provider.requested_models == ["voyage-code-3"]

    async def test_mixed_language_code_routes_to_code_model(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        mixed = (
            "def python_fn():\n    return 1\n\n"
            "function jsFn() { return 2; }\n\n"
            "public class JavaThing {}\n"
        )
        result = await orch.embed_for_content(
            [mixed],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert result.model_id == "voyage-code-3"
        assert result.embeddings and result.embeddings[0] == [0.1, 0.2, 0.3]


class NotImplementedProvider:
    """A provider whose embed() has no real implementation."""

    provider_name = "not-implemented"

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        raise NotImplementedError("embedding not supported by this provider")


class TestProviderDegradesGracefully:
    async def test_provider_not_implemented_returns_empty_sentinel_per_text(
        self, prof_ctx
    ) -> None:
        """When the resolved provider doesn't implement embed() at all, the
        orchestrator must return the empty-list sentinel per text (never crash,
        never a zero vector)."""
        orch = EmbeddingOrchestrator()
        result = await orch.embed_for_content(
            ["a", "b", "c"],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=NotImplementedProvider(),
        )
        assert result.embeddings == [[], [], []]

    async def test_provider_resolver_exception_falls_back_to_default_provider(
        self, prof_ctx
    ) -> None:
        """A broken provider_resolver (raises instead of returning None) must
        not blow up embed_for_content — it degrades to default_provider."""
        orch = EmbeddingOrchestrator()
        default_provider = RecordingProvider()

        def broken_resolver(provider_name: str):
            raise RuntimeError("resolver misconfigured")

        result = await orch.embed_for_content(
            ["hello"],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=default_provider,
            provider_resolver=broken_resolver,
        )
        assert result.embeddings == [[0.1, 0.2, 0.3]]
        assert default_provider.requested_texts == [["hello"]]


class TestContentTypeMisdetection:
    async def test_plain_prose_mislabelled_as_code_still_embeds(self, prof_ctx) -> None:
        """Upstream classification can be wrong (e.g. prose that looks vaguely
        code-like); the orchestrator must still produce a usable embedding
        rather than erroring out."""
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        prose = "The quarterly report shows strong growth across all regions."

        result = await orch.embed_for_content(
            [prose],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        # Routed to the code model per the (mis)declared type — but it still
        # produces a real embedding, not an error.
        assert result.model_id == "voyage-code-3"
        assert len(result.embeddings) == 1
        assert result.embeddings[0]

    async def test_code_snippet_mislabelled_as_text_still_embeds(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        code = "def add(a, b):\n    return a + b"

        result = await orch.embed_for_content(
            [code],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert result.model_id != "voyage-code-3"
        assert len(result.embeddings) == 1
        assert result.embeddings[0]
