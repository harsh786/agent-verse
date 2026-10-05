"""BUILTIN-CREDS (TOOL-01): every built-in handler uses the CALLING connector's
credentials only — never the platform's environment — on a tenant call.

317 of 324 built-in handlers read their vendor credentials with
``os.getenv(...)`` and took no ``credentials`` argument, so MCPClient could not
pass the tenant's own credentials and every tenant's tool call ran with the
platform's tokens (confused deputy).

Guards here:
* static — every dispatched built-in handler accepts ``credentials``, and no
  server module reads ``os.getenv`` / ``os.environ`` directly;
* dynamic — with every env var the handlers know about set to a platform
  sentinel, every tool of every tenant-credentialed built-in is called through
  its dispatch handler, and no outgoing request (HTTP, AWS SDK, DB driver)
  carries a sentinel.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.mcp.servers.credentials import (
    TenantCredentialError,
    in_tenant_scope,
    tenant_getenv,
    tenant_scope,
    with_tenant_credentials,
)
from app.mcp.servers.registry_wiring import get_builtin_server_configs

SERVERS_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "mcp" / "servers"
SENTINEL = "SENTINEL-PLATFORM"
SENTINEL_HOST = "sentinel-platform.invalid"

# Platform-owned, credential-free built-ins: they run on platform config by design.
PLATFORM_OWNED = {"builtin-utility"}


def _configs() -> list[dict[str, Any]]:
    return get_builtin_server_configs()


def _tenant_configs() -> list[dict[str, Any]]:
    return [c for c in _configs() if c["server_id"] not in PLATFORM_OWNED]


# ── static guards ────────────────────────────────────────────────────────────


def test_every_builtin_handler_accepts_credentials() -> None:
    import inspect

    missing = [
        c["server_id"]
        for c in _configs()
        if "credentials" not in inspect.signature(c["handler"]).parameters
    ]
    assert missing == []


def test_every_tenant_builtin_is_dispatched_tenant_scoped() -> None:
    unscoped = [
        c["server_id"]
        for c in _tenant_configs()
        if not getattr(c["handler"], "_tenant_scoped", False)
    ]
    assert unscoped == []


def test_no_server_module_reads_the_process_environment_directly() -> None:
    offenders = []
    for path in sorted(SERVERS_DIR.glob("*_server.py")):
        source = path.read_text()
        if re.search(r"\bos\.(getenv|environ)\b", source):
            offenders.append(path.name)
    assert offenders == [], "use app.mcp.servers.credentials.tenant_getenv"


def test_no_dispatched_module_reads_configuration_at_import_time() -> None:
    """A module-level ``X = tenant_getenv(...)`` is evaluated once, outside any
    tenant call — i.e. from the platform env — and then used for every tenant."""
    import ast
    import sys

    offenders = []
    for module_name in sorted({c["handler"].__module__ for c in _configs()}):
        tree = ast.parse(pathlib.Path(sys.modules[module_name].__file__ or "").read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "tenant_getenv":
                    offenders.append(f"{module_name}:{node.lineno}")
    assert offenders == []


def test_docker_is_not_a_credential_free_builtin() -> None:
    (docker,) = [c for c in _configs() if c["server_id"] == "builtin-docker"]
    assert docker["requires_env"], "the platform Docker daemon must never be a tenant tool"


# ── tenant_getenv semantics ──────────────────────────────────────────────────


def test_tenant_scope_never_falls_back_to_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUBSPOT_API_KEY", SENTINEL)
    assert tenant_getenv("HUBSPOT_API_KEY") == SENTINEL  # platform code path
    with tenant_scope({}):
        assert in_tenant_scope()
        assert tenant_getenv("HUBSPOT_API_KEY") is None
        assert tenant_getenv("HUBSPOT_API_KEY", "") == ""
    with tenant_scope({"api_key": "tenant-key"}):
        assert tenant_getenv("HUBSPOT_API_KEY") == "tenant-key"
    with tenant_scope({"HUBSPOT_API_KEY": "exact"}):
        assert tenant_getenv("HUBSPOT_API_KEY") == "exact"
    assert not in_tenant_scope()


def test_tenant_scope_never_answers_local_file_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    creds = {
        "google_application_credentials": "/etc/platform-sa.json",
        "kubeconfig": "/root/.kube/config",
        "ca_path": "/etc/ssl/private/key.pem",
    }
    with tenant_scope(creds):
        assert tenant_getenv("GOOGLE_APPLICATION_CREDENTIALS") is None
        assert tenant_getenv("KUBECONFIG") is None
        assert tenant_getenv("SPLUNK_CA_PATH") is None


@pytest.mark.parametrize(
    "value",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5:8080",
        "postgresql://u:p@127.0.0.1:5432/app",
        "unix:///var/run/docker.sock",
        "file:///etc/passwd",
    ],
)
def test_tenant_endpoints_are_egress_checked_under_any_key(value: str) -> None:
    with tenant_scope({"some_custom_key": value}), pytest.raises(TenantCredentialError):
        tenant_getenv("ACME_SOME_CUSTOM_KEY")


async def test_blocked_endpoint_becomes_a_tool_error() -> None:
    async def handler(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"url": tenant_getenv("ACME_BASE_URL")}

    wrapped = with_tenant_credentials(handler)
    result = await wrapped("t", {}, credentials={"url": "http://127.0.0.1:9000"})
    assert "egress" in result["error"]


# ── dynamic guard: no platform credential ever leaves on a tenant call ───────


def _env_names() -> set[str]:
    names: set[str] = set()
    for path in SERVERS_DIR.glob("*.py"):
        names |= set(re.findall(r"getenv\(\s*[\"']([A-Z0-9_]+)[\"']", path.read_text()))
    names |= {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_API_KEY",
        "GOOGLE_ACCESS_TOKEN",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "DOCKER_HOST",
    }
    return names


def _sentinel_for(name: str) -> str:
    if name.endswith(("_URL", "_URI", "_HOST", "_ENDPOINT", "_SERVER", "_DSN")):
        return f"https://{SENTINEL_HOST}/{name}"
    return f"{SENTINEL}-{name}"


def _minimal_args(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("parameters") or tool.get("input_schema") or {}
    props = schema.get("properties", {}) or {}
    samples = {
        "string": "x1",
        "integer": 1,
        "number": 1.0,
        "boolean": False,
        "array": ["x1"],
        "object": {"k": "v"},
    }
    return {
        field: samples.get(str((props.get(field) or {}).get("type", "string")), "x1")
        for field in schema.get("required", []) or []
    }


class _Recorder:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, *parts: Any) -> None:
        self.items.append(" ".join(repr(p) for p in parts))

    def leaks(self) -> list[str]:
        return [i for i in self.items if SENTINEL in i or SENTINEL_HOST in i]


@pytest.fixture
def sentinel_world(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    import sys

    import app.net.ssrf_guard as guard

    for name in _env_names():
        monkeypatch.setenv(name, _sentinel_for(name))
    # Tenant endpoints resolve to a public address; nothing touches the network.
    monkeypatch.setattr(guard, "_resolve_host", lambda host: ["93.184.216.34"])
    rec = _Recorder()

    async def _async_send(self: Any, request: httpx.Request, **kw: Any) -> httpx.Response:
        rec.add(str(request.url), dict(request.headers), request.content)
        return httpx.Response(200, json={}, request=request)

    def _sync_send(self: Any, request: httpx.Request, **kw: Any) -> httpx.Response:
        rec.add(str(request.url), dict(request.headers), request.content)
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", _async_send)
    monkeypatch.setattr(httpx.Client, "send", _sync_send)

    def _sdk(label: str) -> Any:
        def _factory(*args: Any, **kwargs: Any) -> Any:
            rec.add(label, args, kwargs)
            raise ConnectionError(f"{label} disabled in test")

        return _factory

    import importlib

    import boto3

    # Import the submodule explicitly: tests that swap a fake ``boto3`` into
    # sys.modules leave the real ``boto3.session`` cached, so a later fresh
    # ``import boto3`` never binds the ``session`` attribute on the package.
    boto3_session = importlib.import_module("boto3.session")

    monkeypatch.setattr(boto3, "client", _sdk("boto3.client"))
    monkeypatch.setattr(boto3, "resource", _sdk("boto3.resource"))
    monkeypatch.setattr(boto3, "Session", _sdk("boto3.Session"))
    monkeypatch.setattr(boto3_session, "Session", _sdk("boto3.session.Session"))
    for mod_name, attr in (
        ("asyncpg", "connect"),
        ("asyncpg", "create_pool"),
        ("redis.asyncio", "from_url"),
        ("pymongo", "MongoClient"),
    ):
        mod = sys.modules.get(mod_name)
        if mod is None:
            try:
                mod = __import__(mod_name, fromlist=[attr])
            except ImportError:
                continue
        monkeypatch.setattr(mod, attr, _sdk(f"{mod_name}.{attr}"))
    for fake in ("aiomysql", "snowflake", "snowflake.connector"):
        stub = MagicMock()
        stub.connect = _sdk(f"{fake}.connect")
        monkeypatch.setitem(sys.modules, fake, stub)
    return rec


TENANT_CREDENTIALS = {
    "token": "tenant-token",
    "api_key": "tenant-api-key",
    "url": "https://tenant.example.com",
    "email": "tenant@example.com",
    "username": "tenant-user",
    "password": "tenant-pass",
}


@pytest.mark.parametrize("credentials", [TENANT_CREDENTIALS, {}], ids=["tenant-creds", "no-creds"])
async def test_no_platform_credential_leaves_on_a_tenant_call(
    sentinel_world: _Recorder, credentials: dict[str, str]
) -> None:
    calls = 0
    for cfg in _tenant_configs():
        for tool in cfg["tool_definitions"]:
            calls += 1
            try:
                await asyncio.wait_for(
                    cfg["handler"](
                        tool["name"], _minimal_args(tool), credentials=dict(credentials)
                    ),
                    timeout=5,
                )
            except Exception:  # handler errors are fine; only leaks matter
                pass
    assert calls > 2000
    leaks = sentinel_world.leaks()
    assert leaks == [], f"{len(leaks)} outgoing requests carried platform credentials: {leaks[:5]}"


async def test_tenant_credentials_reach_the_vendor_request(sentinel_world: _Recorder) -> None:
    (hubspot,) = [c for c in _configs() if c["server_id"] == "builtin-hubspot"]

    await hubspot["handler"]("hubspot_list_contacts", {}, credentials={"api_key": "tenant-hub-key"})

    assert any("tenant-hub-key" in item for item in sentinel_world.items)
    assert sentinel_world.leaks() == []


async def test_aws_builtin_refuses_without_tenant_keys(sentinel_world: _Recorder) -> None:
    (s3,) = [c for c in _configs() if c["server_id"] == "builtin-aws-s3"]

    result = await s3["handler"]("s3_list_buckets", {}, credentials={"token": "x"})

    assert "error" in result
    assert not any(item.startswith("'boto3") for item in sentinel_world.items)


# ── end to end through MCPClient ─────────────────────────────────────────────


def _postgres_cfg(url: str) -> Any:
    from app.mcp.registry import MCPServerConfig

    (pg,) = [c for c in _configs() if c["server_id"] == "builtin-postgres"]
    return MCPServerConfig(
        server_id="builtin-postgres", name="PostgreSQL", url=url, builtin_handler=pg["handler"]
    )


@pytest.mark.parametrize(
    ("url", "reaches_driver"),
    [
        ("postgresql://tenant:pw@8.8.8.8:5432/tenant_db", True),
        ("postgresql://tenant:pw@10.0.0.5:5432/platform_db", False),
        ("postgresql://tenant:pw@8.8.8.8:5432,127.0.0.1:5432/db", False),
    ],
)
async def test_client_dispatches_tenant_dsn_host_checked(
    sentinel_world: _Recorder, url: str, reaches_driver: bool
) -> None:
    from unittest.mock import AsyncMock

    from app.mcp.client import MCPClient
    from app.tenancy.context import PlanTier, TenantContext

    tenant = TenantContext(tenant_id="t-pg", plan=PlanTier.PROFESSIONAL, api_key_id="k")
    cfg = _postgres_cfg(url)

    result = await MCPClient(registry=AsyncMock())._call_tool_impl(
        cfg, cfg.server_id, "postgres_query", {"sql": "SELECT 1"}, tenant
    )

    assert result.success is False  # the driver is stubbed out
    driver_calls = [i for i in sentinel_world.items if i.startswith("'asyncpg.connect'")]
    assert bool(driver_calls) is reaches_driver
    if reaches_driver:
        assert "tenant_db" in driver_calls[0]
    assert sentinel_world.leaks() == []


async def test_client_refuses_connector_without_credentials(sentinel_world: _Recorder) -> None:
    from unittest.mock import AsyncMock

    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPServerConfig
    from app.tenancy.context import PlanTier, TenantContext

    (hub,) = [c for c in _configs() if c["server_id"] == "builtin-hubspot"]
    cfg = MCPServerConfig(
        server_id="builtin-hubspot",
        name="HubSpot",
        base_url="builtin://",
        builtin_handler=hub["handler"],
    )
    tenant = TenantContext(tenant_id="t-hub", plan=PlanTier.PROFESSIONAL, api_key_id="k")

    result = await MCPClient(registry=AsyncMock())._call_tool_impl(
        cfg, cfg.server_id, "hubspot_list_contacts", {}, tenant
    )

    assert result.success is False
    assert "configure credentials" in (result.error or "").lower()
    assert sentinel_world.items == []


async def test_startup_removes_the_old_platform_docker_connector() -> None:
    """Tenants provisioned while Docker was credential-free carry a
    credential-less ``builtin-docker`` entry: startup wiring removes it."""
    from app.mcp.registry import MCPServerConfig
    from app.mcp.servers.registry_wiring import register_builtin_servers
    from app.tenancy.context import PlanTier, TenantContext

    rows: dict[str, MCPServerConfig] = {}

    class _Reg:
        async def register(self, cfg: MCPServerConfig, *, tenant_ctx: Any) -> str:
            rows[cfg.server_id] = cfg
            return cfg.server_id

        async def get(self, server_id: str, *, tenant_ctx: Any) -> MCPServerConfig | None:
            return rows.get(server_id)

        async def unregister(self, server_id: str, *, tenant_ctx: Any) -> bool:
            return rows.pop(server_id, None) is not None

    rows["builtin-docker"] = MCPServerConfig(
        server_id="builtin-docker", name="Docker", base_url="builtin://"
    )
    tenant = TenantContext(tenant_id="t-old", plan=PlanTier.FREE, api_key_id="k")

    await register_builtin_servers(_Reg(), tenant)

    assert "builtin-docker" not in rows
    assert "builtin-utility" in rows
