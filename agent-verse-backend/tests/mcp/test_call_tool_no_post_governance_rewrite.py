"""MCPGOV-01: MCPClient.call_tool runs exactly the arguments it was handed.

Governance (policy rules, grants, the risk gate and a human approval) decides on
the arguments a caller passes to ``call_tool``. Two things inside ``call_tool``
used to change them AFTER that decision:

* the argument resolver alias/fuzzy-mapped keys onto the tool schema, so a
  policy rule on ``arguments.amount`` never saw an ``amount_usd`` the connector
  then received as ``amount``;
* self-healing asked an LLM for new arguments after an "argument error" and
  dispatched them directly — no policy, grant or approval saw them.

Normalisation is now an explicit step a caller runs BEFORE governance
(``prepare_arguments`` / ``prepare_arguments_by_name``), and a healed argument
set is only returned as a suggestion for the caller to re-submit through its
governed path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.client import MCPClient, ToolCallResult, ToolDefinition
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

PAYOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "amount": {"type": "number"},
        "destination": {"type": "string"},
    },
    "required": ["amount", "destination"],
}


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t-mcpgov", plan=PlanTier.ENTERPRISE, api_key_id="k")


def _cfg() -> MCPServerConfig:
    return MCPServerConfig(
        server_id="pay-1",
        name="payments",
        url="http://payments.example.com",
        tool_definitions=[{"name": "create_payout", "parameters": PAYOUT_SCHEMA}],
    )


def _client() -> tuple[MCPClient, MCPRegistry]:
    registry = MCPRegistry(redis=None)
    return MCPClient(registry=registry, timeout=5.0), registry


@pytest.fixture(autouse=True)
def _bypass_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.mcp.client as _mcp_client

    monkeypatch.setattr(_mcp_client, "assert_public_url", lambda *_a, **_kw: None)


async def test_call_tool_dispatches_exactly_the_given_arguments() -> None:
    client, registry = _client()
    dispatched: list[dict[str, Any]] = []

    async def _impl(cfg: Any, server_id: str, tool: str, arguments: dict, ctx: Any) -> Any:
        dispatched.append(dict(arguments))
        return ToolCallResult(tool_name=tool, success=True, output="ok")

    args = {"amount_usd": 5000, "destination": "acct_attacker"}
    with (
        patch.object(registry, "get", AsyncMock(return_value=_cfg())),
        patch.object(client, "_call_tool_impl", side_effect=_impl),
    ):
        await client.call_tool(
            server_id="pay-1", tool_name="create_payout", arguments=args, tenant_ctx=_ctx()
        )

    # No "amount" materialises after the caller's governance check.
    assert dispatched == [{"amount_usd": 5000, "destination": "acct_attacker"}]


async def test_self_heal_never_dispatches_healed_arguments() -> None:
    client, registry = _client()
    dispatched: list[dict[str, Any]] = []

    async def _impl(cfg: Any, server_id: str, tool: str, arguments: dict, ctx: Any) -> Any:
        dispatched.append(dict(arguments))
        return ToolCallResult(
            tool_name=tool, success=False, error="missing required parameter: amount"
        )

    healer = MagicMock()
    healer.is_argument_error = MagicMock(return_value=True)
    healer.heal = AsyncMock(return_value={"amount": 99999, "destination": "acct_attacker"})

    with (
        patch.object(registry, "get", AsyncMock(return_value=_cfg())),
        patch.object(client, "_call_tool_impl", side_effect=_impl),
        patch("app.mcp.tool_intelligence.get_healer", return_value=healer),
    ):
        result = await client.call_tool(
            server_id="pay-1",
            tool_name="create_payout",
            arguments={"destination": "acct_ok"},
            tenant_ctx=_ctx(),
        )

    assert dispatched == [{"destination": "acct_ok"}]  # the governed call only
    assert result.success is False
    # The healed set comes back as a suggestion for a governed re-submission.
    assert result.suggested_arguments == {"amount": 99999, "destination": "acct_attacker"}
    assert "missing required parameter" in result.error
    assert "suggested" in result.error.lower()


async def test_prepare_arguments_normalises_to_the_tool_schema() -> None:
    client, registry = _client()
    with patch.object(registry, "get", AsyncMock(return_value=_cfg())):
        prepared = await client.prepare_arguments(
            server_id="pay-1",
            tool_name="create_payout",
            arguments={"amount_usd": 5000, "destination": "acct_x"},
            tenant_ctx=_ctx(),
        )

    assert prepared.arguments == {"amount": 5000, "destination": "acct_x"}
    assert prepared.errors == []


async def test_prepare_arguments_reports_unknown_and_missing_keys() -> None:
    client, registry = _client()
    with patch.object(registry, "get", AsyncMock(return_value=_cfg())):
        prepared = await client.prepare_arguments(
            server_id="pay-1",
            tool_name="create_payout",
            arguments={"memo_x": "hi"},
            tenant_ctx=_ctx(),
        )

    joined = " ".join(prepared.errors)
    assert "memo_x" in joined
    assert "amount" in joined


async def test_prepare_arguments_by_name_resolves_the_connector_first() -> None:
    client, registry = _client()
    cfg = _cfg()
    tools = [ToolDefinition(name="create_payout", description="", input_schema=PAYOUT_SCHEMA)]
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(registry, "list_server_records", AsyncMock(return_value=[("pay-1", cfg)])),
        patch.object(client, "discover_tools", AsyncMock(return_value=tools)),
    ):
        prepared = await client.prepare_arguments_by_name(
            tool_name="create_payout",
            arguments={"amount_usd": 10, "destination": "acct_x"},
            tenant_ctx=_ctx(),
        )

    assert prepared.arguments == {"amount": 10, "destination": "acct_x"}
    assert prepared.errors == []
