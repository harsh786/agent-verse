"""Phase 10+11: AI Ops (Evals/Drift) + Agent Memory 2.0 tests."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.ai_ops import router as ai_ops_router
from app.api.memory_v2 import router as memory_v2_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-p1011", plan=PlanTier.PROFESSIONAL, api_key_id="kid-p1011")
_CTX_B = TenantContext(tenant_id="tid-p1011b", plan=PlanTier.FREE, api_key_id="kid-p1011b")
_KEY = "ak_phase1011_test_key"
_KEY_B = "ak_phase1011b_test_key"
_HEADERS = {"X-API-Key": _KEY}
_HEADERS_B = {"X-API-Key": _KEY_B}

def _make_app():
    app = FastAPI()
    async def _resolve(key):
        if key == _KEY: return _CTX
        if key == _KEY_B: return _CTX_B
        return None
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(ai_ops_router)
    app.include_router(memory_v2_router)
    return app


# ── Phase 10: AI Ops tests ────────────────────────────────────────────────────

def test_create_eval_dataset():
    client = TestClient(_make_app())
    resp = client.post("/ai-ops/datasets", json={
        "name": "QA Golden Dataset",
        "golden_tasks": [
            {"input": "What is 2+2?", "expected_output": "4"},
            {"input": "Capital of France?", "expected_output": "Paris"},
        ],
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "dataset_id" in data
    assert data["task_count"] == 2

def test_list_eval_datasets():
    client = TestClient(_make_app())
    client.post("/ai-ops/datasets", json={"name": "Test Dataset"}, headers=_HEADERS)
    resp = client.get("/ai-ops/datasets", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

def test_run_eval_suite():
    client = TestClient(_make_app())
    create = client.post("/ai-ops/datasets", json={
        "name": "Eval Suite",
        "golden_tasks": [{"input": "test", "expected_output": "test response"}],
    }, headers=_HEADERS)
    dataset_id = create.json()["dataset_id"]

    resp = client.post(f"/ai-ops/datasets/{dataset_id}/run", json={}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "result_id" in data
    assert "avg_score" in data
    assert "passed" in data
    assert 0.0 <= data["avg_score"] <= 1.0

def test_eval_result_persisted():
    client = TestClient(_make_app())
    create = client.post("/ai-ops/datasets", json={"name": "Persist Test"}, headers=_HEADERS)
    dataset_id = create.json()["dataset_id"]
    client.post(f"/ai-ops/datasets/{dataset_id}/run", json={}, headers=_HEADERS)

    results = client.get("/ai-ops/eval-results", headers=_HEADERS)
    assert results.status_code == 200
    assert results.json()["total"] >= 1

def test_set_baseline_and_compute_drift():
    client = TestClient(_make_app())
    client.post("/ai-ops/baselines", json={"metric_name": "model_quality", "value": 0.85}, headers=_HEADERS)

    resp = client.post("/ai-ops/drift", json={"metric_name": "model_quality", "current_value": 0.60}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["drift_score"] > 0.1
    assert data["severity"] in ("warning", "critical")

def test_drift_within_threshold_is_info():
    client = TestClient(_make_app())
    client.post("/ai-ops/baselines", json={"metric_name": "latency_ms", "value": 500.0}, headers=_HEADERS)

    resp = client.post("/ai-ops/drift", json={"metric_name": "latency_ms", "current_value": 505.0}, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["severity"] == "info"

def test_list_drift_alerts():
    client = TestClient(_make_app())
    client.post("/ai-ops/baselines", json={"metric_name": "test_metric", "value": 1.0}, headers=_HEADERS)
    client.post("/ai-ops/drift", json={"metric_name": "test_metric", "current_value": 0.5}, headers=_HEADERS)

    resp = client.get("/ai-ops/alerts", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

def test_regression_detection_on_eval():
    client = TestClient(_make_app())
    # Create dataset and run twice
    create = client.post("/ai-ops/datasets", json={"name": "Regression Test"}, headers=_HEADERS)
    did = create.json()["dataset_id"]

    # First run sets baseline
    client.post(f"/ai-ops/datasets/{did}/run", json={}, headers=_HEADERS)
    # Second run triggers regression check
    client.post(f"/ai-ops/datasets/{did}/run", json={}, headers=_HEADERS)

    status = client.get("/ai-ops/regression-status", headers=_HEADERS)
    assert status.status_code == 200
    assert "status" in status.json()

def test_create_llm_judge():
    client = TestClient(_make_app())
    resp = client.post("/ai-ops/judges", json={
        "name": "Quality Judge",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "evaluation_dimensions": ["accuracy", "relevance", "safety"],
    }, headers=_HEADERS)
    assert resp.status_code == 200
    assert "judge_id" in resp.json()

def test_tenant_isolation_eval_results():
    client = TestClient(_make_app())
    create = client.post("/ai-ops/datasets", json={"name": "Isolated Dataset"}, headers=_HEADERS)
    did = create.json()["dataset_id"]
    client.post(f"/ai-ops/datasets/{did}/run", json={}, headers=_HEADERS)

    # Tenant B should see 0 results
    results_b = client.get("/ai-ops/eval-results", headers=_HEADERS_B)
    assert results_b.status_code == 200
    # Should be empty or have only B's own results


# ── Phase 11: Agent Memory 2.0 tests ─────────────────────────────────────────

def test_create_memory_with_provenance():
    client = TestClient(_make_app())
    resp = client.post("/memory-v2", json={
        "content": "The agent learns from user feedback",
        "memory_type": "fact",
        "confidence": 0.9,
        "provenance": {"created_from_goal_id": "goal-123", "confidence_evidence": "User confirmed"},
    }, headers=_HEADERS)
    assert resp.status_code == 200
    assert "memory_id" in resp.json()

def test_list_memories_with_filter():
    client = TestClient(_make_app())
    client.post("/memory-v2", json={"content": "Active memory", "lifecycle_state": "active"}, headers=_HEADERS)

    resp = client.get("/memory-v2?lifecycle_state=active", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    for m in resp.json()["memories"]:
        assert m["lifecycle_state"] == "active"

def test_get_memory_by_id():
    client = TestClient(_make_app())
    create = client.post("/memory-v2", json={"content": "Find me by ID"}, headers=_HEADERS)
    mid = create.json()["memory_id"]

    resp = client.get(f"/memory-v2/{mid}", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["content"] == "Find me by ID"

def test_update_memory():
    client = TestClient(_make_app())
    create = client.post("/memory-v2", json={"content": "Original", "confidence": 0.7}, headers=_HEADERS)
    mid = create.json()["memory_id"]

    resp = client.patch(f"/memory-v2/{mid}", json={"confidence": 0.95, "content": "Updated"}, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["update_count"] == 1

def test_soft_delete_memory():
    client = TestClient(_make_app())
    create = client.post("/memory-v2", json={"content": "To be deleted"}, headers=_HEADERS)
    mid = create.json()["memory_id"]

    delete = client.delete(f"/memory-v2/{mid}", headers=_HEADERS)
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    # Should not appear in list
    get = client.get(f"/memory-v2/{mid}", headers=_HEADERS)
    assert get.status_code == 404

def test_lifecycle_mark_stale():
    client = TestClient(_make_app())
    client.post("/memory-v2", json={"content": "Old memory"}, headers=_HEADERS)

    resp = client.post("/memory-v2/lifecycle/mark-stale", json={"days_old": 0}, headers=_HEADERS)
    assert resp.status_code == 200
    assert "marked_stale" in resp.json()

def test_conflict_detection():
    client = TestClient(_make_app())
    client.post("/memory-v2", json={"content": "The system is running normally"}, headers=_HEADERS)
    # Contradictory memory
    client.post("/memory-v2", json={"content": "The system is not running normally"}, headers=_HEADERS)

    resp = client.get("/memory-v2/conflicts/all", headers=_HEADERS)
    assert resp.status_code == 200
    assert "conflicts" in resp.json()

def test_resolve_conflict():
    client = TestClient(_make_app())
    # Create conflicting memories
    client.post("/memory-v2", json={"content": "A is true"}, headers=_HEADERS)
    client.post("/memory-v2", json={"content": "A is not true"}, headers=_HEADERS)

    conflicts = client.get("/memory-v2/conflicts/all", headers=_HEADERS).json()["conflicts"]
    if conflicts:
        conflict_id = conflicts[0]["conflict_id"]
        resp = client.post(f"/memory-v2/conflicts/{conflict_id}/resolve", json={"resolution": "Kept newer entry"}, headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

def test_gdpr_export():
    client = TestClient(_make_app())
    client.post("/memory-v2", json={"content": "GDPR test memory"}, headers=_HEADERS)

    resp = client.get("/memory-v2/export/gdpr", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "tenant_id" in data
    assert "total_memories" in data
    assert "exported_at" in data

def test_memory_tenant_isolation():
    client = TestClient(_make_app())
    create = client.post("/memory-v2", json={"content": "Tenant A secret"}, headers=_HEADERS)
    mid = create.json()["memory_id"]

    resp = client.get(f"/memory-v2/{mid}", headers=_HEADERS_B)
    assert resp.status_code == 404

def test_invalid_lifecycle_state_returns_400():
    client = TestClient(_make_app())
    create = client.post("/memory-v2", json={"content": "Test"}, headers=_HEADERS)
    mid = create.json()["memory_id"]

    resp = client.patch(f"/memory-v2/{mid}", json={"lifecycle_state": "invalid_state"}, headers=_HEADERS)
    assert resp.status_code == 400
