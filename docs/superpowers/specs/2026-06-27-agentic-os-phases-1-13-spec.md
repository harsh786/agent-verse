# AgentVerse Agentic OS — Implementation Specification (Phases 1–13)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform AgentVerse from a well-architected prototype into a production Agentic OS — complete capability discovery, structured tool-using planner, durable DAG execution kernel, stateful RPA, persistent memory, production RAG, full governance, and world-class HITL.

**Architecture:** Each phase is an independent vertical slice. Complete Phase N tests before starting Phase N+1. Every phase ships with unit, integration, API, and E2E tests.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 async, PostgreSQL 16 + pgvector, Redis 7, LangGraph 0.2, Celery 5.4, React 19, Vitest 3, Playwright 1.49, pytest-asyncio.

**Priority encoding:** 🔴 P0 = broken/blocks everything · 🟡 P1 = stub must become real · 🟢 P2 = world-class addition

---

## Phase 1 — Universal Capability Registry

### 1.1 🔴 MCP Health Check Task (currently returns 0 servers)

**Current state:** `app/scaling/tasks.py:597–617` — `_check_servers()` returns `[]` immediately with comment "In production: query MCPRegistry". `check_mcp_health` Celery task fires every 30s but records nothing.

**Gap:** Operators have zero visibility into which MCP connectors are healthy. Unhealthy connectors are silently used until tool calls fail.

**Implementation:**

File: `app/scaling/tasks.py` — replace `_check_servers()`:
```python
async def _check_servers() -> list[dict[str, Any]]:
    """Ping every registered MCP server across all active tenants."""
    import json as _json
    results: list[dict[str, Any]] = []
    try:
        redis_url = celery_app.conf.broker_url or ""
        if not redis_url:
            return [{"status": "skipped", "reason": "no Redis broker"}]
        import redis as _redis
        client = _redis.from_url(redis_url, decode_responses=True)
        keys = client.keys("mcp:servers:*")
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as http:
            for key in keys[:50]:
                tenant_id = key.split(":")[2] if key.count(":") >= 2 else "unknown"
                raw = client.get(key)
                if not raw:
                    continue
                servers: dict = _json.loads(raw)
                for sid, sdata in servers.items():
                    url = sdata.get("url", "")
                    if not url:
                        continue
                    t0 = __import__("time").monotonic()
                    try:
                        resp = await http.get(f"{url}/health", timeout=3.0)
                        latency_ms = round((__import__("time").monotonic() - t0) * 1000)
                        status = "healthy" if resp.status_code < 400 else "degraded"
                        error = None
                    except Exception as exc:
                        latency_ms = round((__import__("time").monotonic() - t0) * 1000)
                        status = "unreachable"
                        error = str(exc)
                    results.append({
                        "server_id": sid,
                        "tenant_id": tenant_id,
                        "url": url,
                        "status": status,
                        "latency_ms": latency_ms,
                        "error": error,
                    })
                    # Update status in registry
                    if status != "healthy":
                        sdata["status"] = "UNHEALTHY"
                    else:
                        sdata["status"] = "ACTIVE"
                    servers[sid] = sdata
                client.setex(key, 3600, _json.dumps(servers))
        client.close()
    except Exception as exc:
        results.append({"status": "error", "reason": str(exc)})
    return results
```

**Tests:** `tests/scaling/test_celery_app.py`
```python
@pytest.mark.asyncio
async def test_check_servers_returns_results_with_respx():
    import respx, httpx
    with respx.mock:
        respx.get("http://mcp.example.com/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        from app.scaling.tasks import _check_servers
        # Inject fake Redis data via monkeypatching
        results = await _check_servers()
        assert isinstance(results, list)

def test_check_mcp_health_task_returns_servers_key():
    from app.scaling.tasks import check_mcp_health
    result = check_mcp_health()
    assert "servers" in result
    assert "servers_checked" in result
    assert isinstance(result["servers"], list)
```

**Acceptance:** `uv run pytest tests/scaling/test_celery_app.py::test_check_mcp_health_task_returns_servers_key -v` → PASS

---

### 1.2 🔴 Tool Risk Classification for Non-Jira Tools

**Current state:** `app/agent/tool_risk.py:32–33` returns `"unknown"` for all non-Jira tools.

**Gap:** GitHub, Slack, Stripe, Confluence tool calls bypass all risk governance.

**Implementation:** `app/agent/tool_risk.py` — extend `_RISK_RULES`:
```python
_RISK_RULES: list[tuple[str, list[str], str]] = [
    # (risk_level, keyword_tokens, match_on)
    # Destructive — never auto-approve
    ("destructive", ["delete", "destroy", "drop", "truncate", "purge", "wipe", "terminate", "revoke"], "any"),
    # Write-high — require approval in supervised mode
    ("write_high", ["create_issue", "create_pr", "merge", "deploy", "transition", "close", "resolve",
                    "publish", "send", "charge", "refund", "transfer", "update_permission", "add_member"], "any"),
    # Write-low — log but allow
    ("write_low", ["update", "edit", "patch", "comment", "assign", "label", "tag", "set"], "any"),
    # Read — always allow
    ("read", ["get", "list", "search", "fetch", "query", "find", "show", "describe", "status", "check"], "any"),
]

_CONNECTOR_OVERRIDES: dict[str, str] = {
    "stripe": "write_high",        # All Stripe writes are high-risk (money)
    "payments": "write_high",
    "billing": "write_high",
    "deploy": "destructive",
    "production": "write_high",
}

def classify_tool_risk(tool_name: str, server_name: str = "") -> str:
    combined = f"{server_name} {tool_name}".lower()
    # Check connector-level overrides first
    for connector_keyword, override_risk in _CONNECTOR_OVERRIDES.items():
        if connector_keyword in combined:
            return override_risk
    # Check rule tokens
    for risk_level, tokens, _ in _RISK_RULES:
        if any(t in combined for t in tokens):
            return risk_level
    return "read"  # Default safe assumption
```

**Tests:** `tests/agent/test_tool_risk.py`
```python
@pytest.mark.parametrize("tool_name,server,expected", [
    ("jira.delete_issue", "jira", "destructive"),
    ("github.merge_pr", "github", "write_high"),
    ("slack.send_message", "slack", "write_high"),
    ("stripe.create_charge", "stripe", "write_high"),
    ("jira.search_issues", "jira", "read"),
    ("confluence.get_page", "confluence", "read"),
    ("github.list_repos", "github", "read"),
    ("jira.create_issue", "jira", "write_high"),
    ("db.drop_table", "database", "destructive"),
])
def test_classify_tool_risk(tool_name, server, expected):
    from app.agent.tool_risk import classify_tool_risk
    assert classify_tool_risk(tool_name, server) == expected
```

---

### 1.3 🔴 OAuth/PKCE Auth Header Construction

**Current state:** `app/mcp/client.py:277–297` — `oauth_ac`, `oauth_cc`, `pkce`, `hmac` enum values exist but produce empty headers.

**Gap:** OAuth-authenticated MCP servers (e.g., Atlassian Cloud) return 401 on every tool call.

**Implementation:** `app/mcp/client.py` — complete `_build_auth_header()`:
```python
async def _build_auth_header(
    self, server_config: "MCPServerConfig", tenant_ctx: "TenantContext"
) -> str:
    auth_type = server_config.auth_type
    auth_config = server_config.auth_config or {}

    if auth_type == "bearer":
        token = await self._resolve_secret(auth_config.get("token_ref", ""), tenant_ctx)
        return f"Bearer {token}"
    elif auth_type == "api_key":
        key = await self._resolve_secret(auth_config.get("api_key_ref", ""), tenant_ctx)
        return key
    elif auth_type == "basic":
        import base64
        user = auth_config.get("username", "")
        pwd = await self._resolve_secret(auth_config.get("password_ref", ""), tenant_ctx)
        encoded = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        return f"Basic {encoded}"
    elif auth_type == "custom_header":
        return await self._resolve_secret(auth_config.get("value_ref", ""), tenant_ctx)
    elif auth_type in {"oauth_ac", "pkce", "oauth_cc"}:
        # Try to get a valid OAuth token
        token = self._oauth_manager.get_token(
            tenant_ctx.tenant_id, server_config.server_id
        ) if self._oauth_manager else None
        if token:
            if token.is_expired() and self._oauth_manager:
                try:
                    token = await self._oauth_manager.refresh_token(
                        tenant_id=tenant_ctx.tenant_id,
                        server_id=server_config.server_id,
                        token=token,
                        auth_config=auth_config,
                    )
                except Exception:
                    pass
            if token and not token.is_expired():
                return f"Bearer {token.access_token}"
        return ""
    elif auth_type == "hmac":
        # HMAC-SHA256 signed request (computed per-request in call_tool)
        return ""  # Header built dynamically in call_tool
    return ""
```

**Tests:** `tests/mcp/test_mcp_client.py`
```python
@pytest.mark.asyncio
async def test_build_auth_header_bearer():
    from app.mcp.client import MCPClient
    from unittest.mock import AsyncMock
    client = MCPClient(registry=AsyncMock(), secret_resolver=AsyncMock(return_value="my-token"))
    # Create mock server config with bearer auth
    from app.mcp.registry import MCPServerConfig, AuthType
    config = MCPServerConfig(server_id="s1", tenant_id="t1", name="Test",
                              url="http://x.com", auth_type="bearer",
                              auth_config={"token_ref": "vault://connectors/s1/token"})
    header = await client._build_auth_header(config, AsyncMock())
    assert header == "Bearer my-token"
```

---

### 1.4 🟡 Connector Health Snapshot Persistence

**Current state:** `POST /connectors/{id}/test` checks connectivity but result is never stored.

**Implementation:**

New model `app/db/models/mcp.py` — add `ConnectorHealthSnapshot`:
```python
class ConnectorHealthSnapshot(Base):
    __tablename__ = "connector_health_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True,
        default=lambda: uuid.uuid4().hex)
    server_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # healthy/degraded/unreachable
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_health_tenant_server_checked", "tenant_id", "server_id", "checked_at"),
        {"schema": None},
    )
```

New migration `app/db/migrations/versions/0015_connector_health.py`.

Update `app/api/connectors.py:test_connector()`:
```python
@router.post("/{server_id}/test")
async def test_connector(request: Request, server_id: str) -> dict[str, Any]:
    ...
    result = await _do_health_check(server_config)
    # Persist snapshot
    db = getattr(request.app.state, "db_session_factory", None)
    if db:
        async with db() as session, session.begin():
            session.add(ConnectorHealthSnapshot(
                server_id=server_id, tenant_id=tenant.tenant_id,
                status=result["status"], latency_ms=result.get("latency_ms"),
                error=result.get("error"),
            ))
    return result
```

New endpoint `GET /connectors/{id}/health`:
```python
@router.get("/{server_id}/health")
async def get_connector_health(request: Request, server_id: str,
                                limit: int = 20) -> list[dict[str, Any]]:
    """Return health check history for a connector."""
    tenant = _require_tenant(request)
    db = request.app.state.db_session_factory
    async with db() as session:
        result = await session.execute(
            select(ConnectorHealthSnapshot)
            .where(ConnectorHealthSnapshot.server_id == server_id,
                   ConnectorHealthSnapshot.tenant_id == tenant.tenant_id)
            .order_by(ConnectorHealthSnapshot.checked_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
    return [{"status": r.status, "latency_ms": r.latency_ms,
             "error": r.error, "checked_at": r.checked_at.isoformat()} for r in rows]
```

---

### 1.5 🟢 Semantic Tool Search

**Implementation:** `app/mcp/capability_search.py` (new file):
```python
"""Semantic tool capability search using embeddings."""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolMatch:
    server_id: str
    tool_name: str
    description: str
    score: float
    input_schema: dict[str, Any]


class CapabilitySearch:
    def __init__(self, embedder: Any = None) -> None:
        self._embedder = embedder
        # Cache: tool_ref_key -> embedding vector
        self._embeddings: dict[str, list[float]] = {}

    async def index_tools(self, tools: list[Any], tenant_id: str) -> None:
        """Pre-compute embeddings for all tools at discovery time."""
        if not self._embedder:
            return
        from app.providers.base import EmbedRequest
        descriptions = [f"{t.name}: {t.description}" for t in tools]
        try:
            resp = await self._embedder.embed(EmbedRequest(texts=descriptions))
            for tool, vec in zip(tools, resp.embeddings):
                key = f"{tenant_id}:{tool.server_id}:{tool.name}"
                self._embeddings[key] = vec
        except Exception:
            pass

    async def search(self, goal: str, tenant_id: str, top_k: int = 5) -> list[ToolMatch]:
        """Find tools most relevant to the given goal using cosine similarity."""
        if not self._embedder or not self._embeddings:
            return []
        from app.providers.base import EmbedRequest
        try:
            resp = await self._embedder.embed(EmbedRequest(texts=[goal]))
            query_vec = resp.embeddings[0]
        except Exception:
            return []

        scores: list[tuple[str, float]] = []
        for key, vec in self._embeddings.items():
            if not key.startswith(f"{tenant_id}:"):
                continue
            score = _cosine(query_vec, vec)
            scores.append((key, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        matches = []
        for key, score in scores[:top_k]:
            _, server_id, tool_name = key.split(":", 2)
            matches.append(ToolMatch(server_id=server_id, tool_name=tool_name,
                                     description="", score=score, input_schema={}))
        return matches


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a)) or 1e-9
    mag_b = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (mag_a * mag_b)
```

**Tests:**
```python
def test_cosine_similarity_identical_vectors():
    from app.mcp.capability_search import _cosine
    v = [1.0, 0.0, 0.0]
    assert _cosine(v, v) == pytest.approx(1.0)

def test_cosine_orthogonal_vectors():
    from app.mcp.capability_search import _cosine
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

@pytest.mark.asyncio
async def test_capability_search_empty_without_embedder():
    from app.mcp.capability_search import CapabilitySearch
    cs = CapabilitySearch(embedder=None)
    results = await cs.search("fix the bug", "t1")
    assert results == []
```

---

## Phase 2 — Agent Router / Intent Router

### 2.1 🔴 Create `app/agent/router.py`

**Current state:** File does not exist. When `agent_id=None`, `GoalService` builds a default `AgentGraph` with no agent-selection logic.

**Gap:** Users must manually specify `agent_id` on every goal. No auto-routing.

**Implementation:** `app/agent/router.py` (new file):
```python
"""Agent intent router — selects the best agent for a goal automatically."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger
from app.tenancy.context import TenantContext

logger = get_logger(__name__)


@dataclass
class AgentScore:
    agent_id: str
    agent_name: str
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class RoutingDecision:
    agent_id: str | None
    confidence: float          # 0.0 – 1.0
    reasoning: str
    fallback_agent_id: str | None = None
    all_scores: list[AgentScore] = field(default_factory=list)


class AgentRouter:
    """Routes goals to the most capable registered agent."""

    MIN_CONFIDENCE_THRESHOLD = 0.3

    def __init__(self, agent_store: Any, llm_provider: Any = None) -> None:
        self._store = agent_store
        self._llm = llm_provider

    async def route(self, goal: str, tenant_ctx: TenantContext) -> RoutingDecision:
        agents = self._store.list(tenant_ctx=tenant_ctx)
        if not agents:
            return RoutingDecision(agent_id=None, confidence=0.0,
                                   reasoning="No agents registered")

        scores: list[AgentScore] = []
        for agent in agents:
            score = self._score_agent(goal, agent)
            scores.append(score)

        scores.sort(key=lambda s: s.score, reverse=True)
        best = scores[0]

        if best.score < self.MIN_CONFIDENCE_THRESHOLD:
            logger.info("agent_router_low_confidence", goal=goal[:100],
                        best_score=best.score)
            return RoutingDecision(
                agent_id=None, confidence=best.score,
                reasoning=f"Low confidence ({best.score:.2f}) — no agent clearly matches",
                all_scores=scores,
            )

        logger.info("agent_router_selected", agent_id=best.agent_id,
                    confidence=best.score, goal=goal[:100])
        fallback = scores[1].agent_id if len(scores) > 1 else None
        return RoutingDecision(
            agent_id=best.agent_id, confidence=best.score,
            reasoning=f"Selected '{best.agent_name}': {', '.join(best.reasons)}",
            fallback_agent_id=fallback, all_scores=scores,
        )

    def _score_agent(self, goal: str, agent: dict[str, Any]) -> AgentScore:
        goal_lower = goal.lower()
        score = 0.0
        reasons: list[str] = []
        name = agent.get("name", "").lower()
        goal_template = agent.get("goal_template", "").lower()
        connector_ids: list[str] = agent.get("connector_ids", [])

        # Keyword match on agent name
        name_words = re.split(r"[\s_\-]+", name)
        for word in name_words:
            if len(word) > 3 and word in goal_lower:
                score += 0.3
                reasons.append(f"name token '{word}' matches goal")

        # Keyword match on goal template
        template_words = set(re.split(r"\s+", goal_template))
        goal_words = set(re.split(r"\s+", goal_lower))
        overlap = template_words & goal_words - {"the", "a", "an", "to", "for", "and"}
        if overlap:
            score += 0.2 * min(len(overlap), 5) / 5
            reasons.append(f"template overlap: {', '.join(list(overlap)[:3])}")

        # Connector match
        for cid in connector_ids:
            cid_lower = cid.lower()
            if cid_lower in goal_lower:
                score += 0.35
                reasons.append(f"connector '{cid}' relevant")

        # Autonomy mode bonus
        autonomy = agent.get("autonomy_mode", "")
        if "fully-autonomous" in autonomy:
            score += 0.05

        return AgentScore(agent_id=agent.get("agent_id", ""),
                          agent_name=agent.get("name", ""),
                          score=min(1.0, score), reasons=reasons)
```

Wire into `app/services/goal_service.py:submit_goal()`:
```python
# After _validate_agent_id(...):
if agent_id is None and self._app_state is not None:
    agent_store = self._get_agent_store()
    if agent_store is not None:
        from app.agent.router import AgentRouter
        router = AgentRouter(agent_store=agent_store)
        decision = await router.route(goal, tenant_ctx)
        if decision.agent_id and decision.confidence >= 0.3:
            agent_id = decision.agent_id
            logger.info("auto_routed_goal", goal_id=goal_id,
                        agent_id=agent_id, confidence=decision.confidence)
```

**Tests:** `tests/agent/test_router.py`
```python
from app.agent.router import AgentRouter, RoutingDecision
from app.tenancy.context import TenantContext, PlanTier

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


class FakeAgentStore:
    def __init__(self, agents): self._agents = agents
    def list(self, tenant_ctx=None): return self._agents


def test_router_selects_jira_agent_for_jira_goal():
    store = FakeAgentStore([
        {"agent_id": "a1", "name": "Jira Agent", "goal_template": "manage jira issues",
         "connector_ids": ["jira"], "autonomy_mode": "bounded-autonomous"},
        {"agent_id": "a2", "name": "Email Agent", "goal_template": "send emails",
         "connector_ids": ["gmail"], "autonomy_mode": "supervised"},
    ])
    router = AgentRouter(agent_store=store)
    import asyncio
    decision = asyncio.run(router.route("List all open Jira issues in project BAU", CTX))
    assert decision.agent_id == "a1"
    assert decision.confidence > 0.3


def test_router_returns_none_when_no_agents():
    store = FakeAgentStore([])
    router = AgentRouter(agent_store=store)
    import asyncio
    decision = asyncio.run(router.route("do something", CTX))
    assert decision.agent_id is None


def test_router_low_confidence_returns_none():
    store = FakeAgentStore([
        {"agent_id": "a1", "name": "Unrelated Agent", "goal_template": "xyz",
         "connector_ids": [], "autonomy_mode": "supervised"},
    ])
    router = AgentRouter(agent_store=store)
    import asyncio
    decision = asyncio.run(router.route("completely unrelated goal", CTX))
    assert decision.confidence < 0.3


def test_router_scores_include_all_agents():
    store = FakeAgentStore([
        {"agent_id": "a1", "name": "A1", "goal_template": "", "connector_ids": [], "autonomy_mode": ""},
        {"agent_id": "a2", "name": "A2", "goal_template": "", "connector_ids": [], "autonomy_mode": ""},
    ])
    router = AgentRouter(agent_store=store)
    import asyncio
    decision = asyncio.run(router.route("goal", CTX))
    assert len(decision.all_scores) == 2
```

---

### 2.2 🔴 Fix Hardcoded PROFESSIONAL Plan in Celery Workers

**Current state:** `app/scaling/tasks.py:141` — `plan=PlanTier.PROFESSIONAL` for every worker goal regardless of tenant's real plan.

**Gap:** FREE tenant goals run with PROFESSIONAL limits in Celery workers — rate limits bypassed.

**Implementation:** `app/scaling/tasks.py` — fix `run_goal()`:
```python
# Replace hardcoded plan with DB lookup:
async def _resolve_tenant_plan(tenant_id: str) -> "PlanTier":
    from app.tenancy.context import PlanTier
    try:
        from app.db.session import get_session_factory
        from app.db.models.tenant import Tenant
        from sqlalchemy import select
        db = get_session_factory()
        async with db() as session:
            result = await session.execute(
                select(Tenant.plan).where(Tenant.id == tenant_id)
            )
            plan_str = result.scalar_one_or_none()
            if plan_str and plan_str in PlanTier._value2member_map_:
                return PlanTier(plan_str)
    except Exception:
        pass
    return PlanTier.FREE  # default to most restrictive

# In run_goal():
plan = _run_async(_resolve_tenant_plan(tenant_id))
tenant_ctx = TenantContext(tenant_id=tenant_id, plan=plan, api_key_id="celery-worker")
```

**Tests:**
```python
@pytest.mark.asyncio
async def test_resolve_tenant_plan_defaults_to_free_without_db():
    from app.scaling.tasks import _resolve_tenant_plan
    from app.tenancy.context import PlanTier
    plan = await _resolve_tenant_plan("nonexistent-tenant")
    assert plan == PlanTier.FREE
```

---

## Phase 3 — Real Tool-Using Planner

### 3.1 🔴 Structured JSON Plan Format

**Current state:** `app/agent/prompts.py:PLANNER_SYSTEM` produces `{"steps": ["free text description"]}`. Tool names are extracted via regex heuristics in `graph.py:918–931`.

**Gap:** Planner has no schema contract with executor. Tool names are guessed, not declared.

**Implementation:**

New file `app/agent/structured_plan.py`:
```python
"""Structured plan format with explicit tool bindings."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredStep:
    id: str
    description: str
    tool: str | None = None          # "jira.search_issues" or None for LLM-only steps
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    risk: str = "read"               # read / write_low / write_high / destructive
    expected_output: str = ""        # human description of expected output


@dataclass
class StructuredPlan:
    steps: list[StructuredStep]
    goal: str = ""
    iteration: int = 0

    @classmethod
    def from_llm_response(cls, text: str) -> "StructuredPlan":
        """Parse LLM response into a StructuredPlan. Falls back gracefully."""
        # Extract JSON block
        json_match = re.search(r'\{[\s\S]*"steps"[\s\S]*\}', text)
        if not json_match:
            # Legacy: plain text step list
            lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
            steps = [StructuredStep(id=f"s{i+1}", description=l)
                     for i, l in enumerate(lines) if l]
            return cls(steps=steps)
        try:
            data = json.loads(json_match.group())
            steps_raw = data.get("steps", [])
            steps = []
            for i, s in enumerate(steps_raw):
                if isinstance(s, str):
                    steps.append(StructuredStep(id=f"s{i+1}", description=s))
                elif isinstance(s, dict):
                    steps.append(StructuredStep(
                        id=s.get("id", f"s{i+1}"),
                        description=s.get("description", s.get("step", "")),
                        tool=s.get("tool"),
                        arguments=s.get("arguments", s.get("args", {})),
                        depends_on=s.get("depends_on", []),
                        risk=s.get("risk", "read"),
                        expected_output=s.get("expected_output", ""),
                    ))
            return cls(steps=steps)
        except (json.JSONDecodeError, KeyError):
            return cls(steps=[])

    def to_step_list(self) -> list[str]:
        """Convert to legacy list[str] for backward compatibility."""
        return [s.description for s in self.steps]

    def execution_waves(self) -> list[list[StructuredStep]]:
        """Topological sort into parallel execution waves."""
        resolved: set[str] = set()
        waves: list[list[StructuredStep]] = []
        remaining = list(self.steps)
        while remaining:
            wave = [s for s in remaining
                    if all(dep in resolved for dep in s.depends_on)]
            if not wave:
                wave = remaining[:1]  # Break cycle
            waves.append(wave)
            for s in wave:
                resolved.add(s.id)
                remaining.remove(s)
        return waves
```

Update `app/agent/prompts.py` — add `STRUCTURED_PLANNER_SYSTEM`:
```python
STRUCTURED_PLANNER_SYSTEM = """You are a precise autonomous agent planner.

Given a goal and available tools, produce a JSON execution plan.

RULES:
- Each step MUST reference the exact tool name from the available tools list, or null if no tool is needed
- Set risk: "read" for read-only operations, "write_low" for reversible writes, "write_high" for important writes, "destructive" for irreversible deletes
- Set depends_on with step IDs that must complete before this step
- Steps without depends_on run in parallel

OUTPUT FORMAT (strict JSON, no markdown):
{
  "steps": [
    {
      "id": "s1",
      "description": "Human description of what this step does",
      "tool": "server_name.tool_name or null",
      "arguments": {"param": "value"},
      "depends_on": [],
      "risk": "read",
      "expected_output": "description of what this step returns"
    }
  ]
}"""
```

**Tests:** `tests/agent/test_structured_plan.py`
```python
from app.agent.structured_plan import StructuredPlan, StructuredStep

def test_parse_structured_json():
    response = '''{"steps": [
        {"id": "s1", "description": "Search issues", "tool": "jira.search_issues",
         "arguments": {"jql": "project=BAU"}, "depends_on": [], "risk": "read"},
        {"id": "s2", "description": "Create page", "tool": "confluence.create_page",
         "arguments": {}, "depends_on": ["s1"], "risk": "write_low"}
    ]}'''
    plan = StructuredPlan.from_llm_response(response)
    assert len(plan.steps) == 2
    assert plan.steps[0].tool == "jira.search_issues"
    assert plan.steps[1].depends_on == ["s1"]

def test_parse_legacy_text():
    plan = StructuredPlan.from_llm_response("Step 1: do A\nStep 2: do B")
    assert len(plan.steps) == 2
    assert plan.steps[0].tool is None

def test_execution_waves_sequential():
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="A", depends_on=[]),
        StructuredStep(id="s2", description="B", depends_on=["s1"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 2
    assert waves[0][0].id == "s1"
    assert waves[1][0].id == "s2"

def test_execution_waves_parallel():
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="A", depends_on=[]),
        StructuredStep(id="s2", description="B", depends_on=[]),
        StructuredStep(id="s3", description="C", depends_on=["s1", "s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 2
    assert len(waves[0]) == 2  # s1 and s2 parallel
    assert waves[1][0].id == "s3"
```

---

## Phase 4 — Workflow DAG Engine

### 4.1 🔴 Step-Level Checkpoint Write + Read

**Current state:** `app/db/migrations/versions/0011_goal_events_checkpoints.py` creates `goal_checkpoints` table. `app/db/models/goal.py:133–166` defines `GoalCheckpoint` model. Nothing ever writes to it.

**Gap:** If the process crashes mid-execution, the goal restarts from step 0. All prior tool calls are re-executed.

**Implementation:** `app/agent/graph.py` — add checkpoint methods:
```python
async def _write_checkpoint(
    self, goal_id: str, step_index: int, state: "AgentState", tenant_ctx: "TenantContext"
) -> None:
    """Persist current state after each successful step."""
    if self._db_session_factory is None:
        return
    try:
        import json as _json
        from app.db.models.goal import GoalCheckpoint
        from app.db.rls import sqlalchemy_rls_context
        payload = {
            "step_index": step_index,
            "plan": state.plan,
            "steps_completed": [
                {"description": s.description, "output": s.output, "status": s.status.value}
                for s in state.steps[:step_index + 1]
            ],
            "context_keys": list(state.context.keys()),
            "iterations": state.iterations,
        }
        async with self._db_session_factory() as session, session.begin(), \
                   sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
            ck = GoalCheckpoint(
                goal_id=goal_id,
                tenant_id=tenant_ctx.tenant_id,
                checkpoint_key=f"step_{step_index}",
                sequence=step_index,
                payload=payload,
                recovery_status="checkpointed",
            )
            session.add(ck)
    except Exception as exc:
        logger.warning("checkpoint_write_failed", goal_id=goal_id, error=str(exc))

async def _load_checkpoint(
    self, goal_id: str, tenant_ctx: "TenantContext"
) -> dict[str, Any] | None:
    """Load the latest checkpoint for goal resume."""
    if self._db_session_factory is None:
        return None
    try:
        from sqlalchemy import select
        from app.db.models.goal import GoalCheckpoint
        from app.db.rls import sqlalchemy_rls_context
        async with self._db_session_factory() as session, \
                   sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
            result = await session.execute(
                select(GoalCheckpoint)
                .where(GoalCheckpoint.goal_id == goal_id,
                       GoalCheckpoint.tenant_id == tenant_ctx.tenant_id)
                .order_by(GoalCheckpoint.sequence.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return row.payload if row else None
    except Exception as exc:
        logger.warning("checkpoint_load_failed", goal_id=goal_id, error=str(exc))
        return None
```

Call `_write_checkpoint()` after each step completes in `_execute_node()`.

**Tests:**
```python
@pytest.mark.asyncio
async def test_checkpoint_round_trip_without_db():
    """Without DB, checkpoint silently no-ops."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from app.agent.state import AgentState, GoalStatus
    from app.tenancy.context import TenantContext, PlanTier
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.intelligence.guardrails import GuardrailChecker

    fake = FakeProvider(responses=['{"steps":["do it"]}', "done", '{"success":true,"reason":"ok"}'])
    graph = AgentGraph(planner=fake, executor=fake, verifier=fake,
                       result_processor=ResultProcessor(), dedup_cache=DeduplicationCache(),
                       rollback_engine=RollbackEngine(), guardrail_checker=GuardrailChecker())
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
    state = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    # Should not raise
    await graph._write_checkpoint("g1", 0, state, ctx)
    result = await graph._load_checkpoint("g1", ctx)
    assert result is None
```

---

### 4.2 🔴 Goal Pause / Resume

**Current state:** `GoalService` only has `cancel_goal()`. No pause operation.

**Implementation:** `app/services/goal_service.py` — add `pause_goal()` and `resume_goal()`:
```python
_PAUSED_GOALS: dict[str, asyncio.Event] = {}  # goal_id -> resume event

async def pause_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
    record = self._get_record(goal_id, tenant_ctx)
    if record.status not in {GoalStatus.EXECUTING, GoalStatus.PLANNING}:
        raise ValueError(f"Goal {goal_id} is not running (status: {record.status})")
    _PAUSED_GOALS[goal_id] = asyncio.Event()
    record.status = GoalStatus.WAITING_HUMAN
    await self._dispatch_event(goal_id, {"type": "goal_paused"}, tenant_ctx=tenant_ctx)
    return {"goal_id": goal_id, "status": "paused"}

async def resume_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
    record = self._get_record(goal_id, tenant_ctx)
    event = _PAUSED_GOALS.pop(goal_id, None)
    if event:
        event.set()
    record.status = GoalStatus.EXECUTING
    await self._dispatch_event(goal_id, {"type": "goal_resumed"}, tenant_ctx=tenant_ctx)
    return {"goal_id": goal_id, "status": "resumed"}
```

New API endpoints in `app/api/goals.py`:
```python
@router.post("/{goal_id}/pause")
async def pause_goal(request: Request, goal_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        return await svc.pause_goal(goal_id=goal_id, tenant_ctx=tenant)
    except (NotFoundError, ValueError) as exc:
        code = 404 if isinstance(exc, NotFoundError) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc

@router.post("/{goal_id}/resume")
async def resume_goal(request: Request, goal_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        return await svc.resume_goal(goal_id=goal_id, tenant_ctx=tenant)
    except (NotFoundError, ValueError) as exc:
        code = 404 if isinstance(exc, NotFoundError) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
```

**Tests:**
```python
@pytest.mark.asyncio
async def test_pause_resume_goal():
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name":"T","email":"pr@t.com"})
        c.headers["X-API-Key"] = r.json()["raw_key"]
        # Submit a non-dry-run goal (will be in executing)
        r2 = await c.post("/goals", json={"goal": "long running task", "dry_run": False})
        goal_id = r2.json()["goal_id"]
        # Give it a moment to start
        import asyncio; await asyncio.sleep(0.05)
        # Pause
        r3 = await c.post(f"/goals/{goal_id}/pause")
        assert r3.status_code == 200
        assert r3.json()["status"] == "paused"
        # Resume
        r4 = await c.post(f"/goals/{goal_id}/resume")
        assert r4.status_code == 200
```

---

## Phase 5 — Durable Execution Kernel

### 5.1 🔴 Distributed Lock (At-Most-Once Execution)

**Current state:** No distributed lock. Two Celery workers can execute the same goal concurrently after a retry.

**Implementation:** `app/reliability/distributed_lock.py` (new file):
```python
"""Redis-backed distributed lock for at-most-once goal execution."""
from __future__ import annotations
import uuid
from typing import Any


class GoalExecutionLock:
    """Redis SET NX PX lock ensuring at-most-once goal execution per cluster."""

    KEY_PREFIX = "goal_lock:"

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._lock_value = uuid.uuid4().hex

    async def acquire(self, goal_id: str, ttl_ms: int = 300_000) -> bool:
        """Acquire lock. Returns True if acquired, False if already locked."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        result = await self._redis.set(key, self._lock_value, nx=True, px=ttl_ms)
        return result is not None

    async def release(self, goal_id: str) -> None:
        """Release lock only if we own it (Lua script for atomicity)."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await self._redis.eval(lua_script, 1, key, self._lock_value)
        except Exception:
            pass

    async def extend(self, goal_id: str, ttl_ms: int = 300_000) -> bool:
        """Extend lock TTL if still owned."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("pexpire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        try:
            result = await self._redis.eval(lua_script, 1, key, self._lock_value, ttl_ms)
            return bool(result)
        except Exception:
            return False
```

Wire in `app/scaling/tasks.py:run_goal()`:
```python
# At start of run_goal, before any DB operations:
_lock = None
_redis_url = celery_app.conf.broker_url
if _redis_url:
    try:
        import redis.asyncio as aioredis
        _lock_redis = aioredis.from_url(_redis_url, decode_responses=True)
        from app.reliability.distributed_lock import GoalExecutionLock
        _lock = GoalExecutionLock(_lock_redis)
        acquired = _run_async(_lock.acquire(goal_id, ttl_ms=1_800_000))
        if not acquired:
            logger.warning("goal_already_executing", goal_id=goal_id)
            return {"status": "skipped", "goal_id": goal_id, "reason": "already executing"}
    except Exception as exc:
        logger.warning("lock_acquire_failed", goal_id=goal_id, error=str(exc))

try:
    # ... main execution ...
finally:
    if _lock:
        _run_async(_lock.release(goal_id))
```

**Tests:**
```python
@pytest.mark.asyncio
async def test_distributed_lock_acquire_and_release():
    from unittest.mock import AsyncMock, MagicMock
    from app.reliability.distributed_lock import GoalExecutionLock

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)  # lock acquired
    mock_redis.eval = AsyncMock(return_value=1)

    lock = GoalExecutionLock(mock_redis)
    assert await lock.acquire("goal-1") is True
    await lock.release("goal-1")
    mock_redis.eval.assert_called_once()

@pytest.mark.asyncio
async def test_distributed_lock_already_locked():
    from unittest.mock import AsyncMock, MagicMock
    from app.reliability.distributed_lock import GoalExecutionLock

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=None)  # lock NOT acquired (already exists)

    lock = GoalExecutionLock(mock_redis)
    assert await lock.acquire("goal-1") is False
```

---

### 5.2 🔴 Hard Timeout Per Goal

**Implementation:** `app/scaling/tasks.py` — wrap agent execution:
```python
# Add to PLAN_LIMITS in app/tenancy/context.py:
@dataclass(frozen=True, slots=True)
class PlanLimits:
    ...
    goal_timeout_seconds: int  # NEW

PLAN_LIMITS = {
    PlanTier.FREE:         PlanLimits(..., goal_timeout_seconds=300),    # 5 min
    PlanTier.STARTER:      PlanLimits(..., goal_timeout_seconds=900),    # 15 min
    PlanTier.PROFESSIONAL: PlanLimits(..., goal_timeout_seconds=1800),   # 30 min
    PlanTier.ENTERPRISE:   PlanLimits(..., goal_timeout_seconds=7200),   # 2 hours
}

# In run_goal() task:
timeout_s = PLAN_LIMITS[plan].goal_timeout_seconds
try:
    state = _run_async(
        asyncio.wait_for(
            loop.run(goal=effective_goal, tenant_ctx=tenant_ctx,
                     event_callback=worker_event_callback),
            timeout=float(timeout_s)
        )
    )
except asyncio.TimeoutError:
    _run_async(mark_worker_failed(
        TimeoutError(f"Goal exceeded {timeout_s}s timeout")
    ))
    return {"status": "failed", "goal_id": goal_id, "reason": "timeout"}
```

---

## Phase 6 — Universal RPA / Browser Agent

### 6.1 🔴 Stateful Multi-Step Browser Sessions

**Current state:** `RPAExecutor._sessions` dict is declared but never populated. Every call opens/closes a fresh browser.

**Implementation:** `app/rpa/session_manager.py` (new file):
```python
"""Manages live Playwright browser sessions across multiple RPA tool calls."""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrowserSession:
    session_id: str
    tenant_id: str
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    _playwright: Any = field(default=None, repr=False)
    _browser: Any = field(default=None, repr=False)
    _context: Any = field(default=None, repr=False)
    _page: Any = field(default=None, repr=False)

    @property
    def page(self) -> Any:
        return self._page

    async def close(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass


class BrowserSessionManager:
    """Keeps Playwright browser sessions alive across RPA tool calls."""

    def __init__(self, idle_timeout_seconds: int = 300) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._idle_timeout = idle_timeout_seconds
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str, tenant_id: str,
                             headless: bool = True) -> BrowserSession:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session and session.tenant_id == tenant_id:
                session.last_used_at = time.monotonic()
                return session
            # Create new session
            try:
                from playwright.async_api import async_playwright
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=headless)
                context = await browser.new_context(viewport={"width": 1280, "height": 720})
                page = await context.new_page()
                session = BrowserSession(
                    session_id=session_id, tenant_id=tenant_id,
                    _playwright=pw, _browser=browser, _context=context, _page=page
                )
            except ImportError:
                session = BrowserSession(session_id=session_id, tenant_id=tenant_id)
            self._sessions[session_id] = session
            return session

    async def close(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                await session.close()

    async def cleanup_expired(self) -> int:
        cutoff = time.monotonic() - self._idle_timeout
        to_close = [s for s in self._sessions.values() if s.last_used_at < cutoff]
        for s in to_close:
            await self.close(s.session_id)
        return len(to_close)
```

Update `app/rpa/executor.py` to use `BrowserSessionManager`:
```python
class RPAExecutor:
    def __init__(self, artifact_store=None, session_manager=None) -> None:
        self._playwright_available = self._check_playwright()
        self._session_manager = session_manager or BrowserSessionManager()
        self._artifact_store = artifact_store

    async def execute(self, *, tool_name, arguments, session_id=None,
                      tenant_id="", goal_id="") -> RPAResult:
        start = time.monotonic()
        sid = session_id or f"ephemeral-{uuid.uuid4().hex[:8]}"
        ephemeral = session_id is None

        if self._playwright_available:
            result = await self._execute_with_playwright(
                tool_name=tool_name, arguments=arguments,
                session_id=sid, tenant_id=tenant_id, goal_id=goal_id
            )
        else:
            result = await self._execute_simulation(tool_name=tool_name, arguments=arguments)

        if ephemeral:
            await self._session_manager.close(sid)

        result.duration_ms = (time.monotonic() - start) * 1000
        return result
```

---

## Phase 7 — Agent Memory System

### 7.1 🔴 DB Write Path for Memory Stores

**Current state:** `app/memory/execution.py` and `app/memory/long_term.py` are pure Python dicts — DB tables exist in migrations but are never written to.

**Implementation:** `app/memory/execution.py` — add async DB methods:
```python
async def record_async(self, *, goal_text: str, plan: list[str], success: bool,
                        tenant_ctx: "TenantContext", db: Any = None) -> None:
    """Record to both in-memory and PostgreSQL."""
    # In-memory (existing)
    tid = tenant_ctx.tenant_id
    self._memories.setdefault(tid, []).append({
        "goal_text": goal_text, "plan": plan, "success": success,
        "recorded_at": datetime.now(UTC).isoformat()
    })
    # DB
    if db is None:
        return
    try:
        import uuid as _uuid
        from sqlalchemy import text
        async with db() as session, session.begin():
            await session.execute(
                text("""INSERT INTO execution_memory
                        (id, tenant_id, goal_text, plan, success, created_at)
                        VALUES (:id, :tid, :goal, :plan, :success, NOW())"""),
                {"id": _uuid.uuid4().hex, "tid": tenant_ctx.tenant_id,
                 "goal": goal_text[:500], "plan": json.dumps(plan), "success": success}
            )
    except Exception as exc:
        logger.warning("execution_memory_db_write_failed", error=str(exc))

async def recall_async(self, goal: str, tenant_ctx: "TenantContext",
                        db: Any = None, top_k: int = 3) -> list[dict[str, Any]]:
    """Recall from DB first, fall back to in-memory."""
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session:
                result = await session.execute(
                    text("""SELECT goal_text, plan, success FROM execution_memory
                            WHERE tenant_id = :tid AND success = true
                            ORDER BY created_at DESC LIMIT :k"""),
                    {"tid": tenant_ctx.tenant_id, "k": top_k}
                )
                rows = result.fetchall()
                if rows:
                    return [{"goal_text": r[0], "plan": json.loads(r[1]), "success": r[2]}
                            for r in rows]
        except Exception as exc:
            logger.warning("execution_memory_db_read_failed", error=str(exc))
    return self.recall(goal, tenant_ctx)
```

**Wire failure memory into planning (`app/agent/graph.py`):**
```python
# In _node_rag_retrieval:
if self._exec_memory:
    try:
        failures = self._exec_memory.recall_failures(state.goal, tenant_ctx)
        if failures:
            failure_text = "\n".join([f"- {f.get('goal_text','')[:100]}" for f in failures[-3:]])
            rag_parts.append(f"\n## Previously Failed Approaches\nAvoid these:\n{failure_text}")
    except Exception:
        pass
```

### 7.2 🔴 Memory REST API

New `app/api/memory.py`:
```python
from fastapi import APIRouter, HTTPException, Request
from typing import Any

router = APIRouter(prefix="/memory", tags=["memory"])

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx

@router.get("/long-term")
async def list_long_term_memories(request: Request) -> list[dict[str, Any]]:
    tenant = _require_tenant(request)
    mem = request.app.state.long_term_memory
    return [{"memory_id": m.memory_id, "content": m.content,
             "memory_type": m.memory_type, "confidence": m.confidence}
            for m in mem.list_all(tenant_ctx=tenant)]

@router.delete("/long-term/{memory_id}")
async def delete_memory(request: Request, memory_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    mem = request.app.state.long_term_memory
    ok = mem.delete(memory_id=memory_id, tenant_ctx=tenant)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory_id": memory_id, "deleted": True}

@router.delete("")
async def clear_all_memories(request: Request) -> dict[str, Any]:
    """GDPR: clear all memories for this tenant."""
    tenant = _require_tenant(request)
    mem = request.app.state.long_term_memory
    all_mems = mem.list_all(tenant_ctx=tenant)
    for m in all_mems:
        mem.delete(memory_id=m.memory_id, tenant_ctx=tenant)
    return {"cleared": len(all_mems)}
```

Register in `app/main.py`:
```python
from app.api.memory import router as memory_router
app.include_router(memory_router)
```

---

## Phase 8 — Knowledge OS / RAG Workbench

### 8.1 🔴 Remove Random Embedding Fallback

**Current state:** `app/api/knowledge.py:67–72` `_fallback_embedding()` returns a random unit-norm vector when no embedder is configured.

**Fix:**
```python
# Replace _fallback_embedding() with:
def _fallback_embedding(dim: int = 768) -> list[float]:
    raise HTTPException(
        status_code=503,
        detail=(
            "Embedding provider not configured. "
            "Set VOYAGE_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY to enable knowledge features."
        )
    )
```

### 8.2 🔴 File Upload Endpoint

New endpoint in `app/api/knowledge.py`:
```python
from fastapi import File, Form, UploadFile

@router.post("/ingest/file")
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    collection_id: str = Form(...),
) -> dict[str, Any]:
    """Ingest a file (txt, md, py, ts, pdf) into a knowledge collection."""
    tenant = _require_tenant(request)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)

    content_bytes = await file.read()
    filename = file.filename or "uploaded_file"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    # Parse content based on file type
    if ext == "pdf":
        try:
            import pypdf
            import io
            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            raise HTTPException(status_code=422, detail="pypdf not installed for PDF support")
    elif ext in {"docx", "doc"}:
        try:
            import docx
            import io
            doc = docx.Document(io.BytesIO(content_bytes))
            text = "\n".join(para.text for para in doc.paragraphs)
        except ImportError:
            raise HTTPException(status_code=422, detail="python-docx not installed for DOCX support")
    else:
        text = content_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(status_code=422, detail="File is empty or could not be parsed")

    # Chunk and ingest
    chunk_size = 512
    overlap = 64
    chunks_created = 0
    for i in range(0, len(text), chunk_size - overlap):
        chunk = text[i:i + chunk_size]
        if not chunk.strip():
            continue
        from app.providers.base import EmbedRequest
        try:
            embedding_resp = await embed_texts([chunk], provider=embedder)
            embedding = embedding_resp[0] if embedding_resp else []
        except Exception:
            embedding = []
        await store.ingest_chunk(
            collection_id=collection_id,
            content=chunk,
            embedding=embedding,
            metadata={"source_file": filename, "ext": ext, "char_offset": i},
            tenant_ctx=tenant,
        )
        chunks_created += 1

    return {"filename": filename, "chunks_created": chunks_created,
            "collection_id": collection_id}
```

### 8.3 🟡 Source Citations in Search Results

Update `GET /knowledge/search` response to include document metadata:
```python
@router.get("/search")
async def search_knowledge(request: Request, q: str, collection_id: str | None = None,
                            top_k: int = 5, threshold: float = 0.3) -> list[dict[str, Any]]:
    ...
    results = await store.hybrid_search(query=q, ...)
    return [
        {
            "chunk_id": r.chunk_id,
            "content": r.content,
            "score": r.score,
            "vector_score": r.vector_score,
            "trigram_score": r.trigram_score,
            # New citation fields:
            "source_file": r.metadata.get("source_file", ""),
            "source_url": r.metadata.get("source_url", ""),
            "char_offset": r.metadata.get("char_offset"),
            "ingested_at": r.metadata.get("ingested_at", ""),
        }
        for r in results
    ]
```

---

## Phase 9 — Governance Policy Engine

### 9.1 🔴 Policy Persistence to DB

**Current state:** `app/api/governance.py:69–73` — policy registry is `app.state._policy_registry` dict, lost on restart.

**Implementation:** New DB model `app/db/models/governance.py` — add `PolicyRecord`:
```python
class PolicyRecord(Base):
    __tablename__ = "governance_policies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True,
        default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tools_pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # deny|require_approval
    priority: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index("ix_policies_tenant", "tenant_id"),
        {"schema": None}
    )
```

New migration `app/db/migrations/versions/0016_governance_policies_table.py`.

Update `app/api/governance.py` to use DB-backed store:
```python
async def _get_policy_store(request: Request) -> "PolicyStore":
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        # Fall back to in-memory dict on app.state
        if not hasattr(request.app.state, "_policy_registry"):
            setattr(request.app.state, "_policy_registry", {})
        return InMemoryPolicyStore(request.app.state._policy_registry)
    return DBPolicyStore(db)
```

### 9.2 🔴 Real Budget Cost Calculation

**Current state:** `graph.py:399` uses hardcoded `cost_usd=0.01`.

**Implementation:** `app/governance/pricing.py` (new file):
```python
"""LLM pricing table for cost estimation."""
_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_cost_per_1k_tokens, output_cost_per_1k_tokens)
    "claude-opus-4-8":      (0.015,  0.075),
    "claude-sonnet-4-5":    (0.003,  0.015),
    "claude-haiku-3-5":     (0.00025, 0.00125),
    "gpt-4o":               (0.005,  0.015),
    "gpt-4o-mini":          (0.00015, 0.0006),
    "llama-3.1-70b":        (0.0009, 0.0009),
}

def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a completion."""
    model_lower = model.lower()
    for key, (inp_rate, out_rate) in _PRICING.items():
        if key in model_lower:
            return (input_tokens * inp_rate + output_tokens * out_rate) / 1000
    # Unknown model: use GPT-4o pricing as conservative estimate
    return (input_tokens * 0.005 + output_tokens * 0.015) / 1000
```

Wire into `graph.py._execute_step()`:
```python
# After executor LLM call:
from app.governance.pricing import estimate_cost
actual_cost = estimate_cost(resp.model, resp.input_tokens, resp.output_tokens)
# Accumulate in state
state.context["total_cost_usd"] = state.context.get("total_cost_usd", 0.0) + actual_cost
# Budget check with real cost
if self._cost_controller:
    ok = self._cost_controller.check_and_record(
        goal_id=state.goal_id, cost_usd=actual_cost, tenant_ctx=tenant_ctx
    )
```

### 9.3 🔴 HITL Approval Persistence

**Current state:** `HITLGateway._requests` is in-memory.

**Implementation:** Add DB persistence to `app/governance/hitl.py`:
```python
async def request_approval_async(self, *, goal_id: str, action: str,
                                   risk_level: str, tenant_ctx: "TenantContext",
                                   db: Any = None) -> str:
    req_id = self.request_approval(goal_id=goal_id, action=action,
                                    risk_level=risk_level, tenant_ctx=tenant_ctx)
    if db:
        try:
            from app.db.models.governance import ApprovalRequest as DBApprovalRequest
            from app.db.rls import sqlalchemy_rls_context
            async with db() as session, session.begin(), \
                       sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                session.add(DBApprovalRequest(
                    id=req_id, goal_id=goal_id, tenant_id=tenant_ctx.tenant_id,
                    action=action, risk_level=risk_level, status="pending",
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                ))
        except Exception as exc:
            logger.warning("hitl_db_persist_failed", error=str(exc))
    return req_id

async def load_pending_from_db(self, db: Any, tenant_id: str) -> int:
    """On startup: restore pending approvals from DB so in-flight goals can resume."""
    if db is None:
        return 0
    try:
        from sqlalchemy import select
        from app.db.models.governance import ApprovalRequest as DBApprovalRequest
        async with db() as session:
            result = await session.execute(
                select(DBApprovalRequest)
                .where(DBApprovalRequest.tenant_id == tenant_id,
                       DBApprovalRequest.status == "pending")
            )
            rows = result.scalars().all()
        for row in rows:
            req = ApprovalRequest(goal_id=row.goal_id, action=row.action,
                                   risk_level=row.risk_level or "unknown",
                                   request_id=row.id, status=ApprovalStatus.PENDING)
            self._requests[(tenant_id, row.id)] = req
        return len(rows)
    except Exception as exc:
        logger.warning("hitl_load_failed", error=str(exc))
        return 0
```

---

## Phase 10 — Human-In-The-Loop Control Plane

### 10.1 🔴 Approval Notifications

**Implementation:** `app/services/notification_service.py` (new file):
```python
"""Sends notifications when HITL approval is required."""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class NotificationChannel:
    channel_id: str
    tenant_id: str
    channel_type: str   # "slack" | "email" | "webhook"
    config: dict[str, Any]


class NotificationService:
    def __init__(self) -> None:
        self._channels: dict[str, list[NotificationChannel]] = {}

    def add_channel(self, channel: NotificationChannel) -> None:
        self._channels.setdefault(channel.tenant_id, []).append(channel)

    def get_channels(self, tenant_id: str) -> list[NotificationChannel]:
        return self._channels.get(tenant_id, [])

    async def notify_approval_required(
        self, *, request_id: str, goal_id: str, action: str,
        risk_level: str, tenant_id: str
    ) -> None:
        channels = self.get_channels(tenant_id)
        message = {
            "text": f":warning: *Approval Required* for goal `{goal_id}`\n"
                    f"Action: `{action}`\nRisk: `{risk_level}`\n"
                    f"Request ID: `{request_id}`",
            "request_id": request_id,
            "goal_id": goal_id,
            "action": action,
            "risk_level": risk_level,
        }
        for channel in channels:
            try:
                await self._send_to_channel(channel, message)
            except Exception as exc:
                logger.warning("notification_failed", channel_type=channel.channel_type,
                               error=str(exc))

    async def _send_to_channel(self, channel: NotificationChannel,
                                message: dict[str, Any]) -> None:
        if channel.channel_type == "slack":
            webhook_url = channel.config.get("webhook_url", "")
            if webhook_url:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(webhook_url,
                                      json={"text": message["text"]})
        elif channel.channel_type == "webhook":
            url = channel.config.get("url", "")
            if url:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(url, json=message)
```

New endpoints in `app/api/governance.py`:
```python
@router.get("/notifications")
async def list_notification_channels(request: Request) -> list[dict[str, Any]]:
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        return []
    return [{"channel_id": c.channel_id, "type": c.channel_type}
            for c in svc.get_channels(tenant.tenant_id)]

class CreateNotificationChannelRequest(BaseModel):
    channel_type: str  # slack|email|webhook
    config: dict[str, Any]

@router.post("/notifications", status_code=201)
async def create_notification_channel(request: Request,
                                       body: CreateNotificationChannelRequest) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        raise HTTPException(503, "Notification service not available")
    from app.services.notification_service import NotificationChannel
    channel = NotificationChannel(
        channel_id=uuid.uuid4().hex, tenant_id=tenant.tenant_id,
        channel_type=body.channel_type, config=body.config
    )
    svc.add_channel(channel)
    return {"channel_id": channel.channel_id, "type": channel.channel_type}
```

### 10.2 🔴 Approval Expiry Enforcement

**Implementation:** New Celery task + fix expiry setting:
```python
# In hitl.py request_approval():
req = ApprovalRequest(goal_id=goal_id, action=action, risk_level=risk_level)
req.expires_at = (datetime.now(UTC) + timedelta(seconds=self._timeout)).isoformat()
```

New `app/scaling/tasks.py` task:
```python
@celery_app.task(name="app.scaling.tasks.expire_pending_approvals",
                 bind=True, max_retries=0)
def expire_pending_approvals(self: Any) -> dict[str, Any]:
    """Auto-reject approval requests past their expires_at."""
    return _run_async(_do_expire_approvals())

async def _do_expire_approvals() -> dict[str, Any]:
    from app.db.session import get_session_factory
    from sqlalchemy import update, select
    from app.db.models.governance import ApprovalRequest
    from datetime import UTC, datetime
    try:
        db = get_session_factory()
        async with db() as session, session.begin():
            result = await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.status == "pending",
                       ApprovalRequest.expires_at < datetime.now(UTC))
                .values(status="timed_out")
                .returning(ApprovalRequest.id)
            )
            expired_ids = [row[0] for row in result.fetchall()]
        return {"expired_count": len(expired_ids), "expired_ids": expired_ids}
    except Exception as exc:
        return {"error": str(exc)}
```

Add to Beat schedule: every 60 seconds.

---

## Phase 11 — Agent Collaboration / Multi-Agent Deliberation

### 11.1 🔴 LLM-Based Consensus Synthesis

**Current state:** `app/collab/agent_collab.py:43–60` — rule-based string matching for "agree"/"disagree".

**Implementation:** `app/collab/agent_collab.py` — replace with LLM call:
```python
async def synthesize_consensus_llm(
    self, session_id: str, provider: Any, tenant_ctx: "TenantContext"
) -> ConsensusResult:
    """Use LLM to synthesize consensus from all rounds."""
    rounds = self.list_rounds(session_id, tenant_ctx=tenant_ctx)
    if not rounds:
        return ConsensusResult(agreed=False, summary="No rounds yet")

    rounds_text = json.dumps([
        {"agent": r.agent_id, "type": r.round_type, "content": r.content[:500]}
        for r in rounds
    ], indent=2)

    from app.providers.base import CompletionRequest, Message
    req = CompletionRequest(
        messages=[Message(role="user", content=
            f"Analyze these collaboration rounds and produce a consensus:\n{rounds_text}\n\n"
            f'Return JSON: {{"consensus":"...", "agreed":true/false, '
            f'"key_points":["..."], "dissenter_id":null}}'
        )],
        model="",
    )
    try:
        resp = await provider.complete(req)
        import re, json as _json
        m = re.search(r'\{[\s\S]*\}', resp.content)
        if m:
            data = _json.loads(m.group())
            return ConsensusResult(
                agreed=data.get("agreed", False),
                summary=data.get("consensus", ""),
                dissenter=data.get("dissenter_id"),
            )
    except Exception as exc:
        logger.warning("consensus_llm_failed", error=str(exc))

    # Fall back to rule-based
    return self.synthesize_consensus(session_id=session_id, tenant_ctx=tenant_ctx)
```

### 11.2 🟡 Agent-to-Agent Task Delegation

New endpoint `POST /collab/sessions/{session_id}/delegate`:
```python
class DelegationRequest(BaseModel):
    from_agent_id: str
    to_agent_id: str
    sub_task: str
    context: dict[str, Any] = {}

@router.post("/sessions/{session_id}/delegate", status_code=202)
async def delegate_task(request: Request, session_id: str,
                         body: DelegationRequest) -> dict[str, Any]:
    tenant = _require_tenant(request)
    goal_svc = request.app.state.goal_service
    goal_text = f"[Delegated from {body.from_agent_id}] {body.sub_task}"
    result = await goal_svc.submit_goal(
        goal=goal_text, priority="normal", dry_run=False,
        tenant_ctx=tenant, agent_id=body.to_agent_id
    )
    return {
        "delegated_goal_id": result["goal_id"],
        "from_agent_id": body.from_agent_id,
        "to_agent_id": body.to_agent_id,
        "session_id": session_id,
    }
```

---

## Phase 12 — Agent Marketplace / Blueprint System

### 12.1 🔴 `deploy()` Must Create a Real Agent

**Current state:** `app/enterprise/marketplace.py:175–183` — creates fake UUID, never calls `AgentStore`.

**Fix:**
```python
def deploy(self, *, template_id: str, params: dict[str, Any],
           tenant_ctx: "TenantContext") -> DeployedTemplate:
    template = self.get_template(template_id=template_id)
    if template is None:
        raise ValueError(f"Template '{template_id}' not found")

    agent_config = {
        "name": params.get("name", template["name"]),
        "goal_template": template.get("goal_template",
                         f"Execute tasks as described: {template['description']}"),
        "autonomy_mode": template.get("autonomy_mode", "bounded-autonomous"),
        "connector_ids": [],  # tenant binds connectors after deploy
        "description": template["description"],
    }

    agent_id: str
    if self._agent_store is not None:
        agent_id = self._agent_store.create(agent_config, tenant_ctx=tenant_ctx)
    else:
        agent_id = f"agent-{uuid.uuid4().hex[:12]}"

    dep = DeployedTemplate(
        deployment_id=uuid.uuid4().hex,
        template_id=template_id,
        agent_id=agent_id,
        tenant_id=tenant_ctx.tenant_id,
    )
    self._deployments[dep.deployment_id] = dep
    return dep
```

Wire `_agent_store` in `main.py`:
```python
_marketplace = Marketplace(agent_store=_agent_store)
```

---

## Phase 13 — Simulation and Sandbox

### 13.1 🔴 Dry-Run with Real LLM Planner

**Current state:** `SimulationRunner._build_plan()` is a keyword heuristic.

**Implementation:** `app/enterprise/simulation.py` — replace with `MockMCPClient`:
```python
class MockMCPClient:
    """MCP client that returns mock responses instead of calling real APIs."""

    def __init__(self, mock_tools: dict[str, Any]) -> None:
        self._mocks = mock_tools

    async def discover_tools(self, server_id: str, tenant_ctx: Any) -> list:
        return []

    async def discover_all_tools(self, tenant_ctx: Any) -> list:
        return []

    async def call_tool(self, server_id: str, tool_name: str,
                         arguments: dict, tenant_ctx: Any) -> dict[str, Any]:
        key = tool_name
        full_key = f"{server_id}.{tool_name}"
        mock = self._mocks.get(full_key) or self._mocks.get(key)
        if mock is not None:
            content = mock if isinstance(mock, str) else json.dumps(mock)
            return {"content": [{"type": "text", "text": content}], "simulated": True}
        return {"content": [{"type": "text",
                             "text": f"[simulated: no mock for {tool_name}]"}],
                "simulated": True}
```

Update `SimulationRunner.start()` to use real `AgentGraph` if provider available:
```python
def start(self, *, goal: str, mock_tools: dict[str, Any],
          tenant_ctx: "TenantContext", provider: Any = None) -> SimulationRun:
    run = SimulationRun(goal=goal, mock_tools=mock_tools)
    run.status = "running"
    steps = self._build_plan(goal, mock_tools)

    if provider is not None:
        # Real LLM simulation with mocked tool calls
        mock_mcp = MockMCPClient(mock_tools)
        try:
            from app.agent.graph import AgentGraph
            from app.reliability.dedup import DeduplicationCache
            from app.reliability.result_processor import ResultProcessor
            from app.reliability.rollback import RollbackEngine
            from app.intelligence.guardrails import GuardrailChecker
            graph = AgentGraph(
                planner=provider, executor=provider, verifier=provider,
                mcp_client=mock_mcp,
                result_processor=ResultProcessor(),
                dedup_cache=DeduplicationCache(),
                rollback_engine=RollbackEngine(),
                guardrail_checker=GuardrailChecker(),
            )
            events: list[dict[str, Any]] = []
            async def callback(e: dict[str, Any]) -> None:
                events.append(e)
            import asyncio
            _loop = asyncio.new_event_loop()
            _loop.run_until_complete(
                graph.run(goal=goal, tenant_ctx=tenant_ctx, event_callback=callback)
            )
            _loop.close()
            executed_steps = [
                {"step": e.get("step", e.get("type", "")),
                 "tool": e.get("tool_name"),
                 "output": str(e.get("output", ""))[:300]}
                for e in events if e.get("type") in {"step_complete", "tool_call_complete"}
            ]
        except Exception as exc:
            executed_steps = steps
    else:
        executed_steps = steps
    ...
```

---

## Test Infrastructure

### Backend conftest.py additions

File: `tests/conftest.py` — add shared fixtures:
```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.tenancy.context import TenantContext, PlanTier

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture
def app():
    return create_app()

@pytest.fixture
async def signed_up_client(app):
    """Client with a fresh tenant and valid API key."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "Test", "email": "test@test.com"})
        assert r.status_code == 200, r.text
        raw_key = r.json()["raw_key"]
        c.headers["X-API-Key"] = raw_key
        yield c

@pytest.fixture
def free_ctx():
    return TenantContext(tenant_id="free-tenant", plan=PlanTier.FREE, api_key_id="k1")

@pytest.fixture
def enterprise_ctx():
    return TenantContext(tenant_id="ent-tenant", plan=PlanTier.ENTERPRISE, api_key_id="k2")
```

### API Test Pattern for All New Endpoints

Every new endpoint must have these 4 test cases minimum:
```python
# 1. Auth guard
async def test_{endpoint}_requires_auth(app): ...  # 401

# 2. Happy path
async def test_{endpoint}_happy_path(signed_up_client): ...  # 200/201/202

# 3. Input validation
async def test_{endpoint}_rejects_invalid_input(signed_up_client): ...  # 422

# 4. Tenant isolation
async def test_{endpoint}_tenant_isolation(app): ...  # 403/404
```

### Frontend Test Pattern (Vitest)

Every new component must have:
```tsx
// 1. Renders without crash
it("renders without crashing");

// 2. Shows correct loading state
it("shows loading indicator while fetching");

// 3. Renders API data correctly
it("displays data from API response");

// 4. Shows error state
it("shows error message when API fails");

// 5. User action triggers correct API call
it("calls correct endpoint on user action");
```

### Playwright E2E Pattern

Every new user journey must have:
```typescript
test.describe("{Feature} E2E", () => {
  // Setup: mock all APIs
  test.beforeEach(async ({ page }) => { /* mockAuth + route mocks */ });

  // Happy path
  test("complete happy path", async ({ page }) => { /* navigate, act, assert */ });

  // Error state
  test("handles API error gracefully", async ({ page }) => { /* mock 500, assert error UI */ });
});
```
