"""KB-22: federated search fan-out and speculative draft evidence are bounded.

``POST /knowledge/search/federated`` passed an uncapped ``collection_ids`` list
to ``asyncio.gather`` (chat caps at 10); the speculative strategy concatenated
its whole evidence subset into one prompt with no length bound.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from app.knowledge import federated_search as fs
from app.rag.agentic.patterns import speculative
from tests.rag.test_gateway_entrypoints import HEADERS, TENANT, RecordingGateway, _app


def test_fifty_collection_ids_are_refused() -> None:
    gateway = RecordingGateway()
    client = TestClient(_app(gateway), raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/search/federated",
        json={"query": "q", "collection_ids": [f"c{i}" for i in range(50)]},
        headers=HEADERS,
    )
    assert resp.status_code == 422, resp.text
    assert gateway.calls == []


def test_the_allowed_maximum_is_searched() -> None:
    gateway = RecordingGateway()
    client = TestClient(_app(gateway), raise_server_exceptions=False)
    ids = [f"c{i}" for i in range(fs.MAX_FEDERATED_COLLECTIONS)]
    resp = client.post(
        "/knowledge/search/federated", json={"query": "q", "collection_ids": ids}, headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert len(gateway.calls) == len(ids)


async def test_fan_out_concurrency_is_bounded() -> None:
    in_flight = 0
    peak = 0

    class _SlowGateway(RecordingGateway):
        async def execute(self, tenant_context: Any, **kwargs: Any) -> Any:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return await super().execute(tenant_context, **kwargs)

    await fs.federated_search(
        "q",
        [f"c{i}" for i in range(fs.MAX_FEDERATED_COLLECTIONS)],
        _SlowGateway(),
        tenant_ctx=TENANT,
    )
    assert peak <= fs.FEDERATED_FAN_OUT


def test_speculative_draft_evidence_is_truncated_to_the_budget() -> None:
    class _Item:
        def __init__(self, content: str) -> None:
            self.content = content

    subset = [_Item("x" * 5000) for _ in range(20)]
    text = speculative._draft_evidence_text(subset)
    assert len(text) <= speculative.MAX_DRAFT_EVIDENCE_CHARS
    assert text.startswith("x")
