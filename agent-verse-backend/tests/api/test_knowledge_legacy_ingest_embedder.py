"""/knowledge/ingest/email (and notion / gdrive-folder) must embed what they index.

Regression: these endpoints built ``IngestionOrchestrator(knowledge_store=...)``
with no embedder, so their chunks were stored without vectors and were
invisible to semantic / hybrid retrieval.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from tests.api.test_knowledge_rpa import _make_client

_EMAIL = "From: a@b.com\r\nTo: c@d.com\r\nSubject: Hi\r\nMessage-ID: <m1@b.com>\r\n\r\nHello there"


def test_email_ingest_uses_the_app_embedder() -> None:
    client: TestClient = _make_client()
    embedder = MagicMock(name="embedder")
    client.app.state.embedder = embedder  # type: ignore[attr-defined]
    captured: dict[str, Any] = {}

    class _Orch:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        ingest = AsyncMock(return_value=MagicMock(chunks_ingested=1, deduplicated=False))

    with patch("app.ingestion.orchestrator.IngestionOrchestrator", _Orch):
        client.post(
            "/knowledge/ingest/email", json={"raw_email": _EMAIL, "collection_id": "col-1"}
        )
    assert captured.get("embedder") is embedder
