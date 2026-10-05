"""P8b-3: GET /guardrails/violations serves the durable guardrails-v2 violations.

The legacy endpoint (the one the Guardrail Center UI called) read a per-process
``_violations_store`` that nothing ever wrote to, so it always answered "no
violations" while ``guardrail_violations`` held them. Both endpoints now read
the durable store, tenant-scoped and keyset-paginated.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.guardrails_v2 import engine as engine_mod
from app.guardrails_v2.engine import GuardrailsEngine
from app.guardrails_v2.models import GuardrailViolation
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_A = TenantContext(tenant_id="t-viol-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_B = TenantContext(tenant_id="t-viol-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_KEYS = {"key-a": _A, "key-b": _B}
_TS = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.UTC)


def _v(tenant: str, i: int, **kw: Any) -> GuardrailViolation:
    fields: dict[str, Any] = {
        "violation_id": f"v{i:03d}", "tenant_id": tenant, "rule_id": f"rule-{i}",
        "rule_name": f"Rule {i}", "layer": "tool_output", "action_taken": "block",
        "category": "pii", "content_preview": "email ***", "severity": "high",
        "goal_id": "workflow:run-1",
        # Pairs share a timestamp: the id breaks the tie in the keyset.
        "created_at": (_TS + datetime.timedelta(seconds=i // 2)).isoformat(), **kw,
    }
    return GuardrailViolation(**fields)


class _Repo:
    """Durable-store double with the repository's keyset semantics."""

    def __init__(self, rows: list[GuardrailViolation], *, fail: bool = False) -> None:
        self.rows = rows
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def load(self, tenant_id: str) -> list[Any]:
        return []

    async def list_violations(self, tenant_id: str, **kw: Any) -> list[GuardrailViolation]:
        self.calls.append((tenant_id, kw))
        if self.fail:
            raise ConnectionError("db down")
        rows = [r for r in self.rows if r.tenant_id == tenant_id]
        if kw.get("severity"):
            rows = [r for r in rows if r.severity == kw["severity"]]
        rows.sort(key=lambda r: (datetime.datetime.fromisoformat(r.created_at or ""),
                                 r.violation_id), reverse=True)
        before = kw.get("before")
        if before is not None:
            rows = [r for r in rows if (datetime.datetime.fromisoformat(r.created_at or ""),
                                        r.violation_id) < before]
        return rows[: kw["limit"]]

    async def violation_stats(self, tenant_id: str, **kw: Any) -> list[Any]:
        if self.fail:
            raise ConnectionError("db down")
        return [("high", "tool_output", "pii", 7, 2)]


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> _Repo:
    store = _Repo([_v(_A.tenant_id, i) for i in range(7)] + [_v(_B.tenant_id, 99)])
    fresh = GuardrailsEngine()
    fresh.bind_repository(store)
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)
    return store


def _client() -> TestClient:
    from app.api.guardrails import router as legacy
    from app.api.guardrails_v2 import router as v2

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(legacy)
    app.include_router(v2)
    return TestClient(app, raise_server_exceptions=False)


def _walk(client: TestClient, path: str, key: str, id_field: str) -> list[str]:
    seen: list[str] = []
    cursor = ""
    for _ in range(10):
        resp = client.get(f"{path}?limit=3{cursor}", headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        seen += [v[id_field] for v in body["violations"]]
        if body["next_cursor"] is None:
            return seen
        cursor = f"&cursor={body['next_cursor']}"
    raise AssertionError("pagination did not end")


@pytest.mark.parametrize(("path", "id_field"), [("/guardrails/violations", "id"),
                                                ("/guardrails-v2/violations", "violation_id")])
def test_both_endpoints_page_the_durable_store_per_tenant(
    repo: _Repo, path: str, id_field: str
) -> None:
    client = _client()
    assert _walk(client, path, "key-a", id_field) == [f"v{i:03d}" for i in range(6, -1, -1)]
    assert _walk(client, path, "key-b", id_field) == ["v099"]
    assert {tenant for tenant, _ in repo.calls} == {_A.tenant_id, _B.tenant_id}
    assert all(kw["limit"] == 4 for _, kw in repo.calls)  # one row past the page, never all


def test_legacy_endpoint_keeps_its_shape_and_points_to_v2(repo: _Repo) -> None:
    resp = _client().get("/guardrails/violations?severity=high", headers={"X-API-Key": "key-a"})
    assert resp.status_code == 200
    assert resp.headers["Deprecation"] == "true"
    assert "/guardrails-v2/violations" in resp.headers["Link"]
    first = resp.json()["violations"][0]
    assert first == {
        "id": "v006", "guardrail_id": "rule-6", "guardrail_name": "Rule 6", "type": "pii",
        "severity": "high", "layer": "tool_output", "action_taken": "block",
        "message": "email ***", "goal_id": "workflow:run-1", "created_at": first["created_at"],
    }


@pytest.mark.parametrize("path", ["/guardrails/violations", "/guardrails-v2/violations"])
def test_a_bad_cursor_is_422_and_an_unreadable_store_is_503(repo: _Repo, path: str) -> None:
    client = _client()
    assert client.get(f"{path}?cursor=%%%", headers={"X-API-Key": "key-a"}).status_code == 422
    repo.fail = True
    assert client.get(path, headers={"X-API-Key": "key-a"}).status_code == 503


def test_stats_read_the_durable_store(repo: _Repo) -> None:
    client = _client()
    body = client.get("/guardrails/stats", headers={"X-API-Key": "key-a"}).json()
    assert body["total_window"] == body["total_all"] == 7
    assert body["total_24h"] == 2
    assert body["by_layer"] == {"tool_output": 7}
    assert body["risk_score_p95"] is None
    repo.fail = True
    assert client.get("/guardrails/stats", headers={"X-API-Key": "key-a"}).status_code == 503
