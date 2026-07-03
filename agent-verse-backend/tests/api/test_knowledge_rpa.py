"""Backend tests for RPA URL ingestion, knowledge retrieval in agent loop,
   and knowledge-civilization integration."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.api.knowledge import RpaUrlIngestRequest
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app():
    """Build a minimal test app with the knowledge router."""
    from fastapi import FastAPI
    from app.api.knowledge import router
    from app.tenancy.middleware import TenantMiddleware

    app = FastAPI()

    # Minimal tenant middleware stub
    class _FakeTenant:
        tenant_id = "test-tenant"
        plan = "enterprise"
        api_key_id = "key-1"
        roles: tuple = ()

    @app.middleware("http")
    async def inject_tenant(request, call_next):
        request.state.tenant = _FakeTenant()
        return await call_next(request)

    app.include_router(router)
    return app


def _make_client():
    app = _make_app()
    _store = KnowledgeStore()
    col = KnowledgeCollection(name="test-collection", collection_id="col-1")
    _store._data[("test-tenant", "col-1")] = MagicMock(
        collection=col, chunks=[], spec_set=["collection", "chunks"]
    )
    _store.list_collections = lambda tenant_ctx: [col]
    app.state.knowledge_store = _store
    app.state.embedder = None
    app.state.llm_provider = None
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests: POST /knowledge/ingest/rpa-url
# ---------------------------------------------------------------------------

class TestRpaUrlIngest:

    def test_empty_urls_returns_400(self):
        client = _make_client()
        resp = client.post(
            "/knowledge/ingest/rpa-url",
            json={"collection_id": "col-1", "urls": []},
        )
        assert resp.status_code == 400
        assert "must not be empty" in resp.json()["detail"]

    def test_too_many_urls_returns_400(self):
        client = _make_client()
        urls = [f"https://example.com/page-{i}" for i in range(21)]
        resp = client.post(
            "/knowledge/ingest/rpa-url",
            json={"collection_id": "col-1", "urls": urls},
        )
        assert resp.status_code == 400
        assert "Maximum 20" in resp.json()["detail"]

    def test_invalid_url_scheme_returns_400(self):
        client = _make_client()
        resp = client.post(
            "/knowledge/ingest/rpa-url",
            json={"collection_id": "col-1", "urls": ["ftp://example.com"]},
        )
        assert resp.status_code == 400
        assert "http" in resp.json()["detail"]

    @patch("app.api.knowledge._PLAYWRIGHT_AVAILABLE", False)
    def test_httpx_fallback_when_playwright_unavailable(self):
        """When Playwright is not installed, falls back to httpx + regex strip."""
        import httpx
        client = _make_client()

        mock_response = MagicMock()
        mock_response.text = "<html><body><h1>Test page</h1><p>Some content here</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            resp = client.post(
                "/knowledge/ingest/rpa-url",
                json={"collection_id": "col-1", "urls": ["https://example.com"]},
            )

        # Should succeed (201) even without Playwright
        assert resp.status_code == 201
        body = resp.json()
        assert body["playwright_available"] is False
        assert body["urls_processed"] == 1
        assert body["total_chunks_ingested"] >= 0

    def test_successful_ingest_response_shape(self):
        """Response must contain expected fields for frontend consumption."""
        client = _make_client()

        mock_response = MagicMock()
        mock_response.text = "Hello World. This is a test page with content to ingest."
        mock_response.raise_for_status = MagicMock()

        with patch("app.api.knowledge._PLAYWRIGHT_AVAILABLE", False), \
             patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            resp = client.post(
                "/knowledge/ingest/rpa-url",
                json={
                    "collection_id": "col-1",
                    "urls": ["https://example.com", "https://example.org"],
                    "screenshot": False,
                    "include_links": False,
                },
            )

        assert resp.status_code == 201
        body = resp.json()

        # Check all required response fields
        assert "collection_id" in body
        assert "urls_processed" in body
        assert "urls_succeeded" in body
        assert "total_chunks_ingested" in body
        assert "playwright_available" in body
        assert "results" in body

        # Check per-URL results
        assert len(body["results"]) == 2
        for result in body["results"]:
            assert "url" in result
            assert "success" in result
            assert "chunks_ingested" in result

    def test_batch_ingest_multiple_urls(self):
        """Batch ingestion processes all URLs and returns per-URL results."""
        client = _make_client()

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.text = f"Content for URL {call_count}. This has enough text to create chunks."
            m.raise_for_status = MagicMock()
            return m

        with patch("app.api.knowledge._PLAYWRIGHT_AVAILABLE", False), \
             patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = mock_get
            mock_httpx.return_value = mock_ctx

            resp = client.post(
                "/knowledge/ingest/rpa-url",
                json={
                    "collection_id": "col-1",
                    "urls": [
                        "https://example.com/page-1",
                        "https://example.com/page-2",
                        "https://example.com/page-3",
                    ],
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["urls_processed"] == 3
        assert len(body["results"]) == 3

    def test_failed_url_captured_in_results(self):
        """A URL that fails to fetch appears in results with success=False."""
        client = _make_client()

        async def mock_get(*args, **kwargs):
            import httpx
            raise httpx.ConnectError("Connection refused")

        with patch("app.api.knowledge._PLAYWRIGHT_AVAILABLE", False), \
             patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = mock_get
            mock_httpx.return_value = mock_ctx

            resp = client.post(
                "/knowledge/ingest/rpa-url",
                json={"collection_id": "col-1", "urls": ["https://nonexistent.example.com"]},
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["urls_processed"] == 1
        assert body["urls_succeeded"] == 0
        result = body["results"][0]
        assert result["success"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests: Agent loop knowledge retrieval
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: Agent loop knowledge retrieval (integration via graph node logic)
# ---------------------------------------------------------------------------

class TestAgentLoopKnowledgeRetrieval:
    """Test that the agent graph _node_rag_retrieval correctly uses
    the knowledge store even when no collections are bound to the agent."""

    @pytest.mark.asyncio
    async def test_knowledge_retrieved_event_emitted_on_rag_hit(self):
        """When knowledge is found, a knowledge_retrieved event is emitted
        on agent_state.events with citation metadata."""
        from app.agent.graph import AgentGraph
        from app.agent.state import AgentState
        from app.rag.store import HybridSearchResult

        # Create a mock knowledge store that returns a result
        mock_result = HybridSearchResult(
            chunk_id="chunk-abc",
            content="Deployment requires two approvals from senior engineers.",
            score=0.88,
            vector_score=0.9,
            trigram_score=0.8,
            source_url="https://docs.example.com/deploy",
        )

        mock_store = MagicMock()
        mock_store.list_collections = MagicMock(return_value=[
            MagicMock(collection_id="col-1")
        ])
        mock_store.hybrid_search_db = AsyncMock(return_value=[mock_result])

        # Minimal AgentGraph — only knowledge_store matters for this test
        graph = AgentGraph(
            planner=AsyncMock(),
            executor=AsyncMock(),
            verifier=AsyncMock(),
            knowledge_store=mock_store,
        )

        tenant_ctx = MagicMock()
        tenant_ctx.tenant_id = "tenant-1"

        agent_state = AgentState(
            goal="Find information about deployment process",
            tenant_ctx=tenant_ctx,
        )
        state = {"agent_state": agent_state, "tenant_ctx": tenant_ctx}

        await graph._node_rag_retrieval(state)

        # The knowledge_retrieved event should be in agent_state.events
        knowledge_events = [
            e for e in agent_state.events
            if e.get("type") == "knowledge_retrieved"
        ]
        assert len(knowledge_events) >= 1

        event = knowledge_events[0]
        assert "chunks_found" in event
        assert "citations" in event
        assert event["chunks_found"] >= 1

        # Citation should contain useful metadata
        citation = event["citations"][0]
        assert "collection_id" in citation
        assert "score" in citation
        assert "excerpt" in citation

    @pytest.mark.asyncio
    async def test_rag_skips_gracefully_without_knowledge_store(self):
        """If knowledge_store is None, RAG retrieval returns empty context
        without raising an exception."""
        from app.agent.graph import AgentGraph
        from app.agent.state import AgentState

        graph = AgentGraph(
            planner=AsyncMock(),
            executor=AsyncMock(),
            verifier=AsyncMock(),
            knowledge_store=None,  # ← no knowledge store
        )

        tenant_ctx = MagicMock()
        tenant_ctx.tenant_id = "tenant-1"

        agent_state = AgentState(
            goal="Search for something in the knowledge base",
            tenant_ctx=tenant_ctx,
        )
        state = {"agent_state": agent_state, "tenant_ctx": tenant_ctx}

        result = await graph._node_rag_retrieval(state)

        # Should return without error
        assert "rag_context" in result
        # No knowledge_retrieved event since no store
        knowledge_events = [
            e for e in agent_state.events
            if e.get("type") == "knowledge_retrieved"
        ]
        assert len(knowledge_events) == 0

    @pytest.mark.asyncio
    async def test_rag_uses_all_collections_when_no_agent_binding(self):
        """When agent has no bound collections, RAG searches ALL tenant
        collections (up to 3)."""
        from app.agent.graph import AgentGraph
        from app.agent.state import AgentState
        from app.rag.store import HybridSearchResult

        class FakeCollection:
            def __init__(self, cid: str):
                self.collection_id = cid

        collections = [FakeCollection(f"col-{i}") for i in range(5)]

        call_log: list[str] = []

        async def mock_search(query, query_embedding, collection_id, tenant_ctx, top_k=3):
            call_log.append(collection_id)
            return [HybridSearchResult(
                chunk_id=f"chunk-{collection_id}",
                content=f"Content from {collection_id}",
                score=0.75,
                vector_score=0.75,
                trigram_score=0.75,
            )]

        mock_store = MagicMock()
        mock_store.list_collections = MagicMock(return_value=collections)
        mock_store.hybrid_search_db = mock_search

        graph = AgentGraph(
            planner=AsyncMock(),
            executor=AsyncMock(),
            verifier=AsyncMock(),
            knowledge_store=mock_store,
        )
        # NO agent_collection_ids set → should search all tenant collections

        tenant_ctx = MagicMock()
        tenant_ctx.tenant_id = "tenant-1"

        agent_state = AgentState(
            goal="What do our docs say about authentication?",
            tenant_ctx=tenant_ctx,
        )
        state = {"agent_state": agent_state, "tenant_ctx": tenant_ctx}

        await graph._node_rag_retrieval(state)

        # Should have searched up to 3 of the 5 available collections
        assert len(call_log) <= 3
        assert len(call_log) >= 1


# ---------------------------------------------------------------------------
# Tests: RpaUrlIngestRequest model validation
# ---------------------------------------------------------------------------

class TestRpaUrlIngestRequestModel:

    def test_default_values(self):
        req = RpaUrlIngestRequest(
            collection_id="col-1",
            urls=["https://example.com"],
        )
        assert req.selector == "body"
        assert req.screenshot is False
        assert req.source_type == "rpa-web"
        assert req.max_chars == 50_000
        assert req.include_links is False

    def test_custom_values_accepted(self):
        req = RpaUrlIngestRequest(
            collection_id="col-2",
            urls=["https://example.com", "https://example.org"],
            selector="article.main",
            screenshot=True,
            source_type="rpa-docs",
            max_chars=25_000,
            include_links=True,
        )
        assert req.selector == "article.main"
        assert req.screenshot is True
        assert req.max_chars == 25_000
        assert req.include_links is True

    def test_multiple_urls_accepted(self):
        urls = [f"https://example.com/page-{i}" for i in range(20)]
        req = RpaUrlIngestRequest(collection_id="col-1", urls=urls)
        assert len(req.urls) == 20


# ---------------------------------------------------------------------------
# Tests: Civilization orchestrator blackboard pre-fetch
# ---------------------------------------------------------------------------

class TestCivilizationBlackboardKnowledge:

    @pytest.mark.asyncio
    async def test_blackboard_context_injected_into_execution_context(self):
        """Orchestrator queries blackboard and injects context into goal execution."""
        from app.civilization.orchestrator import CivilizationOrchestrator

        mock_blackboard = AsyncMock()
        mock_blackboard.query = AsyncMock(return_value=[
            {
                "topic": "deployment_process",
                "content": "Always run tests before deploying to production.",
                "confidence": 0.9,
            },
            {
                "topic": "database_schema",
                "content": "Orders table has columns: id, user_id, total_usd.",
                "confidence": 0.85,
            },
        ])

        mock_society = AsyncMock()
        mock_society.route_goal = AsyncMock(return_value={
            "mode": "single_agent",
            "agent_id": "agent-1",
            "confidence": 0.9,
        })

        mock_goal_service = AsyncMock()
        mock_goal_service.submit_goal = AsyncMock(return_value={
            "goal_id": "goal-123",
            "status": "accepted",
        })

        mock_constitution = MagicMock()
        mock_constitution.per_agent_budget_usd = 20.0
        mock_constitution.inherited_policy_ids = []

        captured_execution_context: dict = {}

        async def capture_submit(**kwargs):
            nonlocal captured_execution_context
            captured_execution_context = kwargs.get("execution_context", {})
            return {"goal_id": "goal-123"}

        mock_goal_service.submit_goal.side_effect = capture_submit

        orchestrator = CivilizationOrchestrator(
            civilization_id="civ-1",
            tenant_id="tenant-1",
            constitution=mock_constitution,
            governor=None,
            society=mock_society,
            bus=MagicMock(),
            blackboard=mock_blackboard,
            goal_service=mock_goal_service,
            tenant_ctx=MagicMock(tenant_id="tenant-1"),
        )

        await orchestrator.submit_goal("Deploy the payment service")

        # Verify blackboard was queried
        mock_blackboard.query.assert_called_once()

        # Verify execution_context contains blackboard data
        assert "blackboard_context" in captured_execution_context
        assert captured_execution_context["blackboard_entry_count"] == 2
        context_str = captured_execution_context["blackboard_context"]
        assert "deployment_process" in context_str
        assert "database_schema" in context_str

    @pytest.mark.asyncio
    async def test_blackboard_query_failure_does_not_prevent_goal_submission(self):
        """If blackboard query fails, goal submission still proceeds."""
        from app.civilization.orchestrator import CivilizationOrchestrator

        mock_blackboard = AsyncMock()
        mock_blackboard.query = AsyncMock(side_effect=Exception("DB connection failed"))

        mock_society = AsyncMock()
        mock_society.route_goal = AsyncMock(return_value={
            "mode": "single_agent",
            "agent_id": "agent-1",
            "confidence": 0.8,
        })

        mock_goal_service = AsyncMock()
        mock_goal_service.submit_goal = AsyncMock(return_value={"goal_id": "goal-456"})

        mock_constitution = MagicMock()
        mock_constitution.per_agent_budget_usd = 10.0
        mock_constitution.inherited_policy_ids = []

        orchestrator = CivilizationOrchestrator(
            civilization_id="civ-2",
            tenant_id="tenant-1",
            constitution=mock_constitution,
            governor=None,
            society=mock_society,
            bus=MagicMock(),
            blackboard=mock_blackboard,
            goal_service=mock_goal_service,
            tenant_ctx=MagicMock(tenant_id="tenant-1"),
        )

        # Should not raise even if blackboard fails
        result = await orchestrator.submit_goal("Do some task")
        assert result["status"] == "accepted"
        # Goal service was still called
        mock_goal_service.submit_goal.assert_called_once()
