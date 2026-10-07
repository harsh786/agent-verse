"""Every process must resolve the SAME embedder from the SAME configuration — loudly.

Regressions covered (file uploads failed with 503 "Embedding provider is
unavailable" while the configuration looked correct):

* ``SENTENCE_TRANSFORMERS_MODEL`` was read only from ``os.environ``. pydantic
  Settings reads ``.env`` without exporting it, so a model set only in ``.env``
  was invisible under ``uv run uvicorn``.
* Every provider failure was swallowed (``except Exception: pass``) and the
  providers were an ``if/elif`` chain, so a set-but-failing earlier key silently
  skipped the remaining providers and the embedder became ``None``.
* The NVIDIA / on-prem embedding endpoint was only copied into the embedding
  settings inside ``create_app``; the Celery worker (``_build_worker_ingestion``)
  never ran that, so worker ingestion had no embedder on NVIDIA-only config.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.providers.embedder_factory import (
    build_query_embedder,
    embedder_dimension,
    embedder_model_name,
    resolve_embedder,
)

# Strip ambient provider env AND clear the cached Settings (tests/conftest.py
# isolate_provider_env): an earlier test's cached get_settings() — e.g. one built
# while OPENAI_API_KEY was set — must not leak a key into "nothing configured".
_ISOLATE_PROVIDER_ENV = True

_EMBED_ENV = (
    "OPENAI_API_KEY",
    "VOYAGE_API_KEY",
    "GOOGLE_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_API_KEY",
    "SENTENCE_TRANSFORMERS_MODEL",
    "OPENAI_BASE_URL",
)


@pytest.fixture(autouse=True)
def _clean_embed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _EMBED_ENV:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: Any) -> Settings:
    # _env_file=None: the test must not depend on a developer's local .env.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


class _FakeLocal:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self.embedding_dim = 768


def test_sentence_transformers_model_is_a_settings_field() -> None:
    assert _settings(sentence_transformers_model="all-mpnet-base-v2").sentence_transformers_model


def test_local_model_set_only_in_settings_is_used() -> None:
    """A model configured in .env (Settings) — not exported to os.environ — is honoured."""
    s = _settings(sentence_transformers_model="all-mpnet-base-v2")
    with patch("app.providers.voyage_provider.LocalEmbedProvider", _FakeLocal):
        embedder = build_query_embedder(s)
    assert isinstance(embedder, _FakeLocal)
    assert embedder._model_name == "all-mpnet-base-v2"


def test_env_var_is_still_a_fallback_for_the_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_MODEL", "all-mpnet-base-v2")
    with patch("app.providers.voyage_provider.LocalEmbedProvider", _FakeLocal):
        embedder = build_query_embedder(_settings())
    assert isinstance(embedder, _FakeLocal)


def test_failing_local_model_is_logged_at_error_with_provider_and_reason() -> None:
    def _boom(model_name: str = "") -> Any:
        raise OSError(f"model {model_name} not found on the hub")

    s = _settings(sentence_transformers_model="no-such-model")
    log = MagicMock()
    with (
        patch("app.providers.voyage_provider.LocalEmbedProvider", _boom),
        patch("app.providers.embedder_factory.logger", log),
    ):
        resolution = resolve_embedder(s)
    assert resolution.embedder is None
    assert resolution.status == "unavailable"
    assert resolution.errors and resolution.errors[0][0] == "sentence_transformers"
    assert "no-such-model" in resolution.errors[0][1]
    assert any(
        c.kwargs.get("provider") == "sentence_transformers"
        and "no-such-model" in c.kwargs.get("reason", "")
        for c in log.error.call_args_list
    ), log.error.call_args_list


def test_a_failing_earlier_provider_does_not_skip_later_ones() -> None:
    """Voyage key set but Voyage fails to build → the local model is still tried."""

    def _voyage_boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("voyage client init failed")

    s = _settings(voyage_api_key="pa-bad", sentence_transformers_model="all-mpnet-base-v2")
    log = MagicMock()
    with (
        patch("app.providers.voyage_provider.VoyageProvider", _voyage_boom),
        patch("app.providers.voyage_provider.LocalEmbedProvider", _FakeLocal),
        patch("app.providers.embedder_factory.logger", log),
    ):
        resolution = resolve_embedder(s)
    assert isinstance(resolution.embedder, _FakeLocal)
    assert resolution.provider == "sentence_transformers"
    assert ("voyage", "RuntimeError: voyage client init failed") in resolution.errors
    assert any(c.kwargs.get("provider") == "voyage" for c in log.error.call_args_list)


def test_nothing_configured_is_reported_not_configured() -> None:
    resolution = resolve_embedder(_settings())
    assert resolution.embedder is None
    assert resolution.status == "not_configured"
    assert resolution.errors == []


def test_nvidia_only_config_resolves_the_nvidia_embedder_without_create_app() -> None:
    s = _settings(nvidia_api_key="nvapi-test", nvidia_embed_model="nvidia/nemotron-3-embed-1b")
    resolution = resolve_embedder(s)
    assert resolution.embedder is not None
    assert resolution.provider == "dedicated"
    assert embedder_model_name(resolution.embedder) == "nvidia/nemotron-3-embed-1b"
    assert resolution.dimension == 2048


def test_onprem_only_config_resolves_the_onprem_embedder_without_create_app() -> None:
    s = _settings(
        onprem_enabled=True,
        onprem_qwen_base_url="http://host:30080/v1",
        onprem_embedding_base_url="http://host:30082/v1",
    )
    resolution = resolve_embedder(s)
    assert embedder_model_name(resolution.embedder) == "Qwen/Qwen3-Embedding-0.6B"
    assert resolution.dimension == 1024


def test_explicit_embedding_endpoint_beats_the_nvidia_autofill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embed.example.com/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding-8b")
    s = _settings(nvidia_api_key="nvapi-test", nvidia_embed_model="nvidia/nemotron-3-embed-1b")
    assert embedder_model_name(build_query_embedder(s)) == "qwen3-embedding-8b"


def test_worker_ingestion_gets_the_nvidia_embedder() -> None:
    """The Celery worker never runs create_app; it must still see the NVIDIA embedder."""
    s = _settings(nvidia_api_key="nvapi-test", nvidia_embed_model="nvidia/nemotron-3-embed-1b")
    from app.ingestion.scheduler import _build_worker_ingestion

    with (
        patch("app.core.config.get_settings", return_value=s),
        patch("app.db.session.get_session_factory"),
        patch("app.db.session.get_system_session_factory"),
    ):
        _tracker, pipeline, _store = _build_worker_ingestion()
    embedder = pipeline._embedder  # type: ignore[attr-defined]
    assert embedder is not None
    assert embedder_model_name(embedder) == "nvidia/nemotron-3-embed-1b"


def test_local_embed_provider_reports_its_model_and_real_dimension() -> None:
    class _ST:
        def __init__(self, name: str) -> None:
            self.name = name

        def get_embedding_dimension(self) -> int:
            return 768

    with patch("sentence_transformers.SentenceTransformer", _ST):
        from app.providers.voyage_provider import LocalEmbedProvider

        provider = LocalEmbedProvider(model_name="all-mpnet-base-v2")
    assert embedder_model_name(provider) == "all-mpnet-base-v2"
    assert embedder_dimension(provider) == 768


def test_embedder_dimension_unknown_is_none() -> None:
    assert embedder_dimension(None) is None
    assert embedder_dimension(object()) is None
