"""OrgMCPServer — exposes the org as an MCP server (Q7 of spec).

Exposes org capabilities as MCP tools:
  ask_organization       — NL question to org brain
  start_mission          — create and start a new mission
  get_status             — org health + active missions
  list_missions          — missions with optional status filter
  get_mission_result     — completed mission result + artifacts
  list_pending_approvals — items awaiting approval
  approve                — approve a pending action
  search_knowledge       — search org knowledge base
  search_memory          — search org memory

MCP endpoint: WS /v1/mcp/{org_id}
Auth: Bearer {api_key}
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog
from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    rate_limit_per_hour: int = 100
    requires_scope: str = "orgs:read"


# ── Tool definitions ───────────────────────────────────────────────────────────

ORG_MCP_TOOLS: list[MCPTool] = [
    MCPTool(
        name="ask_organization",
        description="Ask the AI organization anything in natural language. Returns an answer based on current org state, memory, and knowledge.",  # noqa: E501
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural language question"}
            },
            "required": ["question"],
        },
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
        },
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    MCPTool(
        name="start_mission",
        description="Create and start a new mission for the organization to execute.",
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            },
            "required": ["description"],
        },
        output_schema={
            "type": "object",
            "properties": {"mission_id": {"type": "string"}, "status": {"type": "string"}},
        },
        rate_limit_per_hour=10,
        requires_scope="missions:write",
    ),
    MCPTool(
        name="get_status",
        description="Get current organization status: active missions, agents, pending approvals, health score.",  # noqa: E501
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    MCPTool(
        name="list_missions",
        description="List missions with optional status filter.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "failed", "queued", "all"],
                }
            },
        },
        output_schema={"type": "object", "properties": {"missions": {"type": "array"}}},
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    MCPTool(
        name="get_mission_result",
        description="Get the result and artifacts of a completed mission.",
        input_schema={
            "type": "object",
            "properties": {"mission_id": {"type": "string"}},
            "required": ["mission_id"],
        },
        output_schema={"type": "object"},
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    MCPTool(
        name="list_pending_approvals",
        description="List all items currently waiting for human approval.",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"approvals": {"type": "array"}}},
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    MCPTool(
        name="approve",
        description="Approve a pending action item.",
        input_schema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["approval_id"],
        },
        output_schema={"type": "object", "properties": {"approved": {"type": "boolean"}}},
        rate_limit_per_hour=50,
        requires_scope="approve",
    ),
    MCPTool(
        name="search_knowledge",
        description="Search the org's knowledge base.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        output_schema={"type": "object", "properties": {"results": {"type": "array"}}},
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
    MCPTool(
        name="search_memory",
        description="Search organizational memory and past decisions.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema={"type": "object", "properties": {"results": {"type": "array"}}},
        rate_limit_per_hour=200,
        requires_scope="orgs:read",
    ),
]


class OrgMCPServer:
    """
    Exposes the org as a Model Context Protocol (MCP) server.
    Compatible with: Claude Desktop, Cursor, any MCP client.

    Integration Point 3: Tool handlers are fully wired to OrgService,
    GoalService, KnowledgeStore and LongTermMemoryStore via app.state and
    a per-call session from the DB session factory.

    The actual WebSocket transport is handled by the gateway router.
    """

    def __init__(
        self,
        org_id: str,
        api_key: str | None = None,
        # NEW: inject app.state so tool handlers access live services
        app_state: Any | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self.org_id = org_id
        self._api_key = api_key
        self._tools = {t.name: t for t in ORG_MCP_TOOLS}
        # ── Live service references ───────────────────────────────────────
        # Resolved lazily from app_state so server construction is always fast.
        self._app_state = app_state
        self._tenant_id = tenant_id or "system"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_goal_service(self) -> Any | None:
        """Return the GoalService from app.state (non-blocking)."""
        if self._app_state is not None:
            return getattr(self._app_state, "goal_service", None)
        try:
            from app.main import app as _app  # type: ignore[attr-defined]

            return getattr(getattr(_app, "state", None), "goal_service", None)
        except Exception:
            return None

    def _get_knowledge_store(self) -> Any | None:
        if self._app_state is not None:
            return getattr(self._app_state, "knowledge_store", None)
        try:
            from app.main import app as _app  # type: ignore[attr-defined]

            return getattr(getattr(_app, "state", None), "knowledge_store", None)
        except Exception:
            return None

    def _get_long_term_memory(self) -> Any | None:
        if self._app_state is not None:
            return getattr(self._app_state, "long_term_memory", None)
        try:
            from app.main import app as _app  # type: ignore[attr-defined]

            return getattr(getattr(_app, "state", None), "long_term_memory", None)
        except Exception:
            return None

    async def _get_org_service_ctx(self) -> tuple[Any, Any] | None:
        """Return (OrgService, session) backed by a real DB session.

        Caller **must** use as an async context manager or explicitly close
        the session:  ``async with svc._get_org_service_ctx() as (svc, session):``

        Returns None when no DB session factory is configured (test / dev
        without a database).
        """
        try:
            from app.db.session import get_session_factory
            from app.org.service import OrgService as _OrgService

            session_factory = get_session_factory()
            session = session_factory()
            svc = _OrgService(session=session, tenant_id=self._tenant_id)
            return svc, session
        except Exception as exc:
            _log.debug("mcp_server.no_db_session", error=str(exc)[:80])
            return None

    # ── Public MCP interface ──────────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions in MCP format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an MCP tool call and return the result."""
        with _tracer.start_as_current_span("mcp_server.call_tool") as span:
            span.set_attribute("tool", tool_name)
            span.set_attribute("org_id", self.org_id)

            tool = self._tools.get(tool_name)
            if not tool:
                return {"error": f"Unknown tool: {tool_name}"}

            handler = getattr(self, f"_tool_{tool_name}", None)
            if not handler:
                return {"error": f"Tool {tool_name} not yet implemented"}

            try:
                result = await handler(arguments, context or {})
                return {"content": [{"type": "text", "text": str(result)}]}
            except Exception as exc:
                _log.error("mcp_server.tool_error", tool=tool_name, error=str(exc))
                return {"error": str(exc)}

    # ── Tool handlers — fully wired to live services ──────────────────────────

    async def _tool_ask_organization(self, args: dict, ctx: dict) -> str:
        """Route a natural-language question to the org brain.

        Strategy:
        1. Fetch org health + recent decisions from OrgService.
        2. Search knowledge base for relevant context.
        3. Pass everything to GoalService as a short-horizon goal and return the
           agent's answer.  Falls back to a formatted org summary if the goal
           service is unavailable.
        """
        question = args.get("question", "").strip()
        if not question:
            return "Please provide a question."

        with _tracer.start_as_current_span("mcp.ask_organization") as span:
            span.set_attribute("org_id", self.org_id)
            span.set_attribute("question_len", len(question))

            # 1. Fetch org context from DB ─────────────────────────────────────
            org_context_parts: list[str] = []
            ctx_pair = await self._get_org_service_ctx()
            if ctx_pair is not None:
                svc, session = ctx_pair
                try:
                    health = await svc.get_org_health(self.org_id)
                    org_context_parts.append(
                        f"Org Health Score: {health.get('health_score', 'N/A')}\n"
                        f"Active Missions: {health.get('active_missions', 0)}\n"
                        f"Pending Approvals: {health.get('pending_approvals', 0)}\n"
                        f"Total Agents: {health.get('total_agents', 0)}"
                    )
                    decisions = await svc.list_decisions(self.org_id, limit=3)
                    if decisions:
                        dec_text = "\n".join(
                            f"- [{d.decision_type}] {d.title}: {d.rationale[:80]}"
                            for d in decisions
                        )
                        org_context_parts.append(f"Recent Decisions:\n{dec_text}")
                finally:
                    await session.close()

            # 2. Knowledge base search ─────────────────────────────────────────
            ks = self._get_knowledge_store()
            if ks is not None:
                try:
                    kb_results = await ks.search(question, top_k=4)
                    if kb_results:
                        kb_text = "\n".join(
                            f"- {r.get('content', '')[:120]}" for r in kb_results[:4]
                        )
                        org_context_parts.append(f"Relevant Knowledge:\n{kb_text}")
                except Exception:
                    pass

            # 3. Route to GoalService for a real LLM-backed answer ─────────────
            goal_service = self._get_goal_service()
            if goal_service is not None:
                try:
                    from app.tenancy.context import PlanTier, TenantContext

                    tenant_ctx = TenantContext(
                        tenant_id=self._tenant_id,
                        plan=PlanTier.PROFESSIONAL,
                        api_key_id="mcp_ask_org",
                    )
                    org_context_blob = "\n\n".join(org_context_parts)
                    enriched_goal = (
                        f"Answer this question about the organisation (org_id={self.org_id}):\n"
                        f"{question}\n\n"
                        f"Current organisation context:\n{org_context_blob}"
                    )
                    result = await goal_service.submit_goal(
                        goal=enriched_goal,
                        priority="medium",
                        dry_run=False,
                        tenant_ctx=tenant_ctx,
                        workflow_mode="single_agent",
                        execution_context={"org_id": self.org_id, "source": "mcp_ask"},
                    )
                    span.set_attribute("goal_id", result.get("goal_id", ""))
                    return (
                        f"[Mission submitted — goal_id={result.get('goal_id')}]\n"
                        f'The agent is processing your question: "{question}"\n'
                        f"Check mission status or stream events for the answer."
                    )
                except Exception as exc:
                    _log.warning("mcp.ask_org.goal_submit_failed", error=str(exc)[:80])

            # Fallback: return the collected org context as the answer
            if org_context_parts:
                return "Org context summary:\n\n" + "\n\n".join(org_context_parts)
            return (
                f"Organisation {self.org_id} is active. "
                f"Connect to a live backend with DB + LLM provider for full answers."
            )

    async def _tool_start_mission(self, args: dict, ctx: dict) -> dict:
        """Create and immediately dispatch a new org mission."""
        description = args.get("description", "").strip()
        priority = args.get("priority", "medium")
        if not description:
            return {"error": "description is required"}

        with _tracer.start_as_current_span("mcp.start_mission") as span:
            span.set_attribute("org_id", self.org_id)

            ctx_pair = await self._get_org_service_ctx()
            if ctx_pair is None:
                # No DB: submit directly to GoalService as a best-effort
                goal_service = self._get_goal_service()
                if goal_service:
                    from app.tenancy.context import PlanTier, TenantContext

                    tc = TenantContext(
                        tenant_id=self._tenant_id,
                        plan=PlanTier.PROFESSIONAL,
                        api_key_id="mcp_start_mission",
                    )
                    result = await goal_service.submit_goal(
                        goal=description,
                        priority=priority,
                        dry_run=False,
                        tenant_ctx=tc,
                        workflow_mode="single_agent",
                        execution_context={"org_id": self.org_id, "source": "mcp"},
                    )
                    return {
                        "mission_id": result.get("goal_id"),
                        "status": result.get("status", "queued"),
                    }
                return {"error": "no_backend_available", "mission_id": None, "status": "failed"}

            svc, session = ctx_pair
            try:
                from app.tenancy.context import PlanTier, TenantContext

                tc = TenantContext(
                    tenant_id=self._tenant_id,
                    plan=PlanTier.PROFESSIONAL,
                    api_key_id="mcp_start_mission",
                )
                mission, dispatch = await svc.create_mission_and_execute(
                    org_id=self.org_id,
                    title=description[:120],
                    objective=description,
                    priority=priority,
                    source="mcp",
                    tenant_ctx=tc,
                )
                await session.commit()
                span.set_attribute("mission_id", str(mission.id))
                return {
                    "mission_id": str(mission.id),
                    "goal_id": dispatch.get("goal_id"),
                    "status": "active" if dispatch.get("goal_id") else "queued",
                    "topology": dispatch.get("topology", "sequential"),
                    "departments": dispatch.get("departments", []),
                    "estimated_cost_usd": dispatch.get("estimated_cost_usd", 0.0),
                }
            except Exception as exc:
                await session.rollback()
                _log.error("mcp.start_mission.error", error=str(exc)[:150])
                return {"error": str(exc)[:150], "mission_id": None, "status": "failed"}
            finally:
                await session.close()

    async def _tool_get_status(self, args: dict, ctx: dict) -> dict:
        """Return live org health: active missions, agents, pending approvals."""
        with _tracer.start_as_current_span("mcp.get_status") as span:
            span.set_attribute("org_id", self.org_id)

            ctx_pair = await self._get_org_service_ctx()
            if ctx_pair is None:
                return {
                    "org_id": self.org_id,
                    "status": "unknown",
                    "error": "db_unavailable",
                }
            svc, session = ctx_pair
            try:
                health = await svc.get_org_health(self.org_id)
                return health
            finally:
                await session.close()

    async def _tool_list_missions(self, args: dict, ctx: dict) -> dict:
        """List org missions with optional status filter."""
        raw_status = args.get("status", "all")

        with _tracer.start_as_current_span("mcp.list_missions") as span:
            span.set_attribute("org_id", self.org_id)

            ctx_pair = await self._get_org_service_ctx()
            if ctx_pair is None:
                return {"missions": [], "total": 0, "error": "db_unavailable"}

            svc, session = ctx_pair
            try:
                status_filter = None if raw_status == "all" else raw_status
                missions = await svc.list_missions(
                    org_id=self.org_id, status=status_filter, limit=20
                )
                mission_list = [
                    {
                        "id": str(m.id),
                        "title": m.title,
                        "status": m.status,
                        "priority": m.priority,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                        "goal_id": (m.metadata or {}).get("goal_id"),
                    }
                    for m in missions
                ]
                return {"missions": mission_list, "total": len(mission_list)}
            finally:
                await session.close()

    async def _tool_get_mission_result(self, args: dict, ctx: dict) -> dict:
        """Return result and artifacts of a completed mission."""
        mission_id = args.get("mission_id", "")
        if not mission_id:
            return {"error": "mission_id is required"}

        ctx_pair = await self._get_org_service_ctx()
        if ctx_pair is None:
            return {"error": "db_unavailable"}

        svc, session = ctx_pair
        try:
            mission = await svc.get_mission(mission_id)
            if not mission:
                return {"error": f"mission {mission_id} not found"}
            tasks = await svc.list_tasks(mission_id=mission_id)
            return {
                "mission_id": mission_id,
                "title": mission.title,
                "status": mission.status,
                "objective": mission.objective,
                "outcome": mission.expected_outcome,
                "completed_at": mission.completed_at.isoformat() if mission.completed_at else None,
                "tasks_total": len(tasks),
                "tasks_completed": sum(1 for t in tasks if t.status == "completed"),
                "metadata": mission.metadata or {},
            }
        finally:
            await session.close()

    async def _tool_list_pending_approvals(self, args: dict, ctx: dict) -> dict:
        """List all items currently waiting for human approval."""
        with _tracer.start_as_current_span("mcp.list_approvals") as span:
            span.set_attribute("org_id", self.org_id)

            ctx_pair = await self._get_org_service_ctx()
            if ctx_pair is None:
                return {"approvals": [], "total": 0, "error": "db_unavailable"}

            svc, session = ctx_pair
            try:
                events = await svc.list_events(
                    org_id=self.org_id,
                    event_type="approval_required",
                    limit=50,
                )
                approval_list = [
                    {
                        "id": str(e.id),
                        "title": e.title,
                        "entity_type": e.entity_type,
                        "entity_id": e.entity_id,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                        "payload": e.payload or {},
                    }
                    for e in events
                ]
                return {"approvals": approval_list, "total": len(approval_list)}
            finally:
                await session.close()

    async def _tool_approve(self, args: dict, ctx: dict) -> dict:
        """Approve a pending action item via the HITL gateway."""
        approval_id = args.get("approval_id", "")
        comment = args.get("comment", "Approved via MCP")
        if not approval_id:
            return {"error": "approval_id is required"}

        with _tracer.start_as_current_span("mcp.approve") as span:
            span.set_attribute("approval_id", approval_id)

            # Route to HITL gateway if available
            try:
                from app.main import app as _app  # type: ignore[attr-defined]

                hitl = getattr(getattr(_app, "state", None), "hitl_gateway", None)
                if hitl is not None and hasattr(hitl, "approve"):
                    result = await hitl.approve(
                        approval_id=approval_id,
                        approved_by="mcp_client",
                        comment=comment,
                    )
                    return {"approved": True, "result": str(result)}
            except Exception as exc:
                _log.warning("mcp.approve.hitl_failed", error=str(exc)[:80])

            return {"approved": False, "error": "hitl_gateway_unavailable"}

    async def _tool_search_knowledge(self, args: dict, ctx: dict) -> dict:
        """Search the org's knowledge base via the KnowledgeStore."""
        query = args.get("query", "").strip()
        top_k = int(args.get("top_k", 5))
        if not query:
            return {"results": [], "query": ""}

        with _tracer.start_as_current_span("mcp.search_knowledge") as span:
            span.set_attribute("org_id", self.org_id)
            span.set_attribute("query_len", len(query))

            ks = self._get_knowledge_store()
            if ks is not None:
                try:
                    raw_results = await ks.search(query, top_k=top_k)
                    results = [
                        {
                            "content": r.get("content", "")[:400],
                            "source": r.get("source", ""),
                            "score": r.get("score", 0.0),
                        }
                        for r in raw_results
                    ]
                    span.set_attribute("results_count", len(results))
                    return {"results": results, "query": query}
                except Exception as exc:
                    _log.warning("mcp.search_knowledge.error", error=str(exc)[:80])

            # Fallback: dept memory search
            try:
                from app.memory.dept_memory import DepartmentMemory

                dm = DepartmentMemory()
                mem_entries = await dm.retrieve(self.org_id, query, top_k=top_k)
                results = [
                    {"content": e.content[:400], "source": "dept_memory", "score": e.confidence}
                    for e in mem_entries
                ]
                return {"results": results, "query": query}
            except Exception:
                pass

            return {"results": [], "query": query, "error": "knowledge_store_unavailable"}

    async def _tool_search_memory(self, args: dict, ctx: dict) -> dict:
        """Search organisational memory and past decisions."""
        query = args.get("query", "").strip()
        if not query:
            return {"results": [], "query": ""}

        with _tracer.start_as_current_span("mcp.search_memory") as span:
            span.set_attribute("org_id", self.org_id)

            # 1. Long-term memory store
            ltm = self._get_long_term_memory()
            if ltm is not None:
                try:
                    raw = await ltm.search(query, top_k=6)
                    results = [
                        {"content": r.get("content", "")[:300], "source": "long_term_memory"}
                        for r in raw
                    ]
                    span.set_attribute("results_count", len(results))
                    return {"results": results, "query": query}
                except Exception:
                    pass

            # 2. Fallback: recent org decisions from DB
            ctx_pair = await self._get_org_service_ctx()
            if ctx_pair is not None:
                svc, session = ctx_pair
                try:
                    decisions = await svc.list_decisions(self.org_id, limit=10)
                    results = [
                        {
                            "content": f"[{d.decision_type}] {d.title}: {d.rationale}",
                            "source": "org_decisions",
                        }
                        for d in decisions
                        if query.lower() in (d.title + " " + d.rationale).lower()
                    ]
                    return {"results": results[:6], "query": query}
                finally:
                    await session.close()

            return {"results": [], "query": query, "error": "memory_unavailable"}

    def get_claude_desktop_config(self, server_url: str) -> dict[str, Any]:
        """Generate ready-to-use Claude Desktop MCP config snippet."""
        return {
            "mcpServers": {
                f"agentverse-{self.org_id[:8]}": {
                    "command": "npx",
                    "args": ["-y", "@agentverse/mcp-proxy"],
                    "env": {
                        "AGENTVERSE_ORG_URL": server_url,
                        "AGENTVERSE_API_KEY": "<your-api-key>",
                    },
                }
            }
        }
