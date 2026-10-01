"""WF-TOOL-STEP-SERVER: a workflow tool step calls the connector INSTANCE saved
on the step (its server_id), not the first connector exposing the tool name.

The builder saves ``server_id`` on a tool step (two MongoDB connections expose
``mongodb_count``), but the DSL dropped the field and the step dispatched by
name only — on whichever connection was listed first.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.mcp.servers.registry_wiring import get_builtin_server_configs
from app.tenancy.context import PlanTier, TenantContext
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.steps.tool_step import ToolStepNode

CTX = TenantContext(tenant_id="t-wf", plan=PlanTier.PROFESSIONAL, api_key_id="k")
ORDERS = "builtin-mongodb:orders-db"
ANALYTICS = "builtin-mongodb:analytics-db"


class _Mongo:
    dsns: list[str] = []

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        _Mongo.dsns.append(dsn)

    def __getitem__(self, name: str) -> Any:
        class _Coll:
            name = "c"

            def count_documents(self, query: dict[str, Any]) -> int:
                return 3

        class _DB:
            def __getitem__(self, coll: str) -> _Coll:
                return _Coll()

        return _DB()

    def close(self) -> None:
        pass


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> MCPClient:
    import pymongo

    import app.net.ssrf_guard as guard

    _Mongo.dsns = []
    monkeypatch.setattr(pymongo, "MongoClient", _Mongo)
    monkeypatch.setattr(guard, "_resolve_host", lambda host: ["93.184.216.34"])
    reg = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    (spec,) = [c for c in get_builtin_server_configs() if c["server_id"] == "builtin-mongodb"]
    for sid, name, host in (
        (ORDERS, "orders-db", "8.8.8.8"),
        (ANALYTICS, "analytics-db", "8.8.4.4"),
    ):
        await reg.register(
            MCPServerConfig(
                server_id=sid,
                name=name,
                base_url="builtin://",
                auth_config={"url": f"mongodb://u:p@{host}:27017/{name}"},
                builtin_type="builtin-mongodb",
                tool_definitions=spec["tool_definitions"],
            ),
            tenant_ctx=CTX,
        )
    MCPRegistry.register_builtin_handler("builtin-mongodb", spec["handler"])
    return MCPClient(registry=reg)


def _state() -> dict[str, Any]:
    return {
        "run_id": "r1",
        "tenant_id": "t-wf",
        "tenant_ctx": CTX,
        "inputs": {},
        "step_outputs": {},
        "vars": {},
        "is_test_run": False,
        "mock_overrides": {},
    }


def _node(client: MCPClient, **step: Any) -> ToolStepNode:
    definition = StepDefinition.model_validate(
        {"id": "s1", "type": "tool", "tool": "mongodb_count", "input": {"collection": "c"}} | step
    )
    return ToolStepNode(definition, ContextResolver(), mcp_client=client)


@pytest.mark.parametrize("key", ["server_id", "connector_id"])
async def test_step_calls_the_saved_instance(client: MCPClient, key: str) -> None:
    out = await _node(client, **{key: ANALYTICS}).execute(_state())

    assert out["step_outputs"]["s1"]["success"] is True
    assert len(_Mongo.dsns) == 1 and "8.8.4.4" in _Mongo.dsns[0]


async def test_each_instance_is_reachable(client: MCPClient) -> None:
    await _node(client, server_id=ORDERS).execute(_state())
    await _node(client, server_id=ANALYTICS).execute(_state())

    assert ["8.8.8.8" in d for d in _Mongo.dsns] == [True, False]


async def test_ambiguous_bare_tool_without_instance_fails_clearly(client: MCPClient) -> None:
    with pytest.raises(RuntimeError, match="several connectors"):
        await _node(client).execute(_state())
    assert _Mongo.dsns == []


async def test_deleted_saved_instance_fails_instead_of_falling_back(client: MCPClient) -> None:
    await client._registry.unregister(ANALYTICS, tenant_ctx=CTX)

    with pytest.raises(RuntimeError, match="not found"):
        await _node(client, server_id=ANALYTICS).execute(_state())
    assert _Mongo.dsns == []  # never ran on orders-db instead


async def test_saved_instance_must_expose_the_tool(client: MCPClient) -> None:
    with pytest.raises(RuntimeError, match="does not expose"):
        await _node(client, server_id=ANALYTICS, tool="redis_get").execute(_state())
