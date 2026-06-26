"""E2E API tests — full HTTP request/response cycle through the FastAPI app.

Uses FastAPI's TestClient (synchronous) with fake service implementations
injected into app.state. A custom TenantMiddleware is added to enable
authenticated endpoint tests.
"""

from __future__ import annotations

import builtins
import hashlib
import secrets
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.mcp.registry import MCPRegistry
from app.tenancy.context import PlanTier, TenantContext

# ── Fake service implementations ─────────────────────────────────────────────

class _FakeRedis:
    """In-memory Redis stub for MCPRegistry."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, k: str) -> str | None:
        return self._d.get(k)

    async def set(self, k: str, v: str, ex: object = None) -> None:
        self._d[k] = v

    async def delete(self, k: str) -> int:
        existed = k in self._d
        self._d.pop(k, None)
        return int(existed)

    async def sadd(self, k: str, v: str) -> None:
        self._s.setdefault(k, set()).add(v)

    async def srem(self, k: str, v: str) -> None:
        self._s.get(k, set()).discard(v)

    async def smembers(self, k: str) -> builtins.set[str]:
        return self._s.get(k, set())


class FakeTenantService:
    """Minimal tenant service for E2E tests."""

    def __init__(self) -> None:
        self._tenants: dict[str, dict[str, Any]] = {}
        self._keys: dict[str, dict[str, Any]] = {}
        self._key_hash_to_ctx: dict[str, TenantContext] = {}

        # Pre-register a test tenant with a known key
        tid = "e2e-test-tenant"
        raw_key = "av_professional_testkey_e2e_12345"
        key_id = "key-e2e-001"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        self._tenants[tid] = {
            "tenant_id": tid,
            "name": "E2E Tenant",
            "email": "e2e@test.com",
            "plan": "professional",
        }
        self._keys[key_hash] = {
            "key_id": key_id,
            "tenant_id": tid,
            "name": "Test Key",
            "scopes": [],
            "active": True,
        }
        self._key_hash_to_ctx[key_hash] = TenantContext(
            tenant_id=tid,
            plan=PlanTier.PROFESSIONAL,
            api_key_id=key_id,
        )
        self.test_api_key = raw_key
        self.test_tenant_id = tid

    async def create_tenant(self, name: str, email: str) -> dict[str, Any]:
        tid = uuid.uuid4().hex
        raw_key = f"av_free_{secrets.token_urlsafe(20)}"
        key_id = uuid.uuid4().hex
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        self._tenants[tid] = {
            "tenant_id": tid,
            "name": name,
            "email": email,
            "plan": "free",
        }
        self._keys[key_hash] = {
            "key_id": key_id,
            "tenant_id": tid,
            "name": "default",
            "scopes": [],
            "active": True,
        }
        self._key_hash_to_ctx[key_hash] = TenantContext(
            tenant_id=tid,
            plan=PlanTier.FREE,
            api_key_id=key_id,
        )
        return {
            "tenant_id": tid,
            "name": name,
            "email": email,
            "raw_key": raw_key,
        }

    async def get_tenant(self, tenant_id: str) -> dict[str, Any]:
        from app.core.errors import NotFoundError
        if tenant_id not in self._tenants:
            raise NotFoundError(f"Tenant {tenant_id} not found")
        return self._tenants[tenant_id]

    async def list_api_keys(self, tenant_id: str) -> list[dict[str, Any]]:
        return [v for v in self._keys.values() if v["tenant_id"] == tenant_id]

    async def create_api_key(
        self,
        tenant_id: str,
        name: str,
        scopes: list[str],
        expires_at: object = None,
    ) -> dict[str, Any]:
        raw_key = f"av_professional_{secrets.token_urlsafe(20)}"
        key_id = uuid.uuid4().hex
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        self._keys[key_hash] = {
            "key_id": key_id,
            "tenant_id": tenant_id,
            "name": name,
            "scopes": scopes,
            "active": True,
        }
        self._key_hash_to_ctx[key_hash] = TenantContext(
            tenant_id=tenant_id,
            plan=PlanTier.PROFESSIONAL,
            api_key_id=key_id,
        )
        return {"key_id": key_id, "name": name, "raw_key": raw_key}

    async def revoke_api_key(self, tenant_id: str, key_id: str) -> None:
        from app.core.errors import NotFoundError
        for v in self._keys.values():
            if v["key_id"] == key_id and v["tenant_id"] == tenant_id:
                v["active"] = False
                return
        raise NotFoundError(f"Key {key_id} not found")

    async def resolve_api_key(self, raw_key: str) -> TenantContext | None:
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return self._key_hash_to_ctx.get(key_hash)


class FakeGoalService:
    """Minimal goal service for E2E tests (matches the real service contract)."""

    def __init__(self) -> None:
        self._goals: dict[str, dict[str, Any]] = {}

    async def submit_goal(
        self,
        goal: str,
        priority: str = "normal",
        dry_run: bool = False,
        tenant_ctx: object = None,
        agent_id: str | None = None,
        workflow_mode: str = "single_agent",
    ) -> dict[str, Any]:
        goal_id = uuid.uuid4().hex
        self._goals[goal_id] = {
            "goal_id": goal_id,
            "goal": goal,
            "status": "planning",
            "priority": priority,
            "dry_run": dry_run,
            "agent_id": agent_id,
            "workflow_mode": workflow_mode,
        }
        return {
            "goal_id": goal_id,
            "status": "planning",
            "agent_id": agent_id,
            "workflow_mode": workflow_mode,
        }

    async def get_goal(
        self, goal_id: str, tenant_ctx: object = None
    ) -> dict[str, Any]:
        from app.core.errors import NotFoundError
        if goal_id not in self._goals:
            raise NotFoundError(f"Goal {goal_id} not found")
        return self._goals[goal_id]

    async def cancel_goal(
        self, goal_id: str, tenant_ctx: object = None
    ) -> dict[str, Any]:
        if goal_id in self._goals:
            self._goals[goal_id]["status"] = "cancelled"
        return {"goal_id": goal_id, "status": "cancelled"}

    async def get_audit_entries(
        self, goal_id: str, tenant_ctx: object = None
    ) -> list[dict[str, Any]]:
        from app.core.errors import NotFoundError
        if goal_id not in self._goals:
            raise NotFoundError(f"Goal {goal_id} not found")
        return []

    async def handle_approval(
        self,
        goal_id: str,
        request_id: str,
        action: str,
        approver: str,
        note: str = "",
        tenant_ctx: object = None,
    ) -> dict[str, Any]:
        return {"goal_id": goal_id, "request_id": request_id, "action": action}


# ── App factory ───────────────────────────────────────────────────────────────

def _make_test_client() -> tuple[TestClient, FakeTenantService]:
    """Build a test FastAPI app with fake services injected at creation time.

    We pass the fake TenantService directly to create_app() so that the
    TenantMiddleware closure captures the right resolver from the start.
    create_app() accepts Any for service overrides, so duck-typed fakes work.
    """
    fake_ts = FakeTenantService()
    fake_gs = FakeGoalService()

    app = create_app(
        tenant_service=fake_ts,
        goal_service=fake_gs,
        mcp_registry=MCPRegistry(redis=_FakeRedis()),
    )

    return TestClient(app, raise_server_exceptions=False), fake_ts


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health_check() -> None:
    client, _ = _make_test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"healthy", "unhealthy"}


def test_metrics_endpoint() -> None:
    client, _ = _make_test_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_signup_creates_tenant() -> None:
    client, _ = _make_test_client()
    resp = client.post(
        "/tenants/signup",
        json={"name": "Test Corp", "email": "corp@test.com"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "tenant_id" in data
    assert "raw_key" in data


def test_submit_goal_requires_auth() -> None:
    client, _ = _make_test_client()
    resp = client.post("/goals", json={"goal": "test goal"})
    assert resp.status_code == 401


def test_submit_goal_with_valid_api_key() -> None:
    client, fake_ts = _make_test_client()
    resp = client.post(
        "/goals",
        json={"goal": "fix the bug"},
        headers={"X-API-Key": fake_ts.test_api_key},
    )
    # 202 Accepted when tenant middleware resolves the key successfully
    assert resp.status_code == 202
    data = resp.json()
    assert "goal_id" in data
    assert data["status"] == "planning"


def test_get_nonexistent_goal_returns_404() -> None:
    client, fake_ts = _make_test_client()
    resp = client.get(
        "/goals/nonexistent-goal-id",
        headers={"X-API-Key": fake_ts.test_api_key},
    )
    assert resp.status_code == 404


def test_connectors_catalog_requires_auth() -> None:
    client, _ = _make_test_client()
    resp = client.get("/connectors/catalog")
    assert resp.status_code == 401


def test_connectors_catalog_with_auth() -> None:
    client, fake_ts = _make_test_client()
    resp = client.get(
        "/connectors/catalog",
        headers={"X-API-Key": fake_ts.test_api_key},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
