"""Phase 6+7: GraphRAG/RAG Platform + Agent Runtime 2.0 tests."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.models import AgentRole, RiskLevel, StepStatus
from app.api.agent_runtime import router as runtime_router
from app.api.rag_platform import router as rag_router
from app.orchestration.strategy_registry import build_default_registry
from app.rag.contracts import (
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGRetrievalLeg,
    UnavailableRAGStrategyError,
)
from app.rag_platform.query_planner import QueryPlanner, RAGStrategy
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-p67", plan=PlanTier.PROFESSIONAL, api_key_id="kid-p67")
_KEY = "ak_phase67_test_key"
_HEADERS = {"X-API-Key": _KEY}


class _NaiveRuntimeAdapter:
    strategy = RAGStrategy.NAIVE

    async def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult:
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=self.strategy,
        )


class _AdaptiveRuntimeAdapter:
    strategy = RAGStrategy.ADAPTIVE

    async def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult:
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=self.strategy,
        )


class _Gateway:
    def __init__(self) -> None:
        capability = SimpleNamespace()
        self.dependencies = SimpleNamespace(
            strategy_capabilities={
                RAGStrategy.NAIVE: capability,
                RAGStrategy.ADAPTIVE: capability,
            }
        )

    async def execute(
        self,
        tenant_ctx: TenantContext,
        *,
        collection_id: str,
        query: str,
        strategy_id: str,
        top_k: int,
        filters: dict,
    ) -> RAGExecutionResult:
        del tenant_ctx, collection_id, top_k, filters
        strategy = RAGStrategy(strategy_id)
        if strategy not in {RAGStrategy.NAIVE, RAGStrategy.ADAPTIVE}:
            raise UnavailableRAGStrategyError(strategy)
        return RAGExecutionResult(
            requested_strategy_id=strategy_id,
            resolved_strategy_id=strategy,
            retrieval_legs=[
                RAGRetrievalLeg(
                    strategy=strategy,
                    query=query,
                    result_count=0,
                )
            ],
            answer="No matching certified evidence.",
        )


@pytest.fixture(autouse=True)
def _certified_rag_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_default_registry(
        rag_runtime_capabilities={
            RAGStrategy.NAIVE: _NaiveRuntimeAdapter,
            RAGStrategy.ADAPTIVE: _AdaptiveRuntimeAdapter,
        }
    )
    monkeypatch.setattr("app.api.rag_platform.get_strategy_registry", lambda: registry)


def _make_app():
    app = FastAPI()

    async def _resolve(key):
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(rag_router)
    app.include_router(runtime_router)
    app.state.retrieval_gateway = _Gateway()
    return app


# ── Phase 6: RAG Platform tests ──────────────────────────────────────────────

def test_rag_query_returns_answer():
    client = TestClient(_make_app())
    resp = client.post(
        "/rag/query",
        json={
            "query": "What is an AI agent?",
            "collection_id": "collection-1",
            "strategy": RAGStrategy.NAIVE.value,
            "top_k": 3,
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "strategy_used" in data
    assert "citations" in data
    assert "grounded" in data


def test_rag_query_adaptive_strategy():
    client = TestClient(_make_app())
    resp = client.post(
        "/rag/query",
        json={
            "query": "How does agent planning work?",
            "collection_id": "collection-1",
            "strategy": RAGStrategy.ADAPTIVE.value,
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["strategy_used"] in [s.value for s in RAGStrategy]


def test_rag_query_rejects_removed_direct_id():
    client = TestClient(_make_app())

    resp = client.post(
        "/rag/query",
        json={
            "query": "What is an AI agent?",
            "collection_id": "collection-1",
            "strategy": "direct",
        },
        headers=_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "Unknown RAG strategy: direct"


def test_rag_query_rejects_uncertified_canonical_strategy():
    client = TestClient(_make_app())

    resp = client.post(
        "/rag/query",
        json={
            "query": "What is connected?",
            "collection_id": "collection-1",
            "strategy": RAGStrategy.GRAPH.value,
        },
        headers=_HEADERS,
    )

    assert resp.status_code == 503
    assert resp.json()["detail"] == "RAG strategy is unavailable: graph"


def test_rag_list_strategies():
    client = TestClient(_make_app())
    resp = client.get("/rag/strategies", headers=_HEADERS)
    assert resp.status_code == 200
    strategies = resp.json()["strategies"]
    assert len(strategies) == len(RAGStrategy)
    ids = {s["id"] for s in strategies}
    assert ids == {strategy.value for strategy in RAGStrategy}
    assert {"auto", "direct", "multimodal"}.isdisjoint(ids)
    by_id = {strategy["id"]: strategy for strategy in strategies}
    assert by_id[RAGStrategy.NAIVE.value]["state"] == "implemented"
    assert by_id[RAGStrategy.NAIVE.value]["available"] is True
    assert by_id[RAGStrategy.ADAPTIVE.value]["state"] == "implemented"
    assert by_id[RAGStrategy.GRAPH.value]["available"] is False


def test_rag_retrieval_legs_present():
    client = TestClient(_make_app())
    resp = client.post(
        "/rag/query",
        json={
            "query": "test query",
            "collection_id": "collection-1",
            "strategy": RAGStrategy.ADAPTIVE.value,
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    legs = resp.json()["retrieval_legs"]
    assert isinstance(legs, list)
    assert len(legs) >= 1


def test_rag_confidence_score():
    client = TestClient(_make_app())
    resp = client.post(
        "/rag/query",
        json={
            "query": "What is semantic search?",
            "collection_id": "collection-1",
            "strategy": RAGStrategy.ADAPTIVE.value,
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert 0.0 <= resp.json()["confidence"] <= 1.0


def test_query_planner_auto_selects_graph():
    planner = QueryPlanner()
    strategy = planner.select_strategy("What is related to machine learning?")
    assert strategy == RAGStrategy.GRAPH


def test_query_planner_auto_selects_multi_hop():
    planner = QueryPlanner()
    strategy = planner.select_strategy("How does the system cause failures?")
    assert strategy == RAGStrategy.MULTI_HOP


# ── Phase 7: Agent Runtime 2.0 tests ─────────────────────────────────────────

def test_create_execution_plan():
    client = TestClient(_make_app())
    resp = client.post(
        "/agent-runtime/plans",
        json={
            "goal_id": "goal-test-1",
            "goal_text": "Deploy the application to production",
            "strategy": "single_agent",
            "steps": [
                {"description": "Build Docker image", "role": "executor", "risk_level": "low"},
                {
                    "description": "Run integration tests",
                    "role": "verifier",
                    "risk_level": "medium",
                    "dependencies": ["step_1"],
                },
                {"description": "Deploy to production", "role": "executor", "risk_level": "high"},
            ],
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "plan_id" in data
    assert data["step_count"] == 3
    assert data["strategy"] == "single_agent"


def test_get_execution_plan():
    client = TestClient(_make_app())
    create = client.post(
        "/agent-runtime/plans",
        json={
            "goal_id": "g1",
            "goal_text": "Test goal",
            "steps": [{"description": "Step 1", "role": "executor"}],
        },
        headers=_HEADERS,
    )
    plan_id = create.json()["plan_id"]

    resp = client.get(f"/agent-runtime/plans/{plan_id}", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["plan_id"] == plan_id


def test_plan_step_roles_are_valid():
    client = TestClient(_make_app())
    resp = client.post(
        "/agent-runtime/plans",
        json={
            "goal_id": "g-roles",
            "goal_text": "Multi-role goal",
            "steps": [
                {"description": "Plan it", "role": "planner"},
                {"description": "Execute it", "role": "executor"},
                {"description": "Verify it", "role": "verifier"},
                {"description": "Judge it", "role": "judge"},
            ],
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    roles = {s["role"] for s in resp.json()["steps"]}
    assert "planner" in roles
    assert "executor" in roles


def test_plan_step_risk_levels():
    client = TestClient(_make_app())
    resp = client.post(
        "/agent-runtime/plans",
        json={
            "goal_id": "g-risk",
            "goal_text": "Risky operations",
            "steps": [
                {"description": "Read file", "risk_level": "low"},
                {"description": "Deploy", "risk_level": "critical"},
            ],
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200


def test_create_and_get_run_trace():
    client = TestClient(_make_app())
    create = client.post(
        "/agent-runtime/traces", json={"goal_id": "g-trace-1"}, headers=_HEADERS
    )
    assert create.status_code == 200
    trace_id = create.json()["trace_id"]

    get = client.get(f"/agent-runtime/traces/{trace_id}", headers=_HEADERS)
    assert get.status_code == 200
    assert get.json()["trace_id"] == trace_id
    assert get.json()["goal_id"] == "g-trace-1"


def test_trace_not_found():
    client = TestClient(_make_app())
    resp = client.get("/agent-runtime/traces/nonexistent", headers=_HEADERS)
    assert resp.status_code == 404


def test_list_agent_roles():
    client = TestClient(_make_app())
    resp = client.get("/agent-runtime/roles", headers=_HEADERS)
    assert resp.status_code == 200
    roles = resp.json()["roles"]
    role_ids = {r["id"] for r in roles}
    assert "planner" in role_ids
    assert "executor" in role_ids
    assert "judge" in role_ids
    assert "synthesizer" in role_ids


def test_list_strategies():
    client = TestClient(_make_app())
    resp = client.get("/agent-runtime/strategies", headers=_HEADERS)
    assert resp.status_code == 200
    strats = resp.json()["strategies"]
    assert len(strats) >= 4
    ids = {s["id"] for s in strats}
    assert "single_agent" in ids
    assert "debate" in ids
    assert "supervisor" in ids


def test_plan_tenant_isolation():
    client = TestClient(_make_app())
    create = client.post(
        "/agent-runtime/plans",
        json={
            "goal_id": "isolated-goal",
            "steps": [{"description": "Private step"}],
        },
        headers=_HEADERS,
    )
    plan_id = create.json()["plan_id"]

    # Different tenant tries to access
    other_headers = {"X-API-Key": "other-key-no-access"}
    resp = client.get(f"/agent-runtime/plans/{plan_id}", headers=other_headers)
    assert resp.status_code in (401, 404)


def test_agent_role_enum_values():
    assert AgentRole.PLANNER.value == "planner"
    assert AgentRole.EXECUTOR.value == "executor"
    assert AgentRole.JUDGE.value == "judge"
    assert RiskLevel.CRITICAL.value == "critical"
    assert StepStatus.WAITING_HUMAN.value == "waiting_human"
