"""CORE-11: auto-routing reads candidates from the DB and scores history in one query.

route() scored the process-local AgentStore list (agents created on another
replica were invisible, deleted ones still routable), ran one history query per
agent over an unbounded list, and swallowed LLM scoring errors silently.
"""

from __future__ import annotations

from typing import Any

from app.agent.router import MAX_ROUTING_CANDIDATES, AgentRouter
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-route", plan=PlanTier.PROFESSIONAL, api_key_id="k")

_JIRA_AGENT = {
    "agent_id": "a-jira",
    "name": "Jira triage agent",
    "goal_template": "Triage jira issues and tickets",
    "connector_ids": ["builtin-jira"],
}


class _StaleStore:
    """This replica's cache is empty; the DB has the agent."""

    def __init__(self, agents: list[dict[str, Any]]) -> None:
        self.agents = agents
        self.limits: list[int | None] = []

    def list_all(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return []

    async def list_async(
        self, *, tenant_ctx: TenantContext, limit: int | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        self.limits.append(limit)
        return self.agents[: limit or None]


async def test_agent_created_on_another_replica_is_routable() -> None:
    store = _StaleStore([_JIRA_AGENT])
    decision = await AgentRouter(agent_store=store).route("triage the open jira tickets", T)
    assert decision.agent_id == "a-jira"
    assert store.limits == [MAX_ROUTING_CANDIDATES]  # bounded read


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, log: list[str], rows: list[tuple[Any, ...]]) -> None:
        self.log, self.rows = log, rows

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        text = str(stmt)
        if "set_config" in text:
            return _Result([])
        self.log.append(text)
        return _Result(self.rows)

    async def flush(self) -> None:
        return None


async def test_history_for_all_candidates_is_one_query() -> None:
    log: list[str] = []
    rows = [("a1", 0.9, 4), ("a2", 0.2, 3)]
    agents = [
        {"agent_id": aid, "name": f"agent {aid}", "goal_template": "summarize reports"}
        for aid in ("a1", "a2", "a3")
    ]
    router = AgentRouter(agent_store=_StaleStore(agents), db_session_factory=lambda: _Session(log, rows))

    decision = await router.route("summarize the weekly reports", T, available_agents=agents)

    assert len(log) == 1, log
    by_id = {s.agent_id: s.breakdown["history"] for s in decision.all_scores}
    assert by_id == {"a1": 0.9, "a2": 0.2, "a3": 0.0}


async def test_llm_scoring_failure_is_recorded_on_the_decision() -> None:
    class _Broken:
        _default_model = ""

        async def complete(self, request: Any) -> Any:
            raise RuntimeError("provider down")

    agents = [
        _JIRA_AGENT,
        {"agent_id": "a-2", "name": "Other", "goal_template": "something else"},
    ]
    router = AgentRouter(agent_store=_StaleStore(agents), llm_provider=_Broken())
    decision = await router.route("triage the open jira tickets", T, available_agents=agents)

    assert decision.to_dict()["llm_scoring"].startswith("routing_llm_failed")
