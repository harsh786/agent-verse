"""P8b-1: a worker-run goal's step output is screened before it is stored or streamed.

The Celery worker writes its own goal events (``append_submitted_goal_event``):
the step output reached ``goal_events`` (GET /goals/{id}, replay) and the
``goal_events:*`` channel (SSE) with the PII still in it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.guardrails_v2 import engine as engine_mod
from app.guardrails_v2.engine import GuardrailsEngine
from app.guardrails_v2.models import GuardrailAction, GuardrailLayer, GuardrailRule

_T = "t-worker-screen"
EMAIL = "ravi.menon@bramblewood-freight.example"
PHONE = "+91 98450 12345"
CARD = "4111111111111111"
SECRET = "ghp_" + "Z" * 36  # split: secret scanners
OUTPUT = f"Card for Ravi: {EMAIL}, {PHONE}, card {CARD}, token {SECRET}; project ORCA"


class _Repo:
    def __init__(self, rules: list[GuardrailRule]) -> None:
        self.rules = rules

    async def load(self, tenant_id: str) -> list[GuardrailRule]:
        return [r for r in self.rules if r.tenant_id == tenant_id]


class _EmittingGraph:
    def __init__(self, **kwargs: Any) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        await kwargs["event_callback"](
            {"type": "step_complete", "step": "build the card", "output": OUTPUT}
        )
        raise PermissionError("denied after the step")


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, data: str) -> None:
        self.published.append((channel, data))

    def get(self, key: str) -> None:
        return None

    def smembers(self, key: str) -> set[str]:
        return set()

    def scan_iter(self, *a: Any, **k: Any) -> Any:
        return iter(())

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: None


@pytest.mark.parametrize("tenant_rule", [False, True])
def test_worker_step_output_is_redacted_in_stored_and_published_events(
    monkeypatch: pytest.MonkeyPatch, tenant_rule: bool
) -> None:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks
    from app.services.event_store import EventStore
    from app.services.goal_service import GoalService

    rules = (
        [GuardrailRule(rule_id="r-kw", tenant_id=_T, name="codename",
                       rule_type="keyword_block", layers=[GuardrailLayer.FINAL_OUTPUT],
                       action=GuardrailAction.REDACT, config={"keywords": ["orca"]})]
        if tenant_rule else []
    )
    fresh = GuardrailsEngine()
    fresh.bind_repository(_Repo(rules))
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)

    stored: list[dict[str, Any]] = []

    async def _append(self: Any, goal_id: str, event: dict[str, Any], *, tenant_ctx: Any) -> int:
        stored.append(json.loads(json.dumps(event, default=str)))
        return len(stored)

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    redis = _Redis()
    monkeypatch.setattr(EventStore, "append_event", _append)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", _noop)
    monkeypatch.setattr(_graph_mod, "AgentGraph", _EmittingGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: redis)
    monkeypatch.setattr(tasks, "_finalize_owning_mission", _noop)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _noop)

    tasks.run_goal.push_request(retries=0, called_directly=True)
    try:
        tasks.run_goal.run("goal-screen-1", _T, "write a contact card")
    finally:
        tasks.run_goal.pop_request()

    published = [json.loads(d) for _, d in redis.published]
    steps = [p["payload"] for p in published if p.get("type") == "step_complete"]
    assert steps, published
    stored_steps = [e for e in stored if e.get("type") == "step_complete"]
    assert stored_steps, stored
    blob = json.dumps([published, stored])
    for raw in (EMAIL, PHONE, CARD, SECRET):
        assert raw not in blob
    assert "***REDACTED***" in steps[0]["output"]
    assert stored_steps[0]["_screened"] is True
    assert ("ORCA" in steps[0]["output"]) is (not tenant_rule)
