"""The Sources API plus the worker body a queued sync runs — for connector
integration tests that drive a real Source end to end (create -> health -> sync).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.source_config import PipelineResult
from app.ingestion.source_store import SourceConfigStore
from tests.api.test_ingestion_api import _auth, _client


class RecordingPipeline:
    def __init__(self) -> None:
        self.docs: list[Any] = []

    async def ingest(self, raw_doc: Any, config: Any) -> PipelineResult:
        self.docs.append(raw_doc)
        return PipelineResult(
            doc_id=raw_doc.doc_id,
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            status="indexed",
        )


class SourcesHarness:
    def __init__(self, *, family: str, source_type: str) -> None:
        self.family = family
        self.source_type = source_type
        self.store = SourceConfigStore()
        self.tracker = IngestionJobTracker()
        self.client = _client(
            ingestion_source_store=self.store,
            ingestion_job_tracker=self.tracker,
            ingestion_pipeline=MagicMock(),
        )

    def create(self, connection_config: dict[str, Any], **extra: Any) -> dict[str, Any]:
        resp = self.client.post(
            "/sources",
            headers=_auth(),
            json={
                "name": f"{self.source_type} source",
                "family": self.family,
                "source_type": self.source_type,
                "connection_config": connection_config,
                **extra,
            },
        )
        assert resp.status_code == 201, resp.text
        body: dict[str, Any] = resp.json()
        return body

    def health(self, source_id: str) -> dict[str, Any]:
        resp = self.client.get(f"/sources/{source_id}/health", headers=_auth())
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    async def sync(self, source_id: str) -> tuple[dict[str, Any], RecordingPipeline]:
        from app.ingestion.scheduler import _sync_source_async

        with patch("app.ingestion.scheduler.sync_source_task") as task:
            resp = self.client.post(f"/sources/{source_id}/sync", headers=_auth())
            assert resp.status_code == 202, resp.text
            assert resp.json()["status"] == "queued", resp.json()
            queued = task.apply_async.call_args.kwargs["kwargs"]

        pipeline = RecordingPipeline()
        worker_task = MagicMock()
        worker_task.retry.side_effect = lambda exc, **_kw: exc  # surface the real error
        with patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(self.tracker, pipeline, self.store),
        ):
            result = await _sync_source_async(task=worker_task, **queued)
        return result, pipeline
