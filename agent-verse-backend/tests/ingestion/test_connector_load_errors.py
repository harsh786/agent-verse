"""SRC-RSS: a connector module that fails to import must surface as an honest error.

``load_all_connectors`` swallowed the ImportError at DEBUG, so the Sources UI got
a bare "No connector registered" from the health probe, ``POST /sync`` queued a
job anyway, and the worker's ``get_connector`` KeyError escaped before the job
record existed — leaving the source lock held and nothing for the UI to show.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

import app.api.ingestion as ingestion_mod
from app.ingestion import connector_registry
from app.ingestion.source_config import IngestionJob, SourceFamily
from tests.ingestion._lease import install_lease
from tests.api.test_ingestion_api import _auth, _client, _make_source

_RSS_MODULE = "app.ingestion.connectors.rss_connector"


@pytest.fixture
def rss_module_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rss connector module cannot be imported, and nothing registered it."""
    connector_registry.load_all_connectors()
    for source_type in ("rss", "atom"):
        monkeypatch.delitem(connector_registry._REGISTRY, source_type, raising=False)
    monkeypatch.setitem(sys.modules, _RSS_MODULE, None)  # import raises ImportError
    monkeypatch.setattr(connector_registry, "_LOAD_ERRORS", {})
    connector_registry.load_all_connectors()


def _rss_source() -> object:
    return _make_source(
        family=SourceFamily.WEB, source_type="rss", connection_config={"url": "https://x.test/f"}
    )


def test_get_connector_names_the_load_failure(rss_module_broken: None) -> None:
    with pytest.raises(KeyError) as info:
        connector_registry.get_connector("rss")

    message = str(info.value.args[0])
    assert "failed to load" in message
    assert _RSS_MODULE in message


def test_health_probe_returns_the_honest_error(rss_module_broken: None) -> None:
    source = _rss_source()
    ingestion_mod._SOURCES[source.source_id] = source  # type: ignore[attr-defined]

    resp = _client().get(f"/sources/{source.source_id}/health", headers=_auth())  # type: ignore[attr-defined]

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "failed to load" in body["error"]


def test_sync_is_refused_with_a_clear_error_instead_of_queued(rss_module_broken: None) -> None:
    source = _rss_source()
    ingestion_mod._SOURCES[source.source_id] = source  # type: ignore[attr-defined]
    tracker = AsyncMock()
    tracker.acquire_lock.return_value = "job-1"

    with patch("app.ingestion.scheduler.sync_source_task") as task:
        resp = _client(ingestion_job_tracker=tracker, ingestion_pipeline=AsyncMock()).post(
            f"/sources/{source.source_id}/sync",  # type: ignore[attr-defined]
            headers=_auth(),
        )

    assert resp.status_code == 422, resp.text
    assert "failed to load" in resp.json()["detail"]
    task.apply_async.assert_not_called()
    tracker.acquire_lock.assert_not_awaited()


async def test_worker_records_a_failed_job_and_releases_the_lock(
    rss_module_broken: None,
) -> None:
    from app.ingestion.scheduler import _sync_source_async

    source = _rss_source()
    job = IngestionJob(
        job_id="job-7",
        source_id=source.source_id,  # type: ignore[attr-defined]
        tenant_id=source.tenant_id,  # type: ignore[attr-defined]
        status="running",
        sync_mode="incremental",
    )
    tracker = install_lease(AsyncMock())
    tracker.create_job = AsyncMock(return_value=job)
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=source)

    with patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(tracker, AsyncMock(), source_store),
    ):
        result = await _sync_source_async(
            task=None,
            source_id=source.source_id,  # type: ignore[attr-defined]
            tenant_id=source.tenant_id,  # type: ignore[attr-defined]
            triggered_by="manual",
            job_id="job-7",
        )

    assert "failed to load" in result["error"]
    tracker.complete_job.assert_awaited_once()
    assert "failed to load" in tracker.complete_job.await_args.kwargs["error"]
    # Released by its holder: with the token (TG-12), never unowned.
    tracker.release_lock.assert_awaited_once_with(
        source.source_id,  # type: ignore[attr-defined]
        source.tenant_id,  # type: ignore[attr-defined]
        "job-7",
    )
