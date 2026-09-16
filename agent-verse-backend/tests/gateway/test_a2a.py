"""Tests for app/gateway/a2a/__init__.py — OrgAsAgent + OrgA2AClient (A2A protocol).

Covers:
  - AgentResult / AgentEvent / DelegationResult dataclass defaults
  - OrgAsAgent.invoke / invoke_async / stream
  - OrgA2AClient internal (same-deployment) delegation
  - OrgA2AClient external (HTTP) delegation — success + failure
  - OrgA2AClient.close()
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.a2a import (
    AgentEvent,
    AgentResult,
    DelegationResult,
    OrgA2AClient,
    OrgAsAgent,
)

ORG_ID = "org-a2a-test"
TENANT_ID = "tenant-a2a-test"


class TestDataclassDefaults:
    def test_agent_result_defaults(self) -> None:
        r = AgentResult()
        assert r.success is True
        assert r.artifacts == []
        assert r.cost_usd == 0.0
        assert r.error is None

    def test_agent_event_defaults(self) -> None:
        e = AgentEvent(event_type="thinking")
        assert e.text == ""
        assert e.data == {}
        assert e.mission_id is None

    def test_delegation_result_defaults(self) -> None:
        d = DelegationResult(delegated_to_org="org2", task="do x")
        assert d.mission_id is None
        assert d.delegation_cost_usd == 0.0
        assert d.result is None


class TestOrgAsAgentInvoke:
    @pytest.mark.asyncio
    async def test_invoke_returns_success_result(self) -> None:
        agent = OrgAsAgent(org_id=ORG_ID, tenant_id=TENANT_ID)
        result = await agent.invoke("Analyze quarterly numbers")
        assert isinstance(result, AgentResult)
        assert result.success is True
        assert "Analyze quarterly numbers" in result.output
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_invoke_async_returns_mission_id(self) -> None:
        agent = OrgAsAgent(org_id=ORG_ID, tenant_id=TENANT_ID)
        mission_id = await agent.invoke_async("Do the thing")
        assert mission_id.startswith("mission_")

    @pytest.mark.asyncio
    async def test_invoke_async_with_callback_and_context(self) -> None:
        agent = OrgAsAgent(org_id=ORG_ID, tenant_id=TENANT_ID)
        mission_id = await agent.invoke_async(
            "Do the thing", callback_url="https://cb.example.com", context={"k": "v"}
        )
        assert mission_id.startswith("mission_")

    @pytest.mark.asyncio
    async def test_stream_yields_expected_sequence(self) -> None:
        agent = OrgAsAgent(org_id=ORG_ID, tenant_id=TENANT_ID)
        events = [e async for e in agent.stream("Build a report")]
        assert [e.event_type for e in events] == ["thinking", "step", "complete"]
        assert events[-1].data["org_id"] == ORG_ID
        assert "Build a report" in events[-1].text


class TestOrgA2AClientInit:
    @pytest.mark.asyncio
    async def test_base_url_trailing_slash_stripped(self) -> None:
        client = OrgA2AClient(
            calling_org_id=ORG_ID, calling_tenant_id=TENANT_ID, base_url="https://x.com/"
        )
        assert client._base_url == "https://x.com"
        await client.close()

    @pytest.mark.asyncio
    async def test_defaults(self) -> None:
        client = OrgA2AClient(calling_org_id=ORG_ID, calling_tenant_id=TENANT_ID)
        assert client._base_url == ""
        assert client._api_key == ""
        await client.close()


class TestOrgA2AClientInternalDelegation:
    @pytest.mark.asyncio
    async def test_delegate_without_base_url_uses_internal_agent(self) -> None:
        client = OrgA2AClient(calling_org_id=ORG_ID, calling_tenant_id=TENANT_ID)
        result = await client.delegate_to_org(to_org_id="org-2", task="Review contract")
        assert isinstance(result, DelegationResult)
        assert result.delegated_to_org == "org-2"
        assert result.result is not None
        assert result.result.success is True
        await client.close()


class TestOrgA2AClientExternalDelegation:
    @pytest.mark.asyncio
    async def test_delegate_external_success(self) -> None:
        client = OrgA2AClient(
            calling_org_id=ORG_ID,
            calling_tenant_id=TENANT_ID,
            base_url="https://partner.example.com",
            api_key="key-123",
        )
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json = MagicMock(
            return_value={"mission_id": "m-99", "output": "done", "cost_usd": 2.5}
        )
        with patch.object(
            client._http, "post", new=AsyncMock(return_value=fake_response)
        ) as mock_post:
            result = await client.delegate_to_org(
                to_org_id="org-2", task="Audit finances", budget_usd=10.0
            )
        assert result.mission_id == "m-99"
        assert result.result is not None
        assert result.result.cost_usd == 2.5
        mock_post.assert_awaited_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer key-123"
        assert call_kwargs["json"]["budget_usd"] == 10.0
        await client.close()

    @pytest.mark.asyncio
    async def test_delegate_external_http_error_returns_failure(self) -> None:
        client = OrgA2AClient(
            calling_org_id=ORG_ID,
            calling_tenant_id=TENANT_ID,
            base_url="https://partner.example.com",
            api_key="key-123",
        )
        with patch.object(
            client._http, "post", new=AsyncMock(side_effect=RuntimeError("network down"))
        ):
            result = await client.delegate_to_org(to_org_id="org-2", task="Audit finances")
        assert result.result is not None
        assert result.result.success is False
        assert "network down" in result.result.error
        await client.close()

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        client = OrgA2AClient(calling_org_id=ORG_ID, calling_tenant_id=TENANT_ID)
        with patch.object(client._http, "aclose", new=AsyncMock()) as mock_aclose:
            await client.close()
        mock_aclose.assert_awaited_once()
