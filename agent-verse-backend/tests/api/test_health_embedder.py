"""GET /health surfaces whether the embedder is available — without an upload.

An unavailable embedder used to be discoverable only by uploading a file and
getting 503 "Embedding provider is unavailable". /health now reports it as a
non-fatal capability (it never flips readiness: deployments without
embeddings are legitimate), with no error text on the unauthenticated route.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

_EMBED_ENV = (
    "OPENAI_API_KEY",
    "VOYAGE_API_KEY",
    "GOOGLE_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "SENTENCE_TRANSFORMERS_MODEL",
    "NVIDIA_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _EMBED_ENV:
        monkeypatch.delenv(name, raising=False)


def test_health_reports_embedder_not_configured_without_failing_readiness() -> None:
    client = TestClient(create_app(settings=Settings(_env_file=None)))  # type: ignore[call-arg]
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["capabilities"]["embedder"]["status"] == "not_configured"


def test_health_reports_a_failed_embedder_as_unavailable_without_error_text() -> None:
    def _boom(model_name: str = "") -> None:
        raise OSError("secret-path /home/ops/models not found")

    settings = Settings(_env_file=None, sentence_transformers_model="bad-model")  # type: ignore[call-arg]
    with patch("app.providers.voyage_provider.LocalEmbedProvider", _boom):
        app = create_app(settings=settings)
    body = TestClient(app).get("/health").json()
    embedder = body["capabilities"]["embedder"]
    assert embedder["status"] == "unavailable"
    assert embedder["failed_providers"] == ["sentence_transformers"]
    assert "secret-path" not in str(body)


def test_health_reports_a_working_embedder() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, embedding_base_url="https://embed.example.com/v1", embedding_model="m-1"
    )
    body = TestClient(create_app(settings=settings)).get("/health").json()
    embedder = body["capabilities"]["embedder"]
    assert embedder["status"] == "available"
    assert embedder["provider"] == "dedicated"
    assert embedder["model"] == "m-1"
