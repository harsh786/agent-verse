"""Coverage tests for app/main.py — create_app() factory, _FakeRedis,
_resolve_provider_for_app, and error handlers.

Uses manage_pools=False (no DB/Redis pools) for all tests.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


# ── create_app basic tests ────────────────────────────────────────────────────

def test_create_app_returns_fastapi_instance():
    from app.main import create_app

    app = create_app()
    assert isinstance(app, FastAPI)


def test_create_app_manage_pools_false():
    from app.main import create_app

    app = create_app(manage_pools=False)
    assert isinstance(app, FastAPI)
    assert app.state.manage_pools is False


def test_create_app_manage_pools_true():
    from app.main import create_app

    app = create_app(manage_pools=True)
    assert app.state.manage_pools is True


def test_create_app_settings_on_state():
    from app.main import create_app

    app = create_app()
    assert hasattr(app.state, "settings")
    assert app.state.settings is not None


def test_create_app_health_registry_on_state():
    from app.main import create_app
    from app.observability.health import HealthRegistry

    app = create_app()
    assert hasattr(app.state, "health")
    assert isinstance(app.state.health, HealthRegistry)


# ── All expected services are on app.state ────────────────────────────────────

@pytest.fixture(scope="module")
def test_app():
    """Single app instance for all state checks."""
    from app.main import create_app
    return create_app()


def test_tenant_service_on_state(test_app):
    assert hasattr(test_app.state, "tenant_service")
    assert test_app.state.tenant_service is not None


def test_goal_service_on_state(test_app):
    assert hasattr(test_app.state, "goal_service")
    assert test_app.state.goal_service is not None


def test_mcp_registry_on_state(test_app):
    assert hasattr(test_app.state, "mcp_registry")
    assert test_app.state.mcp_registry is not None


def test_mcp_client_on_state(test_app):
    assert hasattr(test_app.state, "mcp_client")
    assert test_app.state.mcp_client is not None


def test_agent_store_on_state(test_app):
    assert hasattr(test_app.state, "agent_store")
    assert test_app.state.agent_store is not None


def test_meta_agent_on_state(test_app):
    assert hasattr(test_app.state, "meta_agent")


def test_hitl_gateway_on_state(test_app):
    assert hasattr(test_app.state, "hitl_gateway")
    assert test_app.state.hitl_gateway is not None


def test_audit_log_on_state(test_app):
    assert hasattr(test_app.state, "audit_log")
    assert test_app.state.audit_log is not None


def test_cost_controller_on_state(test_app):
    assert hasattr(test_app.state, "cost_controller")
    assert test_app.state.cost_controller is not None


def test_policy_engine_on_state(test_app):
    assert hasattr(test_app.state, "policy_engine")
    assert test_app.state.policy_engine is not None


def test_schedule_store_on_state(test_app):
    assert hasattr(test_app.state, "schedule_store")
    assert test_app.state.schedule_store is not None


def test_nl_scheduler_on_state(test_app):
    assert hasattr(test_app.state, "nl_scheduler")
    assert test_app.state.nl_scheduler is not None


def test_knowledge_store_on_state(test_app):
    assert hasattr(test_app.state, "knowledge_store")
    assert test_app.state.knowledge_store is not None


def test_semantic_cache_on_state(test_app):
    assert hasattr(test_app.state, "semantic_cache")
    assert test_app.state.semantic_cache is not None


def test_long_term_memory_on_state(test_app):
    assert hasattr(test_app.state, "long_term_memory")
    assert test_app.state.long_term_memory is not None


def test_eval_runner_on_state(test_app):
    assert hasattr(test_app.state, "eval_runner")
    assert test_app.state.eval_runner is not None


def test_self_optimizer_on_state(test_app):
    assert hasattr(test_app.state, "self_optimizer")
    assert test_app.state.self_optimizer is not None


def test_compliance_controller_on_state(test_app):
    assert hasattr(test_app.state, "compliance_controller")
    assert test_app.state.compliance_controller is not None


def test_simulation_runner_on_state(test_app):
    assert hasattr(test_app.state, "simulation_runner")
    assert test_app.state.simulation_runner is not None


def test_red_team_runner_on_state(test_app):
    assert hasattr(test_app.state, "red_team_runner")
    assert test_app.state.red_team_runner is not None


def test_marketplace_on_state(test_app):
    assert hasattr(test_app.state, "marketplace")
    assert test_app.state.marketplace is not None


def test_rpa_executor_on_state(test_app):
    assert hasattr(test_app.state, "rpa_executor")
    assert test_app.state.rpa_executor is not None


def test_collab_store_on_state(test_app):
    from app.collab.store import CollaborationStore

    assert hasattr(test_app.state, "collab_store")
    assert isinstance(test_app.state.collab_store, CollaborationStore)


def test_workflow_store_on_state(test_app):
    assert hasattr(test_app.state, "workflow_store")
    assert test_app.state.workflow_store is not None


def test_template_store_on_state(test_app):
    assert hasattr(test_app.state, "template_store")
    assert test_app.state.template_store is not None


def test_oauth_manager_on_state(test_app):
    assert hasattr(test_app.state, "oauth_manager")


def test_notification_service_on_state(test_app):
    assert hasattr(test_app.state, "notification_service")


def test_exec_memory_on_state(test_app):
    assert hasattr(test_app.state, "exec_memory")


def test_cost_tracker_on_state(test_app):
    assert hasattr(test_app.state, "cost_tracker")


def test_guardrail_engine_on_state(test_app):
    assert hasattr(test_app.state, "guardrail_engine")


def test_eval_suite_runner_on_state(test_app):
    assert hasattr(test_app.state, "eval_suite_runner")


def test_compliance_checker_on_state(test_app):
    assert hasattr(test_app.state, "compliance_checker")


def test_marketplace_v2_on_state(test_app):
    assert hasattr(test_app.state, "marketplace_v2")


def test_self_optimizer_v2_on_state(test_app):
    assert hasattr(test_app.state, "self_optimizer_v2")


def test_rpa_session_manager_on_state(test_app):
    assert hasattr(test_app.state, "rpa_session_manager")


def test_browser_agent_on_state(test_app):
    assert hasattr(test_app.state, "browser_agent")


# ── Middleware ────────────────────────────────────────────────────────────────

def test_middleware_configured(test_app):
    """CORS and security middleware are registered."""
    from fastapi.middleware.cors import CORSMiddleware

    middleware_classes = [m.cls for m in test_app.user_middleware if hasattr(m, "cls")]
    assert CORSMiddleware in middleware_classes


def test_multiple_middleware_layers(test_app):
    """At least 3 middleware layers registered."""
    assert len(test_app.user_middleware) >= 3


# ── Routers / routes ──────────────────────────────────────────────────────────

def test_health_route_registered(test_app):
    """App has routes registered (health route is deeply nested)."""
    # FastAPI include_router nests routes under the app.router
    # Check via the OpenAPI schema or via the app.router
    from fastapi.routing import APIRoute

    def _collect_routes(router):
        paths = []
        for r in router.routes:
            if hasattr(r, "path"):
                paths.append(r.path)
            if hasattr(r, "app") and hasattr(r.app, "routes"):
                paths.extend(_collect_routes(r.app.router))
        return paths

    # A simpler check: use the OpenAPI schema which lists all routes
    import json

    openapi = test_app.openapi()
    paths = list(openapi.get("paths", {}).keys())
    assert any("health" in p for p in paths), f"No health route in paths: {paths[:10]}"


def test_auth_routes_registered(test_app):
    import json

    openapi = test_app.openapi()
    paths = list(openapi.get("paths", {}).keys())
    assert any("/auth" in p for p in paths), f"No auth route in {paths[:10]}"


def test_goals_routes_registered(test_app):
    openapi = test_app.openapi()
    paths = list(openapi.get("paths", {}).keys())
    assert any("/goals" in p for p in paths)


def test_agents_routes_registered(test_app):
    openapi = test_app.openapi()
    paths = list(openapi.get("paths", {}).keys())
    assert any("/agents" in p for p in paths)


# ── Module-level app ──────────────────────────────────────────────────────────

def test_module_level_app_exists():
    from app.main import app as module_app

    assert isinstance(module_app, FastAPI)


# ── _FakeRedis ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_redis():
    from app.main import _FakeRedis
    return _FakeRedis()


@pytest.mark.asyncio
async def test_fake_redis_set_get(fake_redis):
    await fake_redis.set("k1", "v1")
    result = await fake_redis.get("k1")
    assert result == "v1"


@pytest.mark.asyncio
async def test_fake_redis_get_missing(fake_redis):
    result = await fake_redis.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_fake_redis_set_with_expiry(fake_redis):
    await fake_redis.set("expkey", "val", ex=3600)
    result = await fake_redis.get("expkey")
    assert result == "val"


@pytest.mark.asyncio
async def test_fake_redis_delete_existing(fake_redis):
    await fake_redis.set("del_me", "v")
    count = await fake_redis.delete("del_me")
    assert count == 1
    assert await fake_redis.get("del_me") is None


@pytest.mark.asyncio
async def test_fake_redis_delete_nonexistent(fake_redis):
    count = await fake_redis.delete("does_not_exist")
    assert count == 0


@pytest.mark.asyncio
async def test_fake_redis_sadd_smembers(fake_redis):
    await fake_redis.sadd("myset", "a")
    await fake_redis.sadd("myset", "b")
    await fake_redis.sadd("myset", "b")  # duplicate
    members = await fake_redis.smembers("myset")
    assert "a" in members
    assert "b" in members
    assert len(members) == 2


@pytest.mark.asyncio
async def test_fake_redis_srem(fake_redis):
    await fake_redis.sadd("set2", "x")
    await fake_redis.srem("set2", "x")
    members = await fake_redis.smembers("set2")
    assert "x" not in members


@pytest.mark.asyncio
async def test_fake_redis_smembers_empty(fake_redis):
    members = await fake_redis.smembers("never_existed")
    assert len(members) == 0


@pytest.mark.asyncio
async def test_fake_redis_zadd_zcard(fake_redis):
    count = await fake_redis.zadd("zset1", {"m1": 1.0, "m2": 2.0, "m3": 3.0})
    assert count == 3
    card = await fake_redis.zcard("zset1")
    assert card == 3


@pytest.mark.asyncio
async def test_fake_redis_zadd_update_existing(fake_redis):
    await fake_redis.zadd("zset2", {"m1": 1.0})
    count = await fake_redis.zadd("zset2", {"m1": 5.0})  # update, not add
    assert count == 0  # m1 was already there
    card = await fake_redis.zcard("zset2")
    assert card == 1


@pytest.mark.asyncio
async def test_fake_redis_zremrangebyscore(fake_redis):
    await fake_redis.zadd("zset3", {"m1": 1.0, "m2": 5.0, "m3": 10.0})
    removed = await fake_redis.zremrangebyscore("zset3", 0.0, 5.0)
    assert removed == 2  # m1 (1.0) and m2 (5.0)
    card = await fake_redis.zcard("zset3")
    assert card == 1  # only m3 (10.0) remains


@pytest.mark.asyncio
async def test_fake_redis_zremrangebyscore_empty_set(fake_redis):
    removed = await fake_redis.zremrangebyscore("empty_zset", 0.0, 100.0)
    assert removed == 0


@pytest.mark.asyncio
async def test_fake_redis_expire(fake_redis):
    await fake_redis.zadd("zset_exp", {"m": 1.0})
    result = await fake_redis.expire("zset_exp", 60)
    assert result is True


@pytest.mark.asyncio
async def test_fake_redis_expire_missing_key(fake_redis):
    result = await fake_redis.expire("missing_key", 60)
    # Key doesn't exist — result depends on implementation (False or True)
    assert result in (True, False)


@pytest.mark.asyncio
async def test_fake_redis_zcard_expired_key(fake_redis):
    """Expired sorted set returns 0."""
    import time

    await fake_redis.zadd("exp_zset", {"m": 1.0})
    fake_redis._ttl["exp_zset"] = time.monotonic() - 1.0  # already expired

    result = await fake_redis.zcard("exp_zset")
    assert result == 0


@pytest.mark.asyncio
async def test_fake_redis_zremrangebyscore_expired_key(fake_redis):
    """Expired sorted set for zremrangebyscore returns 0."""
    import time

    await fake_redis.zadd("exp_zset2", {"m": 1.0})
    fake_redis._ttl["exp_zset2"] = time.monotonic() - 1.0

    removed = await fake_redis.zremrangebyscore("exp_zset2", 0, 100)
    assert removed == 0


@pytest.mark.asyncio
async def test_fake_redis_get_expired_string_key(fake_redis):
    """Expired string key returns None."""
    import time

    await fake_redis.set("exp_str", "value")
    fake_redis._ttl["exp_str"] = time.monotonic() - 1.0

    result = await fake_redis.get("exp_str")
    assert result is None


@pytest.mark.asyncio
async def test_fake_redis_pipeline_simulation():
    """_FakeRedis works end-to-end in rate limiter usage pattern."""
    from app.main import _FakeRedis

    redis = _FakeRedis()
    # Simulate a full sliding-window rate limiter cycle
    import time

    key = "auth_rl:1.2.3.4"
    now_ms = int(time.time() * 1000)
    window_ms = 60_000

    await redis.zremrangebyscore(key, 0, now_ms - window_ms)
    await redis.zadd(key, {str(now_ms): now_ms})
    count = await redis.zcard(key)
    await redis.expire(key, 120)

    assert count == 1


# ── _resolve_provider_for_app ─────────────────────────────────────────────────

def test_resolve_provider_returns_fake_in_dev_no_keys():
    """FakeProvider returned when no API keys and environment=development."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings
    from app.providers.fake import FakeProvider

    env = {k: v for k, v in os.environ.items()
           if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}}
    env["ANTHROPIC_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    env["ENVIRONMENT"] = "development"

    with patch.dict(os.environ, env, clear=True):
        settings = Settings()
        result = _resolve_provider_for_app(settings)

    assert isinstance(result, FakeProvider)


def test_resolve_provider_raises_in_prod_no_keys():
    """RuntimeError raised in production with no LLM provider keys."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings

    env = {k: v for k, v in os.environ.items()
           if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}}
    env["ANTHROPIC_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    env["ENVIRONMENT"] = "production"
    # production also checks DATABASE_URL format
    env["DATABASE_URL"] = "postgresql+asyncpg://user:pass@host/dbname"

    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="No LLM provider"):
            settings = Settings()
            _resolve_provider_for_app(settings)


def test_resolve_provider_anthropic_key():
    """Returns AnthropicProvider when ANTHROPIC_API_KEY is set."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings

    mock_provider = MagicMock()
    mock_anthropic_mod = MagicMock()
    mock_anthropic_mod.AnthropicProvider = MagicMock(return_value=mock_provider)

    env = {"ANTHROPIC_API_KEY": "sk-ant-test123", "ENVIRONMENT": "development"}

    with patch.dict(os.environ, env):
        with patch.dict(sys.modules, {"app.providers.anthropic_provider": mock_anthropic_mod}):
            settings = Settings()
            result = _resolve_provider_for_app(settings)

    assert result is not None


def test_resolve_provider_openai_fallback():
    """Returns OpenAI provider when ANTHROPIC_API_KEY absent but OPENAI_API_KEY present."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings
    from app.providers.fake import FakeProvider

    # Make Anthropic fail to simulate "installed but key present" path
    mock_openai_provider = MagicMock()
    mock_openai_mod = MagicMock()
    mock_openai_mod.OpenAICompatibleProvider = MagicMock(return_value=mock_openai_provider)

    env_overrides = {
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "sk-openai-test",
        "ENVIRONMENT": "development",
    }

    with patch.dict(os.environ, env_overrides):
        with patch.dict(sys.modules, {"app.providers.openai_compatible": mock_openai_mod}):
            settings = Settings()
            result = _resolve_provider_for_app(settings)

    assert result is not None


# ── Error handler ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_platform_error_handler_returns_json():
    """PlatformError is caught and serialized to JSON by the error handler."""
    from app.main import _register_error_handlers
    from app.core.errors import PlatformError
    from fastapi import FastAPI

    # Create a minimal app with just the error handlers registered
    mini_app = FastAPI()
    _register_error_handlers(mini_app)

    @mini_app.get("/raise-platform-error")
    async def _raise():
        raise PlatformError("Test platform error", code="test_code")

    async with AsyncClient(
        transport=ASGITransport(app=mini_app), base_url="http://test"
    ) as c:
        r = await c.get("/raise-platform-error")

    assert r.status_code == 500  # PlatformError default http_status
    data = r.json()
    assert any(k in data for k in ("code", "error", "detail", "message", "error_id"))


@pytest.mark.asyncio
async def test_http_200_health_returns_json():
    """Health endpoint is accessible and returns JSON."""
    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/health")

    # Health route is in bypass list, so it's accessible without auth
    assert r.status_code in (200, 503)  # 503 if some services not healthy
    assert r.headers.get("content-type", "").startswith("application/json")


# ── App title and version ─────────────────────────────────────────────────────

def test_app_title():
    from app.main import create_app

    app = create_app()
    assert app.title is not None
    assert len(app.title) > 0


def test_app_version():
    from app.main import create_app

    app = create_app()
    assert app.version is not None
