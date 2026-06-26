"""Tests for the FastAPI app factory: health, metrics, and error handling."""

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ValidationError
from app.main import create_app
from app.mcp.registry import MCPServerConfig
from app.observability.health import HealthCheck
from app.services.goal_queue import CeleryGoalTaskQueue
from app.tenancy.context import PlanTier, TenantContext


def test_health_ok_with_no_dependencies() -> None:
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["checks"] == {}


def test_exported_runtime_app_manages_connection_pools() -> None:
    from app.main import app as runtime_app

    assert runtime_app.state.manage_pools is True


def test_manage_pools_wires_goal_task_queue_when_redis_configured() -> None:
    app = create_app(
        settings=Settings(redis_url="redis://queue.example/0"),
        manage_pools=True,
    )

    assert isinstance(app.state.goal_service._task_queue, CeleryGoalTaskQueue)


async def test_app_mcp_client_resolves_connector_secret_refs_from_state_store() -> None:
    app = create_app()
    tenant_ctx = TenantContext(
        tenant_id="tenant-app-secret",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="kid-app-secret",
    )
    app.state.connector_secret_store = {
        "vault://connectors/srv-abc/token": "resolved-runtime-token"
    }
    cfg = MCPServerConfig(
        name="github",
        url="https://mcp.example.com/mcp",
        auth_type="bearer",
        auth_config={"token": "vault://connectors/srv-abc/token"},
    )

    headers = await app.state.mcp_client._build_auth_headers(cfg, tenant_ctx=tenant_ctx)

    assert headers["Authorization"] == "Bearer resolved-runtime-token"


def test_health_unhealthy_when_a_dependency_is_down() -> None:
    async def failing() -> None:
        raise RuntimeError("connection refused")

    app = create_app(health_checks=[HealthCheck(name="postgres", check=failing)])
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["postgres"]["status"] == "down"


def test_health_healthy_when_check_passes() -> None:
    async def ok() -> None:
        return None

    app = create_app(health_checks=[HealthCheck(name="redis", check=ok)])
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["checks"]["redis"]["status"] == "up"


def test_metrics_endpoint_exposes_prometheus() -> None:
    client = TestClient(create_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_platform_error_renders_structured_envelope() -> None:
    app = create_app()
    router = APIRouter()

    # Use /health/ prefix so TenantMiddleware bypasses auth for this test route.
    @router.get("/health/boom")
    async def boom() -> None:
        raise ValidationError("bad input", details={"field": "email"})

    app.include_router(router)
    resp = TestClient(app, raise_server_exceptions=False).get("/health/boom")
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"] == {"field": "email"}
    assert "error_id" in err


def test_unexpected_error_returns_generic_500_without_leaking() -> None:
    app = create_app()
    router = APIRouter()

    # Use /health/ prefix so TenantMiddleware bypasses auth for this test route.
    @router.get("/health/crash")
    async def crash() -> None:
        raise ValueError("secret internal detail")

    app.include_router(router)
    resp = TestClient(app, raise_server_exceptions=False).get("/health/crash")
    assert resp.status_code == 500
    err = resp.json()["error"]
    assert err["code"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in resp.text
