"""OI-4 against a real Redis: renaming the host keeps every document id, and the
one-time migration moves documents indexed under the legacy host-based ids.

The container is reachable on localhost only, so ``localhost`` / ``127.0.0.1``
go on the operator allowlist (the documented on-prem mechanism).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pytest
import redis
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.ingestion.connectors.redis_connector import RedisConnector
from app.ingestion.mongodb_id_migration import (
    STATUS_COMPLETED_WITH_ISSUES,
    MigrationStateStore,
    migrate_legacy_mongodb_ids,
    migration_name,
)
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

_TENANT = "tid-oi4-int"
_SOURCE = "src-redis-int"
_CTX = TenantContext(tenant_id=_TENANT, plan=PlanTier.FREE, api_key_id="k")
_DIM = 768
_FILL = " — enough words for the pipeline's minimum text length, repeated a little." * 3


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost,127.0.0.1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def server() -> Iterator[int]:
    container = DockerContainer("redis:7-alpine").with_exposed_ports(6379)
    strategy = LogMessageWaitStrategy(re.compile(re.escape("Ready to accept")))
    with container.waiting_for(strategy.with_startup_timeout(90)):
        port = int(container.get_exposed_port(6379))
        r = redis.Redis(host="127.0.0.1", port=port, db=1)
        for i in range(5):
            r.set(f"item:{i}", f"value {i}{_FILL}")
        r.set("item:upd", f"new value after the edit{_FILL}")
        r.set("item:same", f"unchanged value{_FILL}")
        r.rpush("item:list", "a", "b")  # a type the Source does not index
        r.close()
        yield port


def _config(host: str, port: int, **extra: Any) -> SourceConfig:
    return SourceConfig(
        source_id=_SOURCE,
        tenant_id=_TENANT,
        name="r",
        family=SourceFamily.NOSQL_DATABASE,
        source_type="redis",
        connection_config={
            "host": host,
            "port": port,
            "db": 1,
            "auth_type": "none",
            "key_patterns": "item:*",
            "types": "string",
            **extra,
        },
        min_quality_score=0.0,
    )


async def _ids(config: SourceConfig) -> set[str]:
    connector = RedisConnector()
    synced = {raw.doc_id async for raw, _cursor in connector.get_delta(config, None)}
    live = {i async for i in connector.iter_live_doc_ids(config)}
    assert synced <= live
    return synced


async def test_renaming_the_host_keeps_every_document_id(server: int) -> None:
    before = await _ids(_config("localhost", server))
    after = await _ids(_config("127.0.0.1", server))
    assert len(before) == 7
    assert before == after  # 0 new documents after the rename


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * _DIM for _ in request.texts], model="fake")


def _legacy(key: str, *, display: str) -> tuple[str, str]:
    url = f"redis://{display}/1/{quote(key, safe='')}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_SOURCE}:{url}")), url


async def test_legacy_documents_move_to_their_current_ids(server: int) -> None:
    store = KnowledgeStore()
    cid = store.create_collection(KnowledgeCollection(name="kb"), tenant_ctx=_CTX)
    # Indexed by an earlier release while the Source pointed at "localhost".
    config = _config("127.0.0.1", server)
    config.collection_id = cid
    seeded: dict[str, str] = {}
    for key, text in (
        ("item:upd", "old value before the edit"),
        ("item:same", f"# item:same\n\ntype: string\n\nunchanged value{_FILL}"),
        ("item:gone", "a key deleted upstream since"),
        ("item:list", "a list key, no longer indexed"),
    ):
        doc_id, url = _legacy(key, display=f"localhost:{server}")
        seeded[key] = doc_id
        store.ingest_chunk(
            Chunk(
                document_id=doc_id,
                content=text,
                embedding=[0.2] * _DIM,
                chunk_index=0,
                metadata={
                    "source_id": _SOURCE,
                    "source_url": url,
                    "doc_content_hash": hashlib.sha256(text.encode()).hexdigest(),
                },
            ),
            collection_id=cid,
            tenant_ctx=_CTX,
        )
    connector = RedisConnector()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=_Embedder())

    result = await migrate_legacy_mongodb_ids(
        config=config,
        connector=connector,
        pipeline=pipeline,
        knowledge_store=store,
        state_store=MigrationStateStore(migration=migration_name(connector)),
    )

    docs: dict[str, str] = {}
    for chunk in store._data[(_TENANT, cid)].chunks:
        docs[chunk.document_id] = docs.get(chunk.document_id, "") + chunk.content
    from app.ingestion.connectors.redis_connector import _doc_id

    assert set(docs) == {
        _doc_id(config, 1, "item:upd"),
        _doc_id(config, 1, "item:same"),
        seeded["item:list"],
    }
    assert "new value after the edit" in docs[_doc_id(config, 1, "item:upd")]
    assert "unchanged value" in docs[_doc_id(config, 1, "item:same")]
    assert (result["migrated"], result["deleted"], result["skipped"], result["failed"]) == (
        2,
        1,
        1,
        0,
    )
    assert result["status"] == STATUS_COMPLETED_WITH_ISSUES
