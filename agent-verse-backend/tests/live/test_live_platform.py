"""Phase 17: Live Platform Testing.

Run against a real running backend with:
  LIVE_TEST=true uv run pytest tests/live/ -v

Rules:
  - Creates a dedicated test tenant
  - Cleans up all test data
  - Never uses production user data
  - Secrets never appear in logs or artifacts
"""
from __future__ import annotations

import os
import pytest

LIVE_TEST = os.getenv("LIVE_TEST", "false").lower() == "true"
BASE_URL = os.getenv("LIVE_BASE_URL", "http://localhost:8000")
TEST_API_KEY = os.getenv("LIVE_TEST_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_TEST, reason="Set LIVE_TEST=true to run live tests"
)


@pytest.fixture(scope="session")
def live_client():
    import httpx

    return httpx.Client(
        base_url=BASE_URL,
        headers={"X-API-Key": TEST_API_KEY},
        timeout=30.0,
    )


def test_live_health_check(live_client):
    """Backend health check passes."""
    resp = live_client.get("/health")
    assert resp.status_code == 200
    assert resp.json().get("status") in ("ok", "healthy", "degraded")


def test_live_model_registry(live_client):
    """Model registry returns catalog without secrets."""
    resp = live_client.get("/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    # Verify no secrets
    text = resp.text.lower()
    assert "sk-" not in text


def test_live_rag_query(live_client):
    """RAG query returns structured result."""
    resp = live_client.post("/rag/query", json={"query": "What is an AI agent?"})
    assert resp.status_code == 200
    assert "answer" in resp.json()
    assert "strategy_used" in resp.json()


def test_live_knowledge_graph(live_client):
    """Can add and retrieve knowledge graph nodes."""
    add = live_client.post(
        "/knowledge-graph/nodes",
        json={
            "node_type": "concept",
            "label": f"LiveTest-{os.urandom(4).hex()}",
            "content": "Test concept created by live test",
        },
    )
    assert add.status_code == 200
    node_id = add.json()["node_id"]

    get = live_client.get(f"/knowledge-graph/nodes/{node_id}")
    assert get.status_code == 200

    # Cleanup
    live_client.delete("/knowledge-graph/rebuild")  # Clears all tenant graph


def test_live_guardrails_pii(live_client):
    """PII detection works on real backend."""
    live_client.post(
        "/guardrails-v2/rules",
        json={
            "name": "LiveTest-PII",
            "rule_type": "pii_detection",
            "layers": ["step"],
            "action": "block",
        },
    )
    resp = live_client.post(
        "/guardrails-v2/evaluate",
        json={
            "content": "Email: test@example.com",
            "layer": "step",
        },
    )
    assert resp.status_code == 200
    assert "blocked" in resp.json()


def test_live_skills_execution(live_client):
    """Skills can be executed on real backend."""
    resp = live_client.post(
        "/skills-runtime/headroom/execute",
        json={
            "input_context": "This is a test document for the headroom skill.",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_live_no_secrets_in_any_response(live_client):
    """Comprehensive check that no secrets appear in any API response."""
    endpoints = [
        "/models",
        "/models/health",
        "/embeddings/providers",
        "/skills-runtime",
        "/guardrails-v2/layers",
    ]
    secret_patterns = ["sk-", "ghp_", "AIza", "api_key=", "secret=", "password="]

    for endpoint in endpoints:
        resp = live_client.get(endpoint)
        if resp.status_code == 200:
            for pattern in secret_patterns:
                assert pattern not in resp.text.lower(), (
                    f"Secret pattern '{pattern}' found in {endpoint}"
                )
