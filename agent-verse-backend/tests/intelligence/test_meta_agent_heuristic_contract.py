"""Meta-agent must not pass a heuristic guess off as an LLM-designed agent.

Regression: on provider error / timeout / non-JSON output the planner silently
returned a heuristic config and POST /agents/create still created the agent
with 201, indistinguishable from a real design.

Contract now: the planner marks such configs ``generated_by="heuristic"`` (with
``fallback_reason``); the endpoint answers 502 with the draft and creates
nothing unless the caller explicitly sends ``accept_heuristic: true``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.intelligence.meta_agent import MetaAgentPlanner
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-meta-h", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "ak_meta_heuristic"
_H = {"X-API-Key": _KEY}


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _Provider:
    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self.content = content
        self.exc = exc

    async def complete(self, req: Any) -> _Resp:
        if self.exc is not None:
            raise self.exc
        return _Resp(self.content or "")


class _SlowProvider:
    async def complete(self, req: Any) -> _Resp:
        await asyncio.sleep(5)
        return _Resp("{}")


@pytest.mark.asyncio
async def test_provider_error_marks_config_heuristic() -> None:
    cfg = await MetaAgentPlanner(_Provider(exc=RuntimeError("rate limited"))).plan(
        command="build a research agent", tenant_ctx=_CTX
    )
    assert cfg.generated_by == "heuristic"
    assert "rate limited" in cfg.fallback_reason


@pytest.mark.asyncio
async def test_timeout_marks_config_heuristic() -> None:
    cfg = await MetaAgentPlanner(_SlowProvider(), timeout_seconds=0.05).plan(  # type: ignore[arg-type]
        command="build a research agent", tenant_ctx=_CTX
    )
    assert cfg.generated_by == "heuristic"
    assert cfg.fallback_reason


@pytest.mark.asyncio
async def test_non_json_marks_config_heuristic() -> None:
    cfg = await MetaAgentPlanner(_Provider("Sure! Here's an agent...")).plan(  # type: ignore[arg-type]
        command="build a research agent", tenant_ctx=_CTX
    )
    assert cfg.generated_by == "heuristic"


@pytest.mark.asyncio
async def test_valid_json_is_llm_generated() -> None:
    cfg = await MetaAgentPlanner(  # type: ignore[arg-type]
        _Provider('{"name": "Research Agent", "goal_template": "Research {topic}"}')
    ).plan(command="build a research agent", tenant_ctx=_CTX)
    assert cfg.generated_by == "llm"
    assert cfg.name == "Research Agent"


def _app(provider: Any) -> tuple[FastAPI, AgentStore]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(agents_router)
    store = AgentStore()
    app.state.agent_store = store
    app.state.meta_agent = MetaAgentPlanner(provider)
    return app, store


def test_create_with_heuristic_config_is_502_and_creates_nothing() -> None:
    app, store = _app(_Provider(exc=RuntimeError("provider down")))
    client = TestClient(app)
    r = client.post("/agents/create", json={"command": "a research agent"}, headers=_H)
    assert r.status_code == 502
    body = r.json()
    assert isinstance(body["detail"], str)  # clients render `detail` as text
    assert body["generated_by"] == "heuristic"
    assert "provider down" in body["fallback_reason"]
    assert body["draft_config"]["goal_template"] == "a research agent"
    assert client.get("/agents", headers=_H).json() == []


def test_create_with_heuristic_config_requires_explicit_confirmation() -> None:
    app, _ = _app(_Provider(exc=RuntimeError("provider down")))
    client = TestClient(app)
    r = client.post(
        "/agents/create",
        json={"command": "a research agent", "accept_heuristic": True},
        headers=_H,
    )
    assert r.status_code == 201
    assert r.json()["meta_agent_config"]["generated_by"] == "heuristic"
    assert len(client.get("/agents", headers=_H).json()) == 1


def test_create_with_llm_config_reports_generated_by_llm() -> None:
    app, _ = _app(_Provider('{"name": "Research Agent", "goal_template": "Research {t}"}'))
    r = TestClient(app).post("/agents/create", json={"command": "research"}, headers=_H)
    assert r.status_code == 201
    assert r.json()["meta_agent_config"]["generated_by"] == "llm"
