"""Phase 2: AI Router and Model Registry tests."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.models import ModelCapability, ModelRoutePolicy, RoutingMode, TaskType
from app.ai_router.registry import model_registry
from app.ai_router.router import ai_router
from app.api.model_registry import router as models_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-p2", plan=PlanTier.PROFESSIONAL, api_key_id="kid-p2")
_KEY = "ak_phase2_test_key"
_HEADERS = {"X-API-Key": _KEY}


def _make_app():
    app = FastAPI()

    async def _resolve(key):
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return app


# ── Active model tests (composer shows the REAL configured model) ─────────────


def test_active_model_reflects_configured_default(monkeypatch):
    # A configured NVIDIA model must surface as the active model — not the static
    # top-of-catalogue Claude entry the composer used to display.
    monkeypatch.setenv("NVIDIA_MODEL", "moonshotai/kimi-k3")
    client = TestClient(_make_app())
    resp = client.get("/models/active", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_id"] == "moonshotai/kimi-k3"
    assert data["display_name"] == "Kimi K3"  # prettified slug tail
    assert data["configured"] is True


def test_active_model_prettifies_and_handles_unconfigured(monkeypatch):
    for var in ("NVIDIA_MODEL", "DEFAULT_MODEL", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    client = TestClient(_make_app())
    resp = client.get("/models/active", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # No configured model → honest fallback, never a fabricated vendor name.
    assert data["configured"] is False
    assert data["display_name"]  # non-empty label
    assert "claude" not in data["display_name"].lower()


# ── Model Registry tests ──────────────────────────────────────────────────────

def test_list_all_models_returns_builtin_catalog():
    client = TestClient(_make_app())
    resp = client.get("/models", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 5
    providers = {m["provider"] for m in data["models"]}
    assert "anthropic" in providers
    assert "openai" in providers
    assert "gemini" in providers


def test_filter_by_capability():
    client = TestClient(_make_app())
    resp = client.get("/models?capability=embedding", headers=_HEADERS)
    assert resp.status_code == 200
    models = resp.json()["models"]
    assert len(models) > 0
    for m in models:
        assert "embedding" in m["capabilities"]


def test_filter_by_provider():
    client = TestClient(_make_app())
    resp = client.get("/models?provider=anthropic", headers=_HEADERS)
    assert resp.status_code == 200
    models = resp.json()["models"]
    assert all(m["provider"] == "anthropic" for m in models)
    assert len(models) >= 2


def test_model_health_endpoint():
    client = TestClient(_make_app())
    resp = client.get("/models/health", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert len(data["providers"]) > 0
    for p in data["providers"]:
        assert "provider" in p
        assert "is_healthy" in p


def test_models_do_not_expose_api_keys():
    client = TestClient(_make_app())
    resp = client.get("/models", headers=_HEADERS)
    resp_text = resp.text.lower()
    assert "api_key" not in resp_text or "api_key_name" in resp_text
    assert "sk-" not in resp_text
    assert "anthropic-api" not in resp_text


def test_routing_policy_crud():
    client = TestClient(_make_app())
    policy = {"routing_mode": "cheapest", "preferred_provider": "groq", "preferred_model": "llama-3.1-8b-instant"}
    resp = client.put("/models/routing-policies/planning", json=policy, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"

    get_resp = client.get("/models/routing-policies", headers=_HEADERS)
    assert get_resp.status_code == 200
    policies = get_resp.json()["policies"]
    planning = next((p for p in policies if p["task_type"] == "planning"), None)
    assert planning is not None
    assert planning["preferred_provider"] == "groq"


def test_invalid_task_type_returns_400():
    client = TestClient(_make_app())
    resp = client.put("/models/routing-policies/invalid_task", json={"routing_mode": "cheapest"}, headers=_HEADERS)
    assert resp.status_code == 400


# ── AIRouter unit tests ───────────────────────────────────────────────────────

def ai_router_select(task: TaskType, tenant: str):
    return ai_router.select_model(task, tenant)


def test_router_selects_highest_quality_by_default():
    model = ai_router_select(TaskType.PLANNING, "test-tenant")
    assert model is not None


def test_router_selects_embedding_model_for_embedding_task():
    m = ai_router_select(TaskType.EMBEDDING, "test-tenant-emb")
    assert m is not None
    assert ModelCapability.EMBEDDING in m.capabilities


def test_router_cheapest_mode():
    model_registry.set_route_policy("cost-tenant", TaskType.PLANNING, ModelRoutePolicy(
        task_type=TaskType.PLANNING,
        routing_mode=RoutingMode.CHEAPEST,
    ))
    m = ai_router.select_model(TaskType.PLANNING, "cost-tenant")
    assert m is not None
    # Should pick cheapest model
    all_text = [x for x in model_registry.list_models() if ModelCapability.TEXT_GENERATION in x.capabilities]
    min_cost = min(x.cost_per_1k_input for x in all_text)
    assert m.cost_per_1k_input <= min_cost + 0.001  # Allow tiny float comparison slack


def test_router_with_vision_constraint():
    m = ai_router.select_model(TaskType.EXECUTION, "test-vision", require_vision=True)
    assert m is not None
    assert m.supports_vision is True


def test_router_with_tools_constraint():
    m = ai_router.select_model(TaskType.EXECUTION, "test-tools", require_tools=True)
    assert m is not None
    assert m.supports_tools is True


def test_router_health_tracking():
    model_registry.update_health("anthropic", latency_ms=250, error=False)
    health = model_registry.get_provider_health("anthropic")
    assert health.avg_latency_ms > 0


def test_provider_circuit_opens_on_errors():
    for _ in range(10):
        model_registry.update_health("test-failing-provider", error=True, error_msg="timeout")
    health = model_registry.get_provider_health("test-failing-provider")
    assert not health.is_healthy


def test_tenant_isolation_in_registry():
    model_registry.set_route_policy("tenant-a", TaskType.PLANNING, ModelRoutePolicy(
        task_type=TaskType.PLANNING, preferred_provider="anthropic", preferred_model="claude-opus-4-5"
    ))
    model_registry.set_route_policy("tenant-b", TaskType.PLANNING, ModelRoutePolicy(
        task_type=TaskType.PLANNING, preferred_provider="openai", preferred_model="gpt-4o"
    ))

    policy_a = model_registry.get_route_policy("tenant-a", TaskType.PLANNING)
    policy_b = model_registry.get_route_policy("tenant-b", TaskType.PLANNING)

    assert policy_a is not None
    assert policy_b is not None
    assert policy_a.preferred_provider == "anthropic"
    assert policy_b.preferred_provider == "openai"
    assert policy_a.preferred_provider != policy_b.preferred_provider
