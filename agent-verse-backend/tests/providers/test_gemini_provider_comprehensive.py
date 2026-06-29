"""Comprehensive tests for GeminiProvider — targets 85% coverage.

google-generativeai is NOT installed; all tests mock via sys.modules.

Key patching insight: `import google.generativeai as genai` resolves genai via
`sys.modules["google"].generativeai`, NOT via `sys.modules["google.generativeai"]`
directly.  We must set both keys AND set the attribute on the google mock.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from app.providers.base import CompletionRequest, EmbedRequest, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_genai_mocks(
    response_text: str = "Gemini response",
    usage_prompt: int = 0,
    usage_candidates: int = 0,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Build mock objects for google.generativeai.

    Returns (mock_google_pkg, mock_genai_module, mock_model, mock_response).

    Usage:
        mock_google, mock_genai, mock_model, mock_response = _make_genai_mocks()
        with patch.dict(sys.modules, {
            "google": mock_google,
            "google.generativeai": mock_genai,
        }):
            ...
    """
    mock_response = MagicMock()
    mock_response.text = response_text

    if usage_prompt or usage_candidates:
        usage_meta = MagicMock()
        usage_meta.prompt_token_count = usage_prompt
        usage_meta.candidates_token_count = usage_candidates
        mock_response.usage_metadata = usage_meta
    else:
        mock_response.usage_metadata = None

    mock_model = MagicMock()
    mock_model.generate_content = MagicMock(return_value=mock_response)

    # mock_genai = google.generativeai module
    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model
    mock_genai.configure = MagicMock()

    # mock_google = the 'google' namespace package
    mock_google = MagicMock()
    mock_google.generativeai = mock_genai  # Critical: makes `import google.generativeai as genai` work

    return mock_google, mock_genai, mock_model, mock_response


def _patch() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, dict]:
    """Return mocks and the sys.modules dict to patch."""
    g, gai, model, resp = _make_genai_mocks()
    modules = {"google": g, "google.generativeai": gai}
    return g, gai, model, resp, modules


def _patch_with_text(text: str) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, dict]:
    g, gai, model, resp = _make_genai_mocks(response_text=text)
    modules = {"google": g, "google.generativeai": gai}
    return g, gai, model, resp, modules


def _patch_with_usage(prompt: int, cands: int) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, dict]:
    g, gai, model, resp = _make_genai_mocks(usage_prompt=prompt, usage_candidates=cands)
    modules = {"google": g, "google.generativeai": gai}
    return g, gai, model, resp, modules


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

def test_constructor_calls_configure_when_api_key_given() -> None:
    _, mock_genai, _, _, modules = _patch()
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        GeminiProvider(api_key="test-key")
    mock_genai.configure.assert_called_once_with(api_key="test-key")


def test_constructor_skips_configure_without_api_key() -> None:
    _, mock_genai, _, _, modules = _patch()
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        GeminiProvider(api_key=None)
    mock_genai.configure.assert_not_called()


def test_constructor_stores_default_and_embed_models() -> None:
    _, _, _, _, modules = _patch()
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        p = GeminiProvider(
            api_key="key",
            default_model="gemini-1.5-flash",
            embed_model="models/embedding-002",
        )
    assert p._default_model == "gemini-1.5-flash"
    assert p._embed_model == "models/embedding-002"


def test_constructor_raises_import_error_when_genai_missing() -> None:
    from unittest.mock import patch as _patch_fn
    import importlib

    import app.providers.gemini_provider as _mod
    # Save the current module so we can restore it
    saved_mod = sys.modules.get("app.providers.gemini_provider")

    # Simulate the package being absent by injecting None
    with _patch_fn.dict(
        sys.modules,
        {"google.generativeai": None, "google": MagicMock(), "app.providers.gemini_provider": None},  # type: ignore[dict-item]
    ):
        # Re-import the provider module to pick up the None genai module
        sys.modules.pop("app.providers.gemini_provider", None)
        try:
            import app.providers.gemini_provider as _fresh_mod
            with pytest.raises(ImportError, match="google-generativeai"):
                _fresh_mod.GeminiProvider(api_key="key")
        except ImportError:
            pass  # Acceptable if the module itself fails to import

    # Restore the module
    if saved_mod is not None:
        sys.modules["app.providers.gemini_provider"] = saved_mod


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_returns_text_response() -> None:
    from unittest.mock import patch as _patch_fn
    _, mock_genai, mock_model, mock_response, modules = _patch_with_usage(12, 30)

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key", default_model="gemini-1.5-pro")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="gemini-1.5-pro",
            )
        )
    assert result.content == "Gemini response"
    assert result.model == "gemini-1.5-pro"
    assert result.input_tokens == 12
    assert result.output_tokens == 30
    assert result.usage.total_tokens == 42


@pytest.mark.asyncio
async def test_complete_uses_default_model_when_empty() -> None:
    from unittest.mock import patch as _patch_fn
    _, mock_genai, _, _, modules = _patch()

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key", default_model="gemini-1.5-flash")
        result = await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="")
        )
    assert result.model == "gemini-1.5-flash"
    mock_genai.GenerativeModel.assert_called_with("gemini-1.5-flash")


@pytest.mark.asyncio
async def test_complete_with_system_kwarg() -> None:
    """request.system is prepended as [System]: prefix."""
    from unittest.mock import patch as _patch_fn
    _, mock_genai, mock_model, _, modules = _patch()
    captured_prompts: list[str] = []
    original_gc = mock_model.generate_content.return_value

    def _capture(prompt: str) -> MagicMock:
        captured_prompts.append(prompt)
        return original_gc

    mock_model.generate_content = _capture

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="gemini-1.5-pro",
                system="Be concise",
            )
        )
    assert any("[System]: Be concise" in p for p in captured_prompts)


@pytest.mark.asyncio
async def test_complete_extracts_system_from_messages_list() -> None:
    from unittest.mock import patch as _patch_fn
    _, mock_genai, mock_model, _, modules = _patch()
    captured_prompts: list[str] = []
    original_gc = mock_model.generate_content.return_value

    def _capture(prompt: str) -> MagicMock:
        captured_prompts.append(prompt)
        return original_gc

    mock_model.generate_content = _capture

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content="You are helpful"),
                    Message(role="user", content="What is 2+2?"),
                ],
                model="gemini-1.5-pro",
            )
        )
    assert any("[System]: You are helpful" in p for p in captured_prompts)


@pytest.mark.asyncio
async def test_complete_builds_conversation_from_multiple_messages() -> None:
    from unittest.mock import patch as _patch_fn
    _, mock_genai, mock_model, _, modules = _patch()
    captured_prompts: list[str] = []
    original_gc = mock_model.generate_content.return_value

    def _capture(prompt: str) -> MagicMock:
        captured_prompts.append(prompt)
        return original_gc

    mock_model.generate_content = _capture

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="user", content="Hello"),
                    Message(role="assistant", content="Hi there"),
                    Message(role="user", content="How are you?"),
                ],
                model="gemini-1.5-pro",
            )
        )
    prompt = captured_prompts[0]
    assert "[User]: Hello" in prompt
    assert "[Assistant]: Hi there" in prompt
    assert "[User]: How are you?" in prompt


@pytest.mark.asyncio
async def test_complete_zero_tokens_when_no_usage_metadata() -> None:
    from unittest.mock import patch as _patch_fn
    _, _, _, _, modules = _patch()

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="gemini-1.5-pro")
        )
    assert result.input_tokens == 0
    assert result.output_tokens == 0


@pytest.mark.asyncio
async def test_complete_response_without_text_attribute() -> None:
    """If response has no .text, content defaults to empty string."""
    from unittest.mock import patch as _patch_fn
    _, mock_genai, mock_model, mock_response, modules = _patch()
    del mock_response.text  # remove attribute so hasattr returns False

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="gemini-1.5-pro")
        )
    assert result.content == ""


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_nested_list_for_multiple_texts() -> None:
    from unittest.mock import patch as _patch_fn
    _, mock_genai, _, _, modules = _patch()
    nested = [[0.1, 0.2], [0.3, 0.4]]
    mock_genai.embed_content.return_value = {"embedding": nested}

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key")
        result = await provider.embed(EmbedRequest(texts=["a", "b"]))

    assert result.embeddings == nested


@pytest.mark.asyncio
async def test_embed_flat_list_for_single_text_is_wrapped() -> None:
    from unittest.mock import patch as _patch_fn
    _, mock_genai, _, _, modules = _patch()
    flat = [0.1, 0.2, 0.3]
    mock_genai.embed_content.return_value = {"embedding": flat}

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key")
        result = await provider.embed(EmbedRequest(texts=["single text"]))

    # Single text returns flat list → wrapped as [[0.1, 0.2, 0.3]]
    assert result.embeddings == [flat]


@pytest.mark.asyncio
async def test_embed_uses_configured_embed_model_and_task_type() -> None:
    from unittest.mock import patch as _patch_fn
    _, mock_genai, _, _, modules = _patch()
    captured: list[dict] = []

    def _capture(**kw: object) -> dict:
        captured.append(dict(kw))
        return {"embedding": [[0.0]]}

    mock_genai.embed_content = _capture

    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="key", embed_model="models/embedding-001")
        await provider.embed(EmbedRequest(texts=["text"]))

    assert captured[0]["model"] == "models/embedding-001"
    assert captured[0]["task_type"] == "retrieval_document"


# ---------------------------------------------------------------------------
# Capability flags
# ---------------------------------------------------------------------------

def test_supports_vision_true_for_gemini_model() -> None:
    from unittest.mock import patch as _patch_fn
    _, _, _, _, modules = _patch()
    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        p = GeminiProvider(api_key="key", default_model="gemini-1.5-pro")
        assert p.supports_vision() is True


def test_supports_vision_true_for_vision_model() -> None:
    from unittest.mock import patch as _patch_fn
    _, _, _, _, modules = _patch()
    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        p = GeminiProvider(api_key="key", default_model="my-vision-model")
        assert p.supports_vision() is True


def test_supports_vision_false_for_unrelated_model() -> None:
    from unittest.mock import patch as _patch_fn
    _, _, _, _, modules = _patch()
    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        p = GeminiProvider(api_key="key", default_model="gpt-like-model")
        assert p.supports_vision() is False


def test_supports_tool_use_always_true() -> None:
    from unittest.mock import patch as _patch_fn
    _, _, _, _, modules = _patch()
    with _patch_fn.dict(sys.modules, modules):
        from app.providers.gemini_provider import GeminiProvider
        p = GeminiProvider(api_key="key")
        assert p.supports_tool_use() is True
