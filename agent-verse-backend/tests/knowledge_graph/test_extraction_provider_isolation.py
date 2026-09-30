"""KB-30: KG extraction spends no LLM when use_llm=false, and concurrent requests
never share (race on) one provider.

``/knowledge-graph/extract`` always called ``extract_relationships_llm`` even
with ``use_llm=false``; the per-request ``set_provider`` mutated the module
global extractor (and the ingestion hook's shared one), so concurrent requests
raced on which provider was used.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge_graph import router as kg_router
from app.knowledge_graph.extractor import EntityExtractor
from app.knowledge_graph.ingestion_hook import KGIngestionHook
from app.knowledge_graph.store import KnowledgeGraphStore
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext("t-kb30", PlanTier.ENTERPRISE, "k", roles=("admin",))
_TEXT = "Alice Johnson met Bob Smith at ACME headquarters in Berlin to plan the OpenAI rollout."


class _Recorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        await asyncio.sleep(0.01)
        return CompletionResponse(content="[]", model=self.name)


def test_use_llm_false_makes_no_llm_call() -> None:
    provider = _Recorder("app")
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(kg_router)
    app.state._app_provider = provider
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/knowledge-graph/extract",
        json={"text": _TEXT, "use_llm": False},
        headers={"X-API-Key": "k"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["entities_extracted"] >= 2
    assert provider.calls == 0


async def test_concurrent_hook_calls_each_use_their_own_provider() -> None:
    hook = KGIngestionHook(store=KnowledgeGraphStore(), extractor=EntityExtractor())
    a, b = _Recorder("a"), _Recorder("b")

    async def run(provider: _Recorder, doc: str) -> None:
        for _ in range(3):
            await hook.process(
                chunks=[_TEXT], document_id=doc, tenant_id="t-kb30", provider=provider
            )

    await asyncio.gather(run(a, "doc-a"), run(b, "doc-b"))

    # Entities + relationships per chunk: each document's calls reached its own
    # provider only (the shared extractor used to be re-pointed mid-flight).
    assert a.calls == b.calls
    assert a.calls > 0
    assert hook._extractor._provider is None  # the shared extractor is never mutated
