"""a04-F067-01 / a04-F070-03: legacy GitHub / Confluence / Jira / Slack ingest is a durable job.

The four routes fetched, screened and embedded a whole source (thousands of
pages / messages) synchronously inside the HTTP request on the API replica,
through their own ingestor stack (separate Confluence / Jira clients and fixed
1,200-character windows) beside the connector framework. Now:

* the route validates, records a durable ingestion job and queues it (202);
  nothing is fetched in the request, and the tenant's token reaches the broker
  only vault-encrypted;
* the worker runs the REGISTERED connector through the shared ingestion
  pipeline (screening, content-type chunking, embedding, dedup, index) and
  records the outcome on the job (``GET /knowledge/ingest/jobs/{id}``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.ingestion.legacy_source_jobs import (
    LegacySourceJobLeaseHeldError,
    build_source_config,
    decrypt_secrets,
    encrypt_secrets,
    ephemeral_source_id,
    run_legacy_source_ingest,
)
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument
from app.rag.models import KnowledgeCollection
from tests.api.test_knowledge_persistence import API_KEY, TENANT, _app, _AwaitedStore

_H = {"X-API-Key": API_KEY}
_SECRET = "jira-" + "s3cr3t-api-token-value"  # split: never a provider-key literal

_ROUTES: dict[str, dict[str, Any]] = {
    "github": {"owner": "acme", "repo": "widgets", "token": "ghp" + "_tenantowntoken0001"},
    "confluence": {
        "base_url": "https://acme.atlassian.net/wiki",
        "space_key": "ENG",
        "token": _SECRET,
        "user": "me@acme.test",
        "max_pages": 25,
    },
    "jira": {
        "base_url": "https://acme.atlassian.net",
        "project_key": "OPS",
        "token": _SECRET,
        "user": "me@acme.test",
        "jql_extra": "status = Done",
        "max_issues": 40,
    },
    "slack": {"channel_id": "C0123", "token": "xox" + "b-tenant-bot-token", "channel_name": "#eng"},
}


class _JobStore(_AwaitedStore):
    """The persistence fake, with the durable-job calls the worker makes."""

    def __init__(self) -> None:
        super().__init__()
        self.seed_collection(KnowledgeCollection(name="kb", collection_id="collection-1"))
        self.created: list[dict[str, Any]] = []

    async def create_ingestion_job_async(self, **kwargs: Any) -> str:  # type: ignore[override]
        self.created.append(kwargs)
        return await super().create_ingestion_job_async(**kwargs)

    async def claim_ingestion_job_async(  # type: ignore[override]
        self, job_id: str, **kwargs: Any
    ) -> bool:
        kwargs.pop("source_type", None)
        return await super().claim_ingestion_job_async(job_id, **kwargs)

    async def complete_ingestion_job_async(
        self,
        job_id: str,
        *,
        lease_owner: str,
        chunk_count: int,
        tenant_ctx: Any,
        message: str | None = None,
    ) -> bool:
        job = self.jobs[job_id]
        if job["status"] != "running" or job.get("lease_owner") != lease_owner:
            return False
        job.update(status="completed", chunk_count=chunk_count, error_message=message)
        return True


def _post(kind: str, store: _JobStore, **overrides: Any) -> Any:
    body = {"collection_id": "collection-1", **_ROUTES[kind], **overrides}
    return TestClient(_app(store), raise_server_exceptions=False).post(
        f"/knowledge/ingest/{kind}", json=body, headers=_H
    )


# ── the routes ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", list(_ROUTES))
def test_route_queues_a_durable_job_and_fetches_nothing(kind: str) -> None:
    store = _JobStore()
    no_fetch = AssertionError("the request must not read the source")
    with (
        patch("app.ingestion.legacy_source_jobs.ingest_legacy_source_task") as task,
        patch("app.knowledge.ingestors.github_ingestor.GitHubIngestor.repo_files",
              side_effect=no_fetch),
        patch("app.knowledge.ingestors.slack_ingestor.SlackIngestor.ingest_channel",
              side_effect=no_fetch),
        patch("app.ingestion.connector_egress.source_client", side_effect=no_fetch),
    ):  # fmt: skip
        resp = _post(kind, store)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "ingestion_started"
    assert body["job_id"] == "job-1" and body["source_type"] == kind
    assert store.jobs["job-1"]["status"] == "queued"
    assert store.created[0]["source_type"] == kind

    task.apply_async.assert_called_once()
    call = task.apply_async.call_args.kwargs
    assert call["queue"] == "ingestion"
    params = call["kwargs"]
    assert params["kind"] == kind and params["job_id"] == "job-1"
    assert params["tenant_id"] == TENANT.tenant_id
    # The token rides the broker only vault-encrypted …
    token = _ROUTES[kind]["token"]
    assert token not in json.dumps(params)
    # … and the worker gets it back.
    plain = decrypt_secrets(params["connection_config"])
    assert token in json.dumps(plain)


def test_route_maps_the_request_onto_the_connector_config() -> None:
    store = _JobStore()
    with patch("app.ingestion.legacy_source_jobs.ingest_legacy_source_task") as task:
        assert _post("jira", store).status_code == 202
        assert _post("confluence", _JobStore()).status_code == 202
    jira, confluence = (c.kwargs["kwargs"] for c in task.apply_async.call_args_list)
    jira_cc = decrypt_secrets(jira["connection_config"])
    assert jira_cc["project_keys"] == ["OPS"] and jira_cc["jql_extra"] == "status = Done"
    assert jira_cc["username"] == "me@acme.test" and jira_cc["newest_first"] is True
    assert jira["max_documents"] == 40
    conf_cc = decrypt_secrets(confluence["connection_config"])
    assert conf_cc["space_keys"] == ["ENG"] and conf_cc["content_types"] == ["page"]
    assert confluence["max_documents"] == 25


def test_job_status_is_readable_through_the_jobs_endpoint() -> None:
    store = _JobStore()
    with patch("app.ingestion.legacy_source_jobs.ingest_legacy_source_task"):
        job_id = _post("slack", store).json()["job_id"]
    resp = TestClient(_app(store)).get(f"/knowledge/ingest/jobs/{job_id}", headers=_H)
    assert resp.status_code == 200 and resp.json()["status"] == "queued"


def test_internal_base_url_is_refused_before_anything_is_queued() -> None:
    store = _JobStore()
    with patch("app.ingestion.legacy_source_jobs.ingest_legacy_source_task") as task:
        resp = _post("confluence", store, base_url="http://169.254.169.254/latest")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "base_url is not allowed"
    task.apply_async.assert_not_called()
    assert store.jobs == {}


def test_unknown_collection_is_404() -> None:
    store = _JobStore()
    with patch("app.ingestion.legacy_source_jobs.ingest_legacy_source_task") as task:
        resp = _post("slack", store, collection_id="nope")
    assert resp.status_code == 404
    task.apply_async.assert_not_called()


def test_per_tenant_cap_is_429() -> None:
    store = _JobStore()
    store.count_active_ingestion_jobs_async = AsyncMock(return_value=99)  # type: ignore[method-assign]
    with patch("app.ingestion.legacy_source_jobs.ingest_legacy_source_task") as task:
        resp = _post("github", store)
    assert resp.status_code == 429
    task.apply_async.assert_not_called()


def test_a_queue_outage_is_503_and_the_job_is_failed() -> None:
    store = _JobStore()
    with patch("app.ingestion.legacy_source_jobs.ingest_legacy_source_task") as task:
        task.apply_async.side_effect = ConnectionError("broker down")
        resp = _post("slack", store)
    assert resp.status_code == 503
    assert store.jobs["job-1"]["status"] == "failed"


def test_routes_answer_202_with_the_durable_contract() -> None:
    from app.api.knowledge import router

    paths = {f"/knowledge/ingest/{k}" for k in _ROUTES}
    codes = {r.path: r.status_code for r in router.routes if getattr(r, "path", "") in paths}
    assert codes == dict.fromkeys(paths, 202)


def test_the_legacy_confluence_and_jira_ingestors_are_gone() -> None:
    # One connector stack: the routes run app.ingestion.connectors.*.
    import importlib

    for name in ("confluence_ingestor", "jira_ingestor"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"app.knowledge.ingestors.{name}")


def test_the_worker_task_is_registered_on_the_ingestion_queue() -> None:
    from app.ingestion.legacy_source_jobs import ingest_legacy_source_task
    from app.scaling.celery_app import celery_app

    assert "app.ingestion.legacy_source_jobs" in celery_app.conf.include
    assert ingest_legacy_source_task.acks_late is True
    assert ingest_legacy_source_task.name.startswith("ingestion.")


# ── the worker ───────────────────────────────────────────────────────────────

_SSN = "123-45-6789"


def _doc(config: Any, n: int, text: str | None = None) -> RawDocument:
    return RawDocument(
        doc_id=f"issue-{n}",
        source_id=config.source_id,
        tenant_id=config.tenant_id,
        content=(text or f"Issue OPS-{n}: the deployment runbook step {n} failed. " * 6).encode(),
        content_type="text/plain",
        source_url=f"https://acme.atlassian.net/browse/OPS-{n}",
    )


class _Connector:
    def __init__(self, docs: list[RawDocument], error: Exception | None = None) -> None:
        self.docs = docs
        self.error = error
        self.closed = False
        self.configs: list[Any] = []

    def get_delta(self, config: Any, cursor: str | None) -> AsyncIterator[tuple[Any, str]]:
        self.configs.append(config)
        connector = self

        async def _gen() -> AsyncIterator[tuple[Any, str]]:
            try:
                for doc in connector.docs:
                    yield doc, ""
                if connector.error is not None:
                    raise connector.error
            finally:
                connector.closed = True

        return _gen()


class _KB:
    """Chunk store with content-hash dedup (Stage 3) for the real pipeline."""

    def __init__(self) -> None:
        self.chunks: list[Any] = []

    async def exists_by_hash(self, *, content_hash: str, **_: Any) -> bool:
        return any(c.metadata.get("doc_content_hash") == content_hash for c in self.chunks)

    async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
        self.chunks.extend(chunks)
        return [c.chunk_id for c in chunks]


class _Embedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        self.seen.extend(request.texts)
        return EmbedResponse(embeddings=[[0.25] * 8 for _ in request.texts], model="m")


def _job_store() -> _JobStore:
    store = _JobStore()
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://acme.atlassian.net#project=OPS",
    }
    return store


async def _run(
    connector: Any, *, store: _JobStore | None = None, pipeline: Any = None,
    max_documents: int | None = None, kb: _KB | None = None, embedder: Any = None,
) -> tuple[_JobStore, _KB, Any]:  # fmt: skip
    store = store or _job_store()
    kb = kb or _KB()
    embedder = embedder or _Embedder()
    if pipeline is None:
        from app.ingestion.pii import build_pii_analyzer

        # Wired like the worker's pipeline (legacy_source_jobs._worker_services).
        pipeline = IngestionPipeline(
            knowledge_store=kb, embedder=embedder, pii_analyzer=build_pii_analyzer()
        )
    await run_legacy_source_ingest(
        job_id="job-1",
        tenant_ctx=TENANT,
        kind="jira",
        collection_id="collection-1",
        source_url="https://acme.atlassian.net#project=OPS",
        connection_config={"base_url": "https://acme.atlassian.net", "api_token": _SECRET},
        max_documents=max_documents,
        store=store,
        pipeline=pipeline,
        lease_seconds=60,
        connector=connector,
    )
    return store, kb, embedder


def _cfg() -> Any:
    return build_source_config(
        kind="jira", tenant_id=TENANT.tenant_id, collection_id="collection-1",
        source_url="https://acme.atlassian.net#project=OPS", connection_config={},
    )  # fmt: skip


async def test_worker_indexes_every_document_through_the_shared_pipeline() -> None:
    config = _cfg()
    sensitive = f"Issue OPS-9: payroll export for employee with SSN {_SSN} keeps failing. " * 4
    connector = _Connector([_doc(config, 1), _doc(config, 2), _doc(config, 3, sensitive)])

    store, kb, embedder = await _run(connector)

    job = store.jobs["job-1"]
    assert job["status"] == "completed", job
    assert job["chunk_count"] == len(kb.chunks) > 0
    assert job["error_message"] is None
    assert {c.document_id for c in kb.chunks} == {"issue-1", "issue-2", "issue-3"}
    # Screened by the pipeline (Stage 6) before embedding and storage.
    assert all(_SSN not in t for t in embedder.seen)
    assert all(_SSN not in c.content for c in kb.chunks)
    # The connector ran with the ephemeral, never-persisted Source config.
    [used] = connector.configs
    assert used.source_type == "jira" and used.collection_id == "collection-1"
    assert used.connection_config["api_token"] == _SECRET


async def test_reingesting_the_same_source_adds_no_duplicates() -> None:
    config = _cfg()
    kb = _KB()
    await _run(_Connector([_doc(config, 1), _doc(config, 2)]), kb=kb)
    first = len(kb.chunks)
    store, kb, _ = await _run(_Connector([_doc(config, 1), _doc(config, 2)]), kb=kb)
    assert len(kb.chunks) == first
    assert store.jobs["job-1"]["status"] == "completed"
    assert store.jobs["job-1"]["chunk_count"] == 0


async def test_document_cap_stops_reading_and_is_noted() -> None:
    config = _cfg()
    connector = _Connector([_doc(config, n) for n in range(5)])
    store, kb, _ = await _run(connector, max_documents=2)
    assert {c.document_id for c in kb.chunks} == {"issue-0", "issue-1"}
    assert connector.closed  # the generator was closed, not drained
    assert "2-document limit" in store.jobs["job-1"]["error_message"]


async def test_a_source_error_after_some_documents_is_a_noted_partial_success() -> None:
    config = _cfg()
    connector = _Connector([_doc(config, 1)], error=RuntimeError("HTTP 500 from 10.1.2.3"))
    store, kb, _ = await _run(connector)
    job = store.jobs["job-1"]
    assert job["status"] == "completed" and kb.chunks
    assert "could not be read" in job["error_message"]
    assert "10.1.2.3" not in job["error_message"]


async def test_a_source_that_cannot_be_read_fails_the_job_with_a_safe_reason() -> None:
    from app.ingestion.connector_egress import ConnectorEgressBlockedError

    store, kb, _ = await _run(_Connector([], error=ConnectorEgressBlockedError("10.0.0.5")))
    job = store.jobs["job-1"]
    assert job["status"] == "failed"
    assert job["error_message"] == "jira: the source address is not allowed"
    assert kb.chunks == []


async def test_every_document_failing_fails_the_job() -> None:
    config = _cfg()
    pipeline = MagicMock()
    pipeline.ingest = AsyncMock(
        return_value=MagicMock(status="failed", error="embedder down", chunks_created=0)
    )
    store, _, _ = await _run(_Connector([_doc(config, 1), _doc(config, 2)]), pipeline=pipeline)
    job = store.jobs["job-1"]
    assert job["status"] == "failed" and "every document failed" in job["error_message"]


async def test_a_live_lease_elsewhere_raises_for_a_retry() -> None:
    store = _job_store()
    store.jobs["job-1"].update(status="running", lease_owner="other-worker")
    connector = _Connector([])
    with pytest.raises(LegacySourceJobLeaseHeldError) as raised:
        await _run(connector, store=store)
    assert raised.value.retry_after_seconds == 61
    assert connector.configs == []  # nothing fetched
    assert store.jobs["job-1"]["status"] == "running"


async def test_a_duplicate_delivery_of_a_finished_job_is_a_no_op() -> None:
    store = _job_store()
    store.jobs["job-1"].update(status="completed", chunk_count=7)
    connector = _Connector([])
    await _run(connector, store=store)
    assert connector.configs == []
    assert store.jobs["job-1"]["chunk_count"] == 7


def test_task_decrypts_the_config_and_runs_with_the_worker_services() -> None:
    from app.ingestion import legacy_source_jobs as mod

    captured: dict[str, Any] = {}

    async def _fake_run(**kwargs: Any) -> None:
        captured.update(kwargs)

    with (
        patch.object(mod, "_worker_services", return_value=("store", "pipeline")),
        patch.object(mod, "run_legacy_source_ingest", _fake_run),
    ):
        mod.ingest_legacy_source_task.run(
            job_id="job-1",
            tenant_id=TENANT.tenant_id,
            kind="slack",
            collection_id="collection-1",
            source_url="slack:C1",
            connection_config=encrypt_secrets({"bot_token": "xox" + "b-1", "channels": ["C1"]}),
            max_documents=None,
        )
    assert captured["connection_config"] == {"bot_token": "xox" + "b-1", "channels": ["C1"]}
    assert captured["store"] == "store" and captured["pipeline"] == "pipeline"
    assert captured["tenant_ctx"].tenant_id == TENANT.tenant_id


def test_ephemeral_source_ids_are_stable_and_scoped() -> None:
    a = ephemeral_source_id("t1", "jira", "u#project=OPS", "c1")
    assert a == ephemeral_source_id("t1", "jira", "u#project=OPS", "c1")
    assert a != ephemeral_source_id("t2", "jira", "u#project=OPS", "c1")
    assert a != ephemeral_source_id("t1", "jira", "u#project=OPS", "c2")
    assert a != ephemeral_source_id("t1", "jira", "u#project=WEB", "c1")
    assert len(a) <= 64


# ── connector options the legacy routes rely on ──────────────────────────────


async def test_jira_connector_applies_jql_extra_and_newest_first() -> None:
    from app.ingestion.connectors.jira_connector import JiraConnector

    seen: list[str] = []

    class _Resp:
        is_success = True
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"issues": [], "total": 0}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def get(self, url: str, params: dict[str, Any], **kw: Any) -> _Resp:
            seen.append(params["jql"])
            return _Resp()

    config = build_source_config(
        kind="jira", tenant_id="t", collection_id="c", source_url="u",
        connection_config={"base_url": "https://acme.atlassian.net", "project_keys": ["OPS"],
                           "jql_extra": "status = Done", "newest_first": True},
    )  # fmt: skip
    with (
        patch("app.ingestion.connectors.jira_connector.assert_source_url"),
        patch("app.ingestion.connectors.jira_connector.source_client", return_value=_Client()),
    ):
        docs = [d async for d in JiraConnector().get_delta(config, None)]
    assert docs == []
    assert seen == ['project in ("OPS") AND (status = Done) ORDER BY updated DESC']


async def test_slack_connector_stamps_the_channel_name() -> None:
    from app.ingestion.connectors.slack_connector import SlackConnector
    from app.knowledge.ingestors.slack_ingestor import SlackIngestor

    config = build_source_config(
        kind="slack", tenant_id="t", collection_id="c", source_url="slack:C1",
        connection_config={"bot_token": "x", "channels": ["C1"], "channel_names": {"C1": "#eng"}},
    )  # fmt: skip
    ingest = AsyncMock(return_value=[])
    with patch.object(SlackIngestor, "ingest_channel", ingest):
        _ = [d async for d in SlackConnector().get_delta(config, None)]
    assert ingest.await_args.kwargs["channel_name"] == "#eng"
