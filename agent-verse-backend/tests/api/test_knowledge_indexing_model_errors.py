"""KB-14: an indexing-model breaker-open / timeout is reported as such, not as a
persistence outage.

A circuit-open or timeout from the RAPTOR / agentic-chunking model fell to the
catch-all and was answered 503 "Knowledge persistence is unavailable".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.providers.circuit_breaker import ProviderCircuitOpenError
from tests.api.test_knowledge_extra4 import H, _create_collection, _make_app


@pytest.mark.parametrize(
    ("error", "status", "phrase"),
    [
        (ProviderCircuitOpenError("LLM provider circuit open for m"), 503, "indexing model"),
        (TimeoutError("LLM provider call timed out"), 504, "indexing model"),
    ],
)
def test_indexing_model_failures_are_not_persistence_outages(
    error: Exception, status: int, phrase: str
) -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cid = _create_collection(client)
    with patch(
        "app.ingestion.orchestrator.IngestionOrchestrator.ingest",
        new=AsyncMock(side_effect=error),
    ):
        resp = client.post(
            f"/knowledge/collections/{cid}/documents",
            json={"content": "Some document content to index."},
            headers=H,
        )
    assert resp.status_code == status, resp.text
    assert phrase in resp.json()["detail"]
    assert "persistence" not in resp.json()["detail"]


def test_the_breaker_open_error_is_still_a_runtime_error() -> None:
    assert issubclass(ProviderCircuitOpenError, RuntimeError)
