"""Regression: the planner fell back to the hard-coded OpenAI slug "gpt-5.2".

With no router model and a planner provider without ``_default_model`` the plan
request carried "gpt-5.2" to whatever provider was wired (Anthropic, Gemini,
on-prem). The fallback is now the deployment's configured model, else "" (every
provider treats an empty model as "use my default").
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="plan-model", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _NoDefaultModelProvider(FakeProvider):
    def __getattribute__(self, name: str) -> Any:
        if name == "_default_model":
            raise AttributeError(name)
        return super().__getattribute__(name)


@pytest.mark.parametrize(("env", "expected"), [({}, ""), ({"DEFAULT_MODEL": "cfg-m"}, "cfg-m")])
async def test_planner_model_fallback_is_not_a_vendor_slug(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    for var in ("NVIDIA_MODEL", "DEFAULT_MODEL", "OPENAI_MODEL", "DEFAULT_PLANNING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    planner = _NoDefaultModelProvider(responses=['{"steps": ["do it"]}'] * 3)
    graph = AgentGraph(
        planner=planner, executor=FakeProvider(), verifier=FakeProvider(), model_router=None
    )
    graph._model_router = None
    state = AgentState(goal="g", tenant_ctx=T)

    await graph._node_plan({"agent_state": state, "tenant_ctx": T, "rag_context": ""})

    models = [getattr(req, "model", None) for req in planner.call_history]
    assert models, "planner was not called"
    assert "gpt-5.2" not in models
    assert models[0] == expected
