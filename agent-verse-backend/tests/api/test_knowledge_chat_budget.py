"""KB-02: /knowledge/chat synthesis and citation verification are metered and bounded.

The route called ``RAGRetriever.synthesize()`` and ``verify_result()`` with no
budget context, so answer synthesis and the one-call-per-claim entailment checks
used the raw tenant provider: never charged to the tenant, no timeout, no
circuit breaker. Both now run through the RAG cost guard (``complete_decision``
with the generation timeout), a spent budget answers 429, and the number of
entailment calls per answer is capped.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.governance.cost import CostController
from app.providers.base import CompletionResponse
from tests.rag.test_gateway_entrypoints import (
    HEADERS,
    TENANT,
    RecordingGateway,
    RecordingProvider,
    _app,
)

_BODY = {"question": "retention policy", "collection_ids": ["collection-1"]}


def _gateway(provider: Any, controller: CostController | None) -> RecordingGateway:
    gateway = RecordingGateway(provider=provider)
    gateway.dependencies.cost_controller = controller
    return gateway


def test_chat_synthesis_and_verification_are_charged_to_the_tenant() -> None:
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0)
    provider = RecordingProvider()
    client = TestClient(_app(_gateway(provider, controller)), raise_server_exceptions=False)

    response = client.post("/knowledge/chat", json=_BODY, headers=HEADERS)

    assert response.status_code == 200, response.text
    assert provider.requests, "synthesis never reached the tenant model"
    # Synthesis (and any entailment call) was reserved against the tenant budget.
    assert controller.daily_total(tenant_ctx=TENANT) > 0


def test_chat_with_an_exhausted_budget_is_429_and_spends_nothing() -> None:
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=0.0)
    provider = RecordingProvider()
    client = TestClient(_app(_gateway(provider, controller)), raise_server_exceptions=False)

    response = client.post("/knowledge/chat", json=_BODY, headers=HEADERS)

    assert response.status_code == 429, response.text
    assert provider.requests == []


class _HangingProvider(RecordingProvider):
    async def complete(self, request: Any) -> CompletionResponse:
        self.requests.append(request)
        await asyncio.sleep(30)
        raise AssertionError("unreachable")


def test_chat_synthesis_is_bounded_by_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS", "0.2")
    provider = _HangingProvider()
    client = TestClient(_app(_gateway(provider, None)), raise_server_exceptions=False)

    started = time.monotonic()
    response = client.post("/knowledge/chat", json=_BODY, headers=HEADERS)

    assert response.status_code == 503, response.text
    assert provider.requests, "synthesis was never attempted"
    # Cut off by the generation timeout, not by the hung call finishing (30 s).
    assert time.monotonic() - started < 10


async def test_citation_verification_caps_entailment_calls_per_answer() -> None:
    from app.rag.contracts import RAGCitation
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    verifier = MinimalCitationVerifier(provider=provider, model="tenant-model")
    citations = [
        RAGCitation(
            citation_id="c1", chunk_id="k1", content="Evidence one.", score=0.9, source="a"
        )
    ]
    answer = " ".join(f"Claim number {i} holds [1]." for i in range(200))

    result = await verifier.verify(answer, citations)

    assert len(provider.requests) <= MinimalCitationVerifier.MAX_ENTAILMENT_CALLS
    # Claims left unchecked are never reported as grounded.
    assert result.grounded is False
    assert result.reason == "verification_limit_exceeded"
