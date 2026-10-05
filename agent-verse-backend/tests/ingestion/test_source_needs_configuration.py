"""L-02: a Source that cannot index anything is detected once, surfaced, and parked.

The live worker logged "source <id> has no collection_id; nothing can be indexed"
on every scheduled sync (~24 / 10 min): the beat due-scan kept dispatching the
legacy Source, every document failed in the pipeline's index stage and went to
the DLQ, and the DLQ retry replayed them again. Now the sync marks the Source
``needs_configuration`` (with the reason, visible in the API), the due-scan and
the DLQ retry stop picking it up, a manual sync is refused with the reason, and
setting a collection makes it schedulable again.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.api import ingestion as ingestion_mod
from app.ingestion.source_config import (
    CONFIG_STATUS_NEEDS_CONFIGURATION,
    CONFIG_STATUS_OK,
    SourceConfig,
    SourceFamily,
    configuration_problem,
)
from app.ingestion.source_store import SourceConfigStore
from tests.ingestion._lease import install_lease
from tests.api.test_ingestion_api import _auth, _client, _make_source


def _config(**overrides: Any) -> SourceConfig:
    base: dict[str, Any] = dict(
        source_id="src-legacy",
        tenant_id="t1",
        name="Legacy",
        family=SourceFamily.WEB,
        source_type="http",
        collection_id="",
    )
    base.update(overrides)
    return SourceConfig(**base)


def test_configuration_problem_names_the_missing_collection() -> None:
    assert configuration_problem(_config(collection_id="col-1")) is None
    problem = configuration_problem(_config())
    assert problem is not None and "collection" in problem


async def test_scheduled_sync_marks_the_source_once_and_runs_nothing() -> None:
    from app.ingestion.scheduler import _sync_source_async

    store = SourceConfigStore()
    await store.create(_config())
    tracker = install_lease(AsyncMock())
    tracker.acquire_lock.return_value = "lock-1"
    pipeline = AsyncMock()

    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, store),
        ),
        patch("app.ingestion.connector_registry.get_connector") as get_connector,
    ):
        result = await _sync_source_async(
            task=AsyncMock(), source_id="src-legacy", tenant_id="t1", triggered_by="scheduler"
        )

    assert result["skipped"] is True and result["reason"] == "needs_configuration"
    assert "collection" in result["detail"]
    get_connector.assert_not_called()
    tracker.create_job.assert_not_awaited()
    tracker.add_to_dlq.assert_not_awaited()
    pipeline.ingest.assert_not_awaited()
    tracker.release_lock.assert_awaited_once_with("src-legacy", "t1", "lock-1")
    stored = await store.get("src-legacy", "t1")
    assert stored is not None
    assert stored.config_status == CONFIG_STATUS_NEEDS_CONFIGURATION
    assert "collection" in stored.config_status_reason
    assert stored.consecutive_failures == 0  # not a failure to back off from
    # Parked: the beat due-scan no longer dispatches it.
    assert await store.list_due() == []


async def test_setting_a_collection_makes_the_source_schedulable_again() -> None:
    store = SourceConfigStore()
    await store.create(_config())
    await store.mark_needs_configuration("src-legacy", "t1", reason="no collection")
    assert await store.list_due() == []

    updated = await store.update("src-legacy", "t1", collection_id="col-1")

    assert updated is not None
    assert updated.config_status == CONFIG_STATUS_OK
    assert updated.config_status_reason == ""
    assert await store.list_due() == [("src-legacy", "t1")]


async def test_clearing_the_collection_parks_the_source() -> None:
    store = SourceConfigStore()
    await store.create(_config(collection_id="col-1"))
    updated = await store.update("src-legacy", "t1", collection_id="")
    assert updated is not None
    assert updated.config_status == CONFIG_STATUS_NEEDS_CONFIGURATION
    assert await store.list_due() == []


async def test_dlq_retry_leaves_entries_of_a_parked_source_untouched() -> None:
    from app.ingestion.scheduler import _retry_one_dlq_entry

    store = SourceConfigStore()
    await store.create(
        _config(
            config_status=CONFIG_STATUS_NEEDS_CONFIGURATION,
            config_status_reason="no collection",
        )
    )
    tracker = AsyncMock()
    pipeline = AsyncMock()
    entry = {
        "dlq_id": "d1",
        "tenant_id": "t1",
        "source_id": "src-legacy",
        "doc_id": "doc-1",
        "retry_count": 0,
        "raw_doc_json": '{"content": "hi", "content_type": "text/plain"}',
    }

    outcome = await _retry_one_dlq_entry(entry, tracker, pipeline, store)

    assert outcome == "skipped"
    pipeline.run.assert_not_awaited()
    tracker.increment_dlq_retry.assert_not_awaited()
    tracker.mark_dlq_permanent_failure.assert_not_awaited()


def test_api_surfaces_status_refuses_sync_and_patch_clears_it() -> None:
    store = SourceConfigStore()
    tracker = AsyncMock()
    tracker.acquire_lock.return_value = "job-1"
    client = _client(
        ingestion_job_tracker=tracker,
        ingestion_pipeline=AsyncMock(),
        ingestion_source_store=store,
    )

    created = client.post(
        "/sources",
        json={"name": "no-collection", "family": "web", "source_type": "http"},
        headers=_auth(),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["config_status"] == CONFIG_STATUS_NEEDS_CONFIGURATION
    assert body["needs_configuration"] is True
    assert "collection" in body["config_status_reason"]
    source_id = body["source_id"]

    with patch("app.ingestion.scheduler.sync_source_task") as task:
        refused = client.post(f"/sources/{source_id}/sync", headers=_auth())
    assert refused.status_code == 422
    assert "collection" in refused.json()["detail"]
    task.apply_async.assert_not_called()
    tracker.acquire_lock.assert_not_awaited()

    patched = client.patch(
        f"/sources/{source_id}", json={"collection_id": "col-1"}, headers=_auth()
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["config_status"] == CONFIG_STATUS_OK
    assert patched.json()["needs_configuration"] is False


def test_in_memory_api_fallback_also_tracks_the_status() -> None:
    source = _make_source(collection_id="")
    ingestion_mod._SOURCES[source.source_id] = source
    client = _client(ingestion_job_tracker=AsyncMock(), ingestion_pipeline=AsyncMock())
    try:
        patched = client.patch(
            f"/sources/{source.source_id}", json={"collection_id": "col-9"}, headers=_auth()
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["needs_configuration"] is False
    finally:
        ingestion_mod._SOURCES.pop(source.source_id, None)


@pytest.mark.parametrize("status", [CONFIG_STATUS_OK, CONFIG_STATUS_NEEDS_CONFIGURATION])
def test_status_round_trips_through_the_row_mapper(status: str) -> None:
    from app.ingestion.source_store import _row_to_config

    row = {
        "id": "s1",
        "tenant_id": "t1",
        "name": "n",
        "family": "web",
        "source_type": "http",
        "config_status": status,
        "config_status_reason": "why" if status != CONFIG_STATUS_OK else "",
    }
    cfg = _row_to_config(row)
    assert cfg.config_status == status
