"""TRG-10 / TRG-11: POST /nl/schedule validates, enforces quota and is metered.

The route stored parsed specs with no ``is_supported`` / ``validate_spec``
check (an ``s3_event`` or a time-less ``once`` was stored and never fired), and
it called ``nl.parse`` without the tenant, so the LLM parse skipped the budget
preflight and the charge.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.providers.base import CompletionResponse
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.nl_scheduler import NLScheduler
from app.triggers.store import ScheduleStore
from tests.api.test_schedules_api import _CTX, _VALID_KEY, _make_app


def _post(client: TestClient, command: str = "do the thing") -> Any:
    return client.post("/nl/schedule", json={"command": command}, headers={"X-API-Key": _VALID_KEY})


def _client(specs: list[TriggerSpec], store: ScheduleStore | None = None) -> tuple[Any, Any]:
    nl = AsyncMock()
    nl.parse.return_value = specs
    store = store or ScheduleStore()
    return TestClient(_make_app(schedule_store=store, nl_scheduler=nl)), store


def _count(store: ScheduleStore) -> int:
    return len(store.list_all(tenant_ctx=_CTX))


def test_unsupported_type_creates_nothing_and_says_why() -> None:
    client, store = _client(
        [
            TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *"),
            TriggerSpec(trigger_type=TriggerType.S3_EVENT, s3_bucket="b"),
        ]
    )

    r = _post(client)

    assert r.status_code == 422, r.text
    errors = r.json()["detail"]["errors"]
    assert [e["index"] for e in errors] == [1]
    assert errors[0]["trigger_type"] == "s3_event"
    assert "not yet supported" in errors[0]["reason"]
    assert _count(store) == 0  # the valid cron was not created either


def test_once_without_a_time_creates_nothing() -> None:
    client, store = _client([TriggerSpec(trigger_type=TriggerType.ONCE, description="x")])

    r = _post(client)

    assert r.status_code == 422, r.text
    assert "fire_at_iso" in r.json()["detail"]["errors"][0]["reason"]
    assert _count(store) == 0


def test_no_schedule_understood_is_a_422() -> None:
    client, store = _client([])
    r = _post(client)
    assert r.status_code == 422, r.text
    assert _count(store) == 0


def test_quota_is_enforced_and_a_partial_batch_is_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.triggers import quota

    store = ScheduleStore()
    client, _ = _client(
        [
            TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 8 * * *"),
            TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *"),
        ],
        store,
    )
    monkeypatch.setitem(quota.PLAN_MAX_TRIGGERS, "professional", 1)

    r = _post(client)

    assert r.status_code == 403, r.text
    assert _count(store) == 0


# ── TRG-11: the NL parse is charged to the tenant ──────────────────────────────


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, _req: Any) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            content='{"trigger_type": "cron", "cron_expression": "0 9 * * *"}',
            model="m",
            input_tokens=10,
            output_tokens=5,
        )


class _Controller:
    def __init__(self, *, allow: bool) -> None:
        self.allow = allow
        self.checked_tenants: list[str] = []

    async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
        self.checked_tenants.append(tenant_ctx.tenant_id)
        return self.allow


class _Tracker:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def _metered_client(
    monkeypatch: pytest.MonkeyPatch, *, allow: bool
) -> tuple[TestClient, _Provider, _Controller, ScheduleStore]:
    from app.providers import guarded_completion

    controller = _Controller(allow=allow)
    monkeypatch.setattr(guarded_completion, "_platform_services", lambda: (controller, None))
    provider = _Provider()
    store = ScheduleStore()
    app = _make_app(schedule_store=store, nl_scheduler=NLScheduler(provider))  # type: ignore[arg-type]
    return TestClient(app), provider, controller, store


def test_nl_parse_is_budget_checked_and_charged_to_the_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charged: list[tuple[str, str]] = []

    async def fake_charge(_shim: Any, *, role: str, tenant_ctx: Any, **_k: Any) -> None:
        charged.append((role, tenant_ctx.tenant_id))

    monkeypatch.setattr("app.agent.nodes.llm_cost.charge_llm_call", fake_charge)
    client, provider, controller, store = _metered_client(monkeypatch, allow=True)

    r = _post(client, "every day at 9am")

    assert r.status_code == 201, r.text
    assert provider.calls == 1
    assert controller.checked_tenants == [_CTX.tenant_id]
    assert charged == [("nl_scheduler", _CTX.tenant_id)]
    assert _count(store) == 1


def test_nl_parse_is_refused_when_the_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, provider, _controller, store = _metered_client(monkeypatch, allow=False)

    r = _post(client, "every day at 9am")

    assert r.status_code == 429, r.text
    assert "budget" in r.json()["detail"].lower()
    assert provider.calls == 0
    assert _count(store) == 0


async def test_nl_parse_charges_the_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The post-call charge reaches the cost pipeline with the tenant."""
    from app.providers import guarded_completion

    charged: list[Any] = []

    async def fake_charge(_shim: Any, *, resp: Any, role: str, tenant_ctx: Any, **_k: Any) -> None:
        charged.append((role, tenant_ctx.tenant_id, resp.input_tokens))

    monkeypatch.setattr(
        guarded_completion, "_platform_services", lambda: (_Controller(allow=True), _Tracker())
    )
    monkeypatch.setattr("app.agent.nodes.llm_cost.charge_llm_call", fake_charge)

    specs = await NLScheduler(_Provider()).parse(  # type: ignore[arg-type]
        "every day at 9am", tenant_ctx=SimpleNamespace(tenant_id=_CTX.tenant_id)
    )

    assert specs[0].cron_expression == "0 9 * * *"
    assert charged == [("nl_scheduler", _CTX.tenant_id, 10)]
