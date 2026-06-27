"""Unit tests for GoalService."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.state import GoalStatus
from app.agent.tool_context import ToolContext
from app.api.agents import AgentStore
from app.core.errors import NotFoundError
from app.services import goal_service as goal_service_module
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX_A = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="kid-a")
_CTX_B = TenantContext(tenant_id="tid-b", plan=PlanTier.FREE, api_key_id="kid-b")


@pytest.fixture
def svc() -> GoalService:
    return GoalService()


# ── submit_goal ───────────────────────────────────────────────────────────────


class FakeGoalQueue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def enqueue_goal(self, **kwargs: Any) -> str:
        self.enqueued.append(kwargs)
        return "task-1"


class FakeEventStore:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.appended: list[tuple[str, str, dict[str, Any]]] = []
        self.events = events or []

    async def append_event(
        self, goal_id: str, event: dict[str, Any], *, tenant_ctx: TenantContext
    ) -> None:
        self.appended.append((tenant_ctx.tenant_id, goal_id, dict(event)))

    async def list_events(
        self, goal_id: str, *, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        return list(self.events)


async def test_submit_goal_returns_goal_id(svc: GoalService) -> None:
    result = await svc.submit_goal(
        goal="Analyse the codebase for security issues",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )
    assert "goal_id" in result
    assert isinstance(result["goal_id"], str)
    assert len(result["goal_id"]) > 0
    assert result["goal"] == "Analyse the codebase for security issues"
    assert result["priority"] == "normal"
    assert result["dry_run"] is True
    assert "created_at" in result


async def test_submit_goal_dry_run(svc: GoalService) -> None:
    """Dry-run goals are marked complete and emit preview lifecycle events."""
    result = await svc.submit_goal(
        goal="Dry run test goal",
        priority="low",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )
    assert result["dry_run"] is True
    # Dry-run completes immediately without spawning the agent loop
    assert result["status"] == GoalStatus.COMPLETE.value

    # Confirm via get_goal as well
    fetched = await svc.get_goal(goal_id=result["goal_id"], tenant_ctx=_CTX_A)
    assert fetched["status"] == GoalStatus.COMPLETE.value
    assert fetched["event_count"] == 3

    events = await svc.get_events(goal_id=result["goal_id"], tenant_ctx=_CTX_A)
    assert [event["type"] for event in events] == [
        "goal_started",
        "dry_run_preview",
        "goal_complete",
    ]
    assert events[1]["message"] == "Dry run completed without executing tools or writing changes."


async def test_submit_goal_enqueues_worker_without_local_task() -> None:
    queue = FakeGoalQueue()
    svc = GoalService(task_queue=queue)

    result = await svc.submit_goal(
        goal="Run deployment verification",
        priority="high",
        dry_run=False,
        tenant_ctx=_CTX_A,
        agent_id="agent-123",
        workflow_mode="multi_agent",
    )

    record = svc._goals[result["goal_id"]]
    assert record.task is None
    assert queue.enqueued == [
        {
            "goal_id": result["goal_id"],
            "tenant_id": _CTX_A.tenant_id,
            "goal_text": "Run deployment verification",
            "priority": "high",
            "dry_run": False,
            "agent_id": "agent-123",
            "workflow_mode": "multi_agent",
            "goal_template": "",
        }
    ]


async def test_submit_goal_with_queue_awaits_db_persist_before_enqueue() -> None:
    order: list[str] = []

    class OrderedQueue(FakeGoalQueue):
        def enqueue_goal(self, **kwargs: Any) -> str:
            order.append("enqueue")
            return super().enqueue_goal(**kwargs)

    class OrderedPersistGoalService(GoalService):
        async def _db_persist_goal(self, *args: Any, **kwargs: Any) -> None:
            order.append("persist")

    queue = OrderedQueue()
    svc = OrderedPersistGoalService(db_session_factory=object(), task_queue=queue)

    await svc.submit_goal(
        goal="Run durable queued goal",
        priority="high",
        dry_run=False,
        tenant_ctx=_CTX_A,
    )

    assert order == ["persist", "enqueue"]


async def test_submit_goal_with_queue_does_not_enqueue_when_db_persist_fails() -> None:
    class FailingPersistGoalService(GoalService):
        async def _db_persist_goal(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("db unavailable")

    queue = FakeGoalQueue()
    svc = FailingPersistGoalService(db_session_factory=object(), task_queue=queue)

    with pytest.raises(RuntimeError, match="db unavailable"):
        await svc.submit_goal(
            goal="Do not enqueue without durable row",
            priority="high",
            dry_run=False,
            tenant_ctx=_CTX_A,
        )

    assert queue.enqueued == []


async def test_submit_goal_dry_run_does_not_enqueue_worker() -> None:
    queue = FakeGoalQueue()
    svc = GoalService(task_queue=queue)

    result = await svc.submit_goal(
        goal="Preview only",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )

    assert result["status"] == GoalStatus.COMPLETE.value
    assert queue.enqueued == []


async def test_submit_goal_dry_run_appends_events_to_configured_event_store() -> None:
    event_store = FakeEventStore()
    svc = GoalService(event_store=event_store)

    result = await svc.submit_goal(
        goal="Preview persisted events",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )

    assert [event[2]["type"] for event in event_store.appended] == [
        "goal_started",
        "dry_run_preview",
        "goal_complete",
    ]
    assert {event[0] for event in event_store.appended} == {_CTX_A.tenant_id}
    assert {event[1] for event in event_store.appended} == {result["goal_id"]}


async def test_get_events_returns_persisted_events_when_memory_events_empty() -> None:
    persisted = [{"type": "goal_started"}, {"type": "goal_complete"}]
    svc = GoalService(event_store=FakeEventStore(events=persisted))
    svc._goals["goal-loaded"] = GoalRecord(
        goal_id="goal-loaded",
        goal_text="Loaded goal",
        status=GoalStatus.COMPLETE,
        tenant_id=_CTX_A.tenant_id,
        priority="normal",
        dry_run=True,
        created_at="2026-06-25T00:00:00+00:00",
    )

    assert await svc.get_events("goal-loaded", tenant_ctx=_CTX_A) == persisted


async def test_get_goal_falls_back_to_db_when_memory_missing() -> None:
    persisted_goal = SimpleNamespace(
        id="goal-db-only",
        tenant_id=_CTX_A.tenant_id,
        goal_text="Durable goal",
        status=GoalStatus.COMPLETE.value,
        priority="high",
        dry_run=False,
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
        agent_id="agent-123",
        workflow_mode="multi_agent",
        execution_context={},
    )

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return persisted_goal

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def begin(self) -> Any:
            class _Begin:
                async def __aenter__(self) -> None:
                    pass

                async def __aexit__(self, *args: object) -> None:
                    pass

            return _Begin()

        async def execute(
            self, statement: object, params: dict[str, str] | None = None
        ) -> _Result:
            if params is not None:
                return _Result()
            return _Result()

    svc = GoalService(db_session_factory=lambda: _Session())

    fetched = await svc.get_goal("goal-db-only", tenant_ctx=_CTX_A)

    assert fetched["goal_id"] == "goal-db-only"
    assert fetched["status"] == GoalStatus.COMPLETE.value
    assert fetched["goal"] == "Durable goal"
    assert fetched["agent_id"] == "agent-123"
    assert fetched["workflow_mode"] == "multi_agent"


async def test_get_goal_prefers_db_status_for_stale_queued_memory_record() -> None:
    persisted_record = GoalRecord(
        goal_id="goal-stale",
        goal_text="Durable queued goal",
        status=GoalStatus.COMPLETE,
        tenant_id=_CTX_A.tenant_id,
        priority="high",
        dry_run=False,
        created_at="2026-06-25T00:00:00+00:00",
        agent_id="agent-123",
        workflow_mode="multi_agent",
    )

    class RefreshingGoalService(GoalService):
        async def _db_get_goal_record(
            self, goal_id: str, tenant_ctx: TenantContext
        ) -> GoalRecord | None:
            assert goal_id == "goal-stale"
            assert tenant_ctx == _CTX_A
            return persisted_record

    svc = RefreshingGoalService(db_session_factory=object(), task_queue=FakeGoalQueue())
    svc._goals["goal-stale"] = GoalRecord(
        goal_id="goal-stale",
        goal_text="Durable queued goal",
        status=GoalStatus.PLANNING,
        tenant_id=_CTX_A.tenant_id,
        priority="high",
        dry_run=False,
        created_at="2026-06-25T00:00:00+00:00",
        agent_id="agent-123",
        workflow_mode="multi_agent",
    )

    fetched = await svc.get_goal("goal-stale", tenant_ctx=_CTX_A)

    assert fetched["status"] == GoalStatus.COMPLETE.value


async def test_subscribe_events_replays_persisted_events_for_terminal_loaded_goal() -> None:
    persisted = [{"type": "goal_started"}, {"type": "goal_complete"}]
    svc = GoalService(event_store=FakeEventStore(events=persisted))
    svc._goals["goal-loaded"] = GoalRecord(
        goal_id="goal-loaded",
        goal_text="Loaded goal",
        status=GoalStatus.COMPLETE,
        tenant_id=_CTX_A.tenant_id,
        priority="normal",
        dry_run=True,
        created_at="2026-06-25T00:00:00+00:00",
    )

    received = [event async for event in svc.subscribe_events("goal-loaded", tenant_ctx=_CTX_A)]

    assert received == persisted


async def test_subscribe_events_returns_when_stale_memory_has_persisted_terminal_event() -> None:
    persisted = [{"type": "goal_started"}, {"type": "goal_complete"}]
    svc = GoalService(event_store=FakeEventStore(events=persisted))
    svc._goals["goal-stale"] = GoalRecord(
        goal_id="goal-stale",
        goal_text="Queued goal",
        status=GoalStatus.PLANNING,
        tenant_id=_CTX_A.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-06-25T00:00:00+00:00",
    )

    async with asyncio.timeout(0.1):
        received = [
            event async for event in svc.subscribe_events("goal-stale", tenant_ctx=_CTX_A)
        ]

    assert received == persisted


async def test_sync_from_db_loads_goals_under_tenant_rls_context() -> None:
    persisted_tenant = SimpleNamespace(id=_CTX_A.tenant_id, is_active=True)
    persisted_goal = SimpleNamespace(
        id="goal-loaded",
        tenant_id=_CTX_A.tenant_id,
        goal_text="Loaded under RLS",
        status=GoalStatus.COMPLETE.value,
        priority="normal",
        dry_run=True,
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
        agent_id=None,
        workflow_mode="single_agent",
        execution_context={},
    )

    class _ScalarResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def all(self) -> list[Any]:
            return self._rows

    class _Result:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> _ScalarResult:
            return _ScalarResult(self._rows)

    class _Session:
        current_tenant: str | None = None
        goal_queries_without_tenant = 0

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def execute(
            self, statement: object, params: dict[str, str] | None = None
        ) -> _Result:
            sql = str(statement)
            if "set_config('app.tenant_id'" in sql:
                self.current_tenant = params["tid"] if params else None
                return _Result([])
            if "FROM tenants" in sql:
                return _Result([persisted_tenant])
            if "FROM goals" in sql:
                if self.current_tenant == _CTX_A.tenant_id:
                    return _Result([persisted_goal])
                self.goal_queries_without_tenant += 1
            return _Result([])

    session = _Session()

    def fake_db_factory() -> _Session:
        return session

    persisted_events = [{"type": "goal_started"}, {"type": "goal_complete"}]
    svc = GoalService(
        db_session_factory=fake_db_factory,
        event_store=FakeEventStore(events=persisted_events),
    )

    loaded = await svc.sync_from_db()

    assert loaded == 1
    assert session.goal_queries_without_tenant == 0
    assert await svc.get_events("goal-loaded", tenant_ctx=_CTX_A) == persisted_events
    replayed = [event async for event in svc.subscribe_events("goal-loaded", tenant_ctx=_CTX_A)]
    assert replayed == persisted_events


async def test_submit_get_and_list_preserve_agent_binding(svc: GoalService) -> None:
    created = await svc.submit_goal(
        goal="Coordinate release verification",
        priority="high",
        dry_run=True,
        tenant_ctx=_CTX_A,
        agent_id="agent-123",
        workflow_mode="multi_agent",
    )

    assert created["agent_id"] == "agent-123"
    assert created["workflow_mode"] == "multi_agent"

    fetched = await svc.get_goal(goal_id=created["goal_id"], tenant_ctx=_CTX_A)
    assert fetched["agent_id"] == "agent-123"
    assert fetched["workflow_mode"] == "multi_agent"

    listed = await svc.list_goals(tenant_ctx=_CTX_A)
    assert listed["goals"][0]["agent_id"] == "agent-123"
    assert listed["goals"][0]["workflow_mode"] == "multi_agent"


async def test_submit_goal_accepts_valid_agent_id_from_configured_store() -> None:
    class FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, str] | None:
            if agent_id == "agent-123" and tenant_ctx == _CTX_A:
                return {"agent_id": agent_id}
            return None

    svc = GoalService(app_state=SimpleNamespace(agent_store=FakeAgentStore()))

    created = await svc.submit_goal(
        goal="Coordinate release verification",
        priority="high",
        dry_run=True,
        tenant_ctx=_CTX_A,
        agent_id="agent-123",
    )

    assert created["agent_id"] == "agent-123"


async def test_submit_goal_accepts_agent_loaded_from_db_sync() -> None:
    persisted_agent = SimpleNamespace(
        id="agent-db-goal",
        tenant_id=_CTX_A.tenant_id,
        name="goal-agent",
        goal_template="Coordinate {task}",
        autonomy_mode="supervised",
        connector_ids=["github"],
        trigger_config={},
        created_at=None,
    )
    persisted_tenant = SimpleNamespace(id=_CTX_A.tenant_id, is_active=True)

    class _Result:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> _Result:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def execute(
            self, statement: object, params: dict[str, str] | None = None
        ) -> _Result:
            query = str(statement)
            if "FROM tenants" in query:
                return _Result([persisted_tenant])
            if "FROM agents" in query:
                return _Result([persisted_agent])
            return _Result([])

    def fake_db_factory() -> _Session:
        return _Session()

    store = AgentStore(db_session_factory=fake_db_factory)
    loaded = await store.sync_from_db()
    svc = GoalService(app_state=SimpleNamespace(agent_store=store))

    created = await svc.submit_goal(
        goal="Coordinate release verification",
        priority="high",
        dry_run=True,
        tenant_ctx=_CTX_A,
        agent_id="agent-db-goal",
    )

    assert loaded == 1
    assert created["agent_id"] == "agent-db-goal"


async def test_submit_goal_rejects_invalid_agent_id_from_configured_store() -> None:
    class FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> None:
            return None

    svc = GoalService(app_state=SimpleNamespace(agent_store=FakeAgentStore()))

    with pytest.raises(NotFoundError):
        await svc.submit_goal(
            goal="Coordinate release verification",
            priority="high",
            dry_run=True,
            tenant_ctx=_CTX_A,
            agent_id="missing-agent",
        )


async def test_build_tool_context_discovers_agent_connector_tools() -> None:
    agent = {
        "id": "agent-123",
        "name": "Release Agent",
        "connector_ids": ["github", "slack"],
    }

    class FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
            if agent_id == "agent-123" and tenant_ctx == _CTX_A:
                return agent
            return None

    class FakeMCPClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, TenantContext]] = []

        async def discover_tools(
            self, *, server_id: str, tenant_ctx: TenantContext
        ) -> list[SimpleNamespace]:
            self.calls.append((server_id, tenant_ctx))
            return [
                SimpleNamespace(
                    server_id=server_id,
                    server_name=server_id.title(),
                    name=f"{server_id}_tool",
                    description=f"Tool from {server_id}",
                    input_schema={"type": "object"},
                )
            ]

    mcp_client = FakeMCPClient()
    svc = GoalService(
        app_state=SimpleNamespace(agent_store=FakeAgentStore(), mcp_client=mcp_client)
    )

    context = await svc._build_tool_context(agent_id="agent-123", tenant_ctx=_CTX_A)

    assert isinstance(context, ToolContext)
    assert context.connectors == [agent]
    non_rpa = [(tool.server_id, tool.name) for tool in context.tools if tool.server_id != "rpa"]
    assert non_rpa == [
        ("github", "github_tool"),
        ("slack", "slack_tool"),
    ]
    assert mcp_client.calls == [("github", _CTX_A), ("slack", _CTX_A)]


async def test_build_tool_context_isolates_discovery_failures() -> None:
    agent = {
        "id": "agent-123",
        "name": "Release Agent",
        "connector_ids": ["github", "broken", "slack"],
    }

    class FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
            if agent_id == "agent-123" and tenant_ctx == _CTX_A:
                return agent
            return None

    class FakeMCPClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def discover_tools(
            self, *, server_id: str, tenant_ctx: TenantContext
        ) -> list[SimpleNamespace]:
            self.calls.append(server_id)
            if server_id == "broken":
                raise RuntimeError("connector offline")
            return [
                SimpleNamespace(
                    server_id=server_id,
                    server_name=server_id.title(),
                    name=f"{server_id}_tool",
                    description=f"Tool from {server_id}",
                    input_schema={},
                )
            ]

    svc = GoalService(
        app_state=SimpleNamespace(agent_store=FakeAgentStore(), mcp_client=FakeMCPClient())
    )

    context = await svc._build_tool_context(agent_id="agent-123", tenant_ctx=_CTX_A)

    non_rpa_names = [tool.name for tool in context.tools if tool.server_id != "rpa"]
    assert non_rpa_names == ["github_tool", "slack_tool"]
    assert context.connectors == [
        {
            **agent,
            "connector_errors": [
                {"connector_id": "broken", "error": "connector offline"}
            ],
        }
    ]


async def test_build_tool_context_pins_tool_ref_to_requested_connector_id() -> None:
    agent = {"id": "agent-123", "name": "Release Agent", "connector_ids": ["github"]}

    class FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
            if agent_id == "agent-123" and tenant_ctx == _CTX_A:
                return agent
            return None

    class FakeMCPClient:
        async def discover_tools(
            self, *, server_id: str, tenant_ctx: TenantContext
        ) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    server_id="spoofed-server",
                    server_name="Spoofed Server",
                    name="search",
                    description="Search",
                    input_schema={},
                )
            ]

    svc = GoalService(
        app_state=SimpleNamespace(agent_store=FakeAgentStore(), mcp_client=FakeMCPClient())
    )

    context = await svc._build_tool_context(agent_id="agent-123", tenant_ctx=_CTX_A)

    non_rpa = [(tool.server_id, tool.server_name, tool.name) for tool in context.tools if tool.server_id != "rpa"]
    assert non_rpa == [
        ("github", "Spoofed Server", "search")
    ]


async def test_submit_goal_continues_when_tool_discovery_fails() -> None:
    agent = {"id": "agent-123", "name": "Release Agent", "connector_ids": ["broken"]}
    captured: dict[str, Any] = {}

    class FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
            if agent_id == "agent-123" and tenant_ctx == _CTX_A:
                return agent
            return None

    class FakeMCPClient:
        async def discover_tools(
            self, *, server_id: str, tenant_ctx: TenantContext
        ) -> list[SimpleNamespace]:
            raise RuntimeError("connector offline")

    class CapturingGoalService(GoalService):
        async def _run_agent_loop(
            self,
            goal_id: str,
            goal_text: str,
            tenant_ctx: TenantContext,
            tool_context: ToolContext | None = None,
        ) -> None:
            captured["goal_id"] = goal_id
            captured["tool_context"] = tool_context

    svc = CapturingGoalService(
        app_state=SimpleNamespace(agent_store=FakeAgentStore(), mcp_client=FakeMCPClient())
    )

    result = await svc.submit_goal(
        goal="Use connector tools",
        priority="normal",
        dry_run=False,
        tenant_ctx=_CTX_A,
        agent_id="agent-123",
    )
    await asyncio.sleep(0)

    assert result["agent_id"] == "agent-123"
    assert captured["goal_id"] == result["goal_id"]
    assert captured["tool_context"].connectors == [
        {
            **agent,
            "connector_errors": [
                {"connector_id": "broken", "error": "connector offline"}
            ],
        }
    ]


async def test_submit_goal_multi_agent_emits_workflow_events() -> None:
    svc = GoalService()

    result = await svc.submit_goal(
        goal="Fetch Jira issues, write a Confluence summary, and email the team",
        priority="normal",
        dry_run=False,
        tenant_ctx=_CTX_A,
        workflow_mode="multi_agent",
    )
    record = svc._goals[result["goal_id"]]
    assert record.task is not None
    await record.task

    events = await svc.get_events(result["goal_id"], tenant_ctx=_CTX_A)
    event_types = [event["type"] for event in events]

    assert event_types == [
        "goal_started",
        "workflow_planned",
        "workflow_step_started",
        "workflow_step_complete",
        "workflow_step_started",
        "workflow_step_complete",
        "workflow_step_started",
        "workflow_step_complete",
        "goal_complete",
    ]
    assert events[1]["steps"] == [
        {
            "step_id": "step_1",
            "connector_name": "jira",
            "agent_id": None,
            "intent": "fetch_open_issues",
            "input_from": [],
            "requires_approval": False,
        },
        {
            "step_id": "step_2",
            "connector_name": "confluence",
            "agent_id": None,
            "intent": "create_summary_page",
            "input_from": ["step_1"],
            "requires_approval": False,
        },
        {
            "step_id": "step_3",
            "connector_name": "email",
            "agent_id": None,
            "intent": "send_summary_email",
            "input_from": ["step_1", "step_2"],
            "requires_approval": False,
        },
    ]
    assert events[3]["output"]["status"] == "planned_not_executed"


async def test_dispatch_event_redacts_workflow_error_before_append_and_persist() -> None:
    event_store = FakeEventStore()
    svc = GoalService(event_store=event_store)
    goal_id = "goal-redaction"
    svc._goals[goal_id] = GoalRecord(
        goal_id=goal_id,
        goal_text="Run workflow",
        status=GoalStatus.EXECUTING,
        tenant_id=_CTX_A.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        workflow_mode="multi_agent",
    )

    await svc._dispatch_event(
        goal_id,
        {"type": "goal_failed", "reason": "workflow failed api_key=secret"},
        tenant_ctx=_CTX_A,
    )

    assert svc._goals[goal_id].events[-1]["reason"] == (
        "workflow failed api_key=[REDACTED]"
    )
    assert event_store.appended[-1][2]["reason"] == "workflow failed api_key=[REDACTED]"


async def test_submit_goal_multi_agent_calls_matching_connector_tool() -> None:
    agent = {"id": "agent-123", "name": "Jira Agent", "connector_ids": ["jira-server"]}

    class FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
            if agent_id == "agent-123" and tenant_ctx == _CTX_A:
                return agent
            return None

    class FakeMCPClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def discover_tools(
            self, *, server_id: str, tenant_ctx: TenantContext
        ) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    server_id=server_id,
                    server_name="Jira MCP",
                    name="jira_search",
                    description="Search Jira issues",
                    input_schema={"type": "object"},
                )
            ]

        async def call_tool(
            self,
            *,
            server_id: str,
            tool_name: str,
            arguments: dict[str, Any],
            tenant_ctx: TenantContext,
        ) -> SimpleNamespace:
            self.calls.append(
                {
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "tenant_ctx": tenant_ctx,
                }
            )
            return SimpleNamespace(
                tool_name=tool_name,
                success=True,
                output={"issues": ["BAU-1"]},
                error="",
                server_id=server_id,
            )

    mcp_client = FakeMCPClient()
    svc = GoalService(
        app_state=SimpleNamespace(agent_store=FakeAgentStore(), mcp_client=mcp_client)
    )

    result = await svc.submit_goal(
        goal="Fetch open Jira issues",
        priority="normal",
        dry_run=False,
        tenant_ctx=_CTX_A,
        agent_id="agent-123",
        workflow_mode="multi_agent",
    )
    record = svc._goals[result["goal_id"]]
    assert record.task is not None
    await record.task

    events = await svc.get_events(result["goal_id"], tenant_ctx=_CTX_A)

    assert mcp_client.calls == [
        {
            "server_id": "jira-server",
            "tool_name": "jira_search",
            "arguments": {"jql": "statusCategory != Done ORDER BY updated DESC"},
            "tenant_ctx": _CTX_A,
        }
    ]
    assert events[3]["output"] == {
        "status": "executed",
        "tool": "jira_search",
        "server_id": "jira-server",
        "success": True,
        "output": {"issues": ["BAU-1"]},
    }


async def test_run_agent_loop_seeds_tool_context_without_mcp_client() -> None:
    captured: dict[str, Any] = {}

    class FakeLoop:
        async def run(
            self,
            *,
            goal: str,
            tenant_ctx: TenantContext,
            initial_context: dict[str, Any] | None = None,
            event_callback: Any = None,
        ) -> None:
            captured["goal"] = goal
            captured["tenant_ctx"] = tenant_ctx
            captured["initial_context"] = initial_context

    class CapturingGoalService(GoalService):
        def _make_agent_loop_for_tenant(
            self, tenant_ctx: TenantContext, app_state: Any, *, agent_id: str | None = None
        ) -> FakeLoop:
            return FakeLoop()

    mcp_client = object()
    svc = CapturingGoalService(app_state=SimpleNamespace(mcp_client=mcp_client))
    tool_context = ToolContext(connectors=[], tools=[])

    await svc._run_agent_loop(
        "goal-123",
        "Use available tools",
        _CTX_A,
        tool_context=tool_context,
    )

    assert captured["goal"] == "Use available tools"
    assert captured["tenant_ctx"] == _CTX_A
    assert captured["initial_context"] == {
        "tool_prompt": "No connector tools available.",
        "tool_context": tool_context,
    }


async def test_submit_goal_dry_run_records_goal_duration_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    ticks = iter([100.0, 102.5])
    monkeypatch.setattr(goal_service_module, "_monotonic", lambda: next(ticks))

    def _record_goal_duration(
        status: str, duration_seconds: float, priority: str = "normal"
    ) -> None:
        calls.append(
            {
                "status": status,
                "duration_seconds": duration_seconds,
                "priority": priority,
            }
        )

    monkeypatch.setattr(goal_service_module, "record_goal_duration", _record_goal_duration)
    svc = GoalService()

    await svc.submit_goal(
        goal="Validate metric recording",
        priority="high",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )

    assert calls == [
        {"status": "completed", "duration_seconds": 2.5, "priority": "high"}
    ]


def test_make_agent_loop_for_tenant_passes_mcp_client_from_app_state() -> None:
    mcp_client = object()
    svc = GoalService(app_state=SimpleNamespace(mcp_client=mcp_client))

    loop = svc._make_agent_loop_for_tenant(_CTX_A, svc._app_state)

    assert loop._mcp_client is mcp_client


# ── get_goal ──────────────────────────────────────────────────────────────────


async def test_get_goal_not_found_raises(svc: GoalService) -> None:
    with pytest.raises(NotFoundError):
        await svc.get_goal(goal_id="ghost-id", tenant_ctx=_CTX_A)


async def test_get_goal_returns_status(svc: GoalService) -> None:
    created = await svc.submit_goal(
        goal="Check deployment health",
        priority="high",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )
    fetched = await svc.get_goal(goal_id=created["goal_id"], tenant_ctx=_CTX_A)
    assert fetched["goal_id"] == created["goal_id"]
    assert fetched["goal"] == "Check deployment health"
    assert fetched["priority"] == "high"
    assert "event_count" in fetched


async def test_list_goals_returns_only_tenant_goals(svc: GoalService) -> None:
    first = await svc.submit_goal(
        goal="Tenant A goal",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )
    await svc.submit_goal(
        goal="Tenant B goal",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_B,
    )

    listed = await svc.list_goals(tenant_ctx=_CTX_A)

    assert listed == {
        "goals": [
            {
                "id": first["goal_id"],
                "goal_id": first["goal_id"],
                "status": "complete",
                "goal": "Tenant A goal",
                "priority": "normal",
                "dry_run": True,
                "agent_id": None,
                "workflow_mode": "single_agent",
                "created_at": first["created_at"],
                "event_count": 3,
            }
        ]
    }


# ── cancel_goal ───────────────────────────────────────────────────────────────


async def test_cancel_goal(svc: GoalService) -> None:
    created = await svc.submit_goal(
        goal="Long running analysis",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )
    result = await svc.cancel_goal(goal_id=created["goal_id"], tenant_ctx=_CTX_A)
    assert result["status"] == GoalStatus.CANCELLED.value
    assert result["goal_id"] == created["goal_id"]

    # Subsequent get_goal should reflect cancelled status
    fetched = await svc.get_goal(goal_id=created["goal_id"], tenant_ctx=_CTX_A)
    assert fetched["status"] == GoalStatus.CANCELLED.value


async def test_cancel_nonexistent_goal_raises(svc: GoalService) -> None:
    with pytest.raises(NotFoundError):
        await svc.cancel_goal(goal_id="no-such-goal", tenant_ctx=_CTX_A)


# ── get_events ────────────────────────────────────────────────────────────────


async def test_get_events_returns_list(svc: GoalService) -> None:
    created = await svc.submit_goal(
        goal="Scan dependencies",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )
    events = await svc.get_events(goal_id=created["goal_id"], tenant_ctx=_CTX_A)
    assert isinstance(events, list)
    assert len(events) > 0


# ── tenant isolation ──────────────────────────────────────────────────────────


async def test_tenant_isolation(svc: GoalService) -> None:
    """Tenant A's goals are invisible to tenant B."""
    created = await svc.submit_goal(
        goal="Private goal for tenant A",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
    )
    goal_id = created["goal_id"]

    # Tenant B cannot read tenant A's goal
    with pytest.raises(NotFoundError):
        await svc.get_goal(goal_id=goal_id, tenant_ctx=_CTX_B)

    # Tenant B cannot cancel tenant A's goal
    with pytest.raises(NotFoundError):
        await svc.cancel_goal(goal_id=goal_id, tenant_ctx=_CTX_B)

    # Tenant B cannot read events for tenant A's goal
    with pytest.raises(NotFoundError):
        await svc.get_events(goal_id=goal_id, tenant_ctx=_CTX_B)
