"""A workflow tool step resolves the connector credential the API stored (real PG + Redis).

Owner report: a workflow run's ``send_email`` step (tool, connector ``gmail``)
failed in the Celery workflow worker with "Could not resolve the credential
'token' for connector 'gmail'; re-enter the connector's credentials." although
the token had been entered at registration.

The flow here is the production one, split the way the processes split it:

* **API side** — the connector is registered through ``POST /connectors`` exactly
  as the UI does (durable ``DurableConnectorSecretStore`` + Postgres-backed
  registry, as the lifespan wires them); the run row is created by the run store.
* **Worker side** — Redis is flushed (nothing shared but Postgres) and the run is
  executed by ``celery_tasks._build_worker_runner()`` + ``execute_fresh`` on a
  fresh loop via ``_run_async``, i.e. the stores, registry, secret resolver and
  OAuth manager are all built the way a freshly started workflow worker builds
  them, from ``DATABASE_URL`` / ``REDIS_URL`` (a NOBYPASSRLS app role).

The ONLY stand-ins are at the network edge, kept in ``_GmailEdge`` /
``_TokenEndpointEdge``: the Gmail HTTP endpoint (the built-in handler's
``httpx.AsyncClient`` gets a ``MockTransport``) and the OAuth token endpoint
(``ssrf_guard.request_public``). Everything up to the outgoing request is real.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration]

TOKEN = "ya29.workflow-gmail-token-123"
MASTER_KEY_A = "wf-creds-master-key-A-0123456789abcdef"
MASTER_KEY_B = "wf-creds-master-key-B-fedcba9876543210"


# ── network edge stand-ins (nothing else is faked) ──────────────────────────


class _GmailEdge:
    """The Gmail REST endpoint: records what the built-in handler sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization", "").startswith("Bearer "):
            return httpx.Response(
                200, json={"id": "msg-1", "threadId": "thr-1", "labelIds": ["SENT"]}
            )
        return httpx.Response(401, json={"error": {"code": 401, "message": "no token"}})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.mcp.servers import gmail_server

        transport = httpx.MockTransport(self._handle)
        real_client = httpx.AsyncClient

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        monkeypatch.setattr(
            gmail_server,
            "httpx",
            SimpleNamespace(AsyncClient=_client, HTTPStatusError=httpx.HTTPStatusError),
        )


class _TokenEndpointEdge:
    """The OAuth provider's token endpoint (refresh grant)."""

    def __init__(self, new_access_token: str) -> None:
        self.new_access_token = new_access_token
        self.posts: list[dict[str, Any]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.net.ssrf_guard as guard

        async def _request_public(
            client: Any, method: str, url: str, **kwargs: Any
        ) -> httpx.Response:
            self.posts.append(
                {"method": method, "url": url, "data": dict(kwargs.get("data") or {})}
            )
            return httpx.Response(
                200,
                json={
                    "access_token": self.new_access_token,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
                request=httpx.Request(method, url),
            )

        monkeypatch.setattr(guard, "request_public", _request_public)


# ── backends ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def creds_db(pg_url: str) -> Iterator[tuple[str, str]]:
    """An isolated migrated database and an RLS-bound app-role DSN: (admin, app)."""
    from app.db.app_role import AppRoleSpec, ensure_app_role
    from tests._test_backends import fresh_migrated_database

    role = f"wf_creds_app_{secrets.token_hex(4)}"
    with fresh_migrated_database(pg_url) as admin_url:
        password = secrets.token_urlsafe(18)

        async def _bootstrap() -> None:
            engine = create_async_engine(admin_url)
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(ensure_app_role, AppRoleSpec(role=role, password=password))
            finally:
                await engine.dispose()

        asyncio.run(_bootstrap())
        app_url = (
            make_url(admin_url)
            .set(username=role, password=password)
            .render_as_string(hide_password=False)
        )
        yield admin_url, app_url


@pytest.fixture
def backends(
    creds_db: tuple[str, str], redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, str, str]]:
    """Point the process (settings, global engines, REDIS_URL) at the containers."""
    import app.scaling.celery_app as celery_app_mod
    import app.scaling.tasks as tasks_mod
    import app.workflow.celery_tasks as ct
    from app.providers.tenant_vault import invalidate_tenant_vault
    from app.providers.vault_canary import reset_last_canary_result
    from tests._dns import stub_public_dns
    from tests._test_backends import reset_db_singletons

    admin_url, app_url = creds_db
    monkeypatch.setenv("DATABASE_URL", app_url)
    monkeypatch.setenv("MAINTENANCE_DATABASE_URL", admin_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setattr(tasks_mod, "REDIS_URL", redis_url, raising=False)
    monkeypatch.setattr(celery_app_mod, "REDIS_URL", redis_url, raising=False)
    for name in ("AGENTVERSE_VAULT_KEY", "AGENTVERSE_VAULT_KEY_FILE", "VAULT_MASTER_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VAULT_MASTER_KEY", MASTER_KEY_A)
    # Registration and dispatch resolve gmail.googleapis.com for the SSRF guard;
    # no real DNS (or network) in a test.
    stub_public_dns(monkeypatch)
    monkeypatch.setattr(ct, "_WORKER_RUNNER", None)
    reset_db_singletons()
    invalidate_tenant_vault()
    reset_last_canary_result()
    _admin_sql(admin_url, "DELETE FROM vault_key_canary")
    yield admin_url, app_url, redis_url
    monkeypatch.setattr(ct, "_WORKER_RUNNER", None)
    reset_db_singletons()
    invalidate_tenant_vault()
    reset_last_canary_result()


def _admin_sql(admin_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    async def _go() -> list[Any]:
        engine = create_async_engine(admin_url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params or {})
                return list(result.mappings().all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def _flush_redis(redis_url: str) -> None:
    import redis as sync_redis

    client = sync_redis.Redis.from_url(redis_url)
    try:
        client.flushall()
    finally:
        client.close()


# ── API side: what the UI and the API process do ────────────────────────────


def _ctx(tenant: str) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _api_register_and_create_run(
    app_url: str,
    admin_url: str,
    redis_url: str,
    *,
    tenant: str,
    connector: dict[str, Any],
    before_run: Any = None,
) -> tuple[str, str, str]:
    """Register the connector via POST /connectors and create a pending run.

    Returns ``(server_id, workflow_id, run_id)``.
    """

    async def _go() -> tuple[str, str, str]:
        import redis.asyncio as aioredis
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.connectors import router as connectors_router
        from app.mcp.connector_secrets import DurableConnectorSecretStore
        from app.mcp.registry import MCPRegistry
        from app.providers.vault_canary import publish_vault_canary
        from app.tenancy.middleware import TenantMiddleware

        engine = create_async_engine(app_url)
        redis = aioredis.from_url(redis_url, decode_responses=True)
        app_db = async_sessionmaker(engine, expire_on_commit=False)
        try:
            # The API lifespan: vault canary, Postgres registry, durable secrets.
            canary = await publish_vault_canary(app_db, role="api")
            assert canary.ok, canary.message
            app = FastAPI()

            async def _resolve(key: str) -> Any:
                return _ctx(tenant) if key == "k" else None

            app.add_middleware(TenantMiddleware, key_resolver=_resolve)
            app.include_router(connectors_router)
            app.state.mcp_registry = MCPRegistry(redis, db_factory=app_db)
            app.state.connector_secret_store = DurableConnectorSecretStore(
                db_factory=app_db, redis=redis
            )
            app.state.connector_secret_store_is_production_safe = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://api"
            ) as client:
                created = await client.post(
                    "/connectors", headers={"X-API-Key": "k"}, json=connector
                )
            assert created.status_code == 201, created.text
            server_id = str(created.json()["server_id"])
            if before_run is not None:
                await before_run(app_db, redis, server_id)

            return server_id, str(uuid.uuid4()), str(uuid.uuid4())
        finally:
            await redis.aclose()
            await engine.dispose()

    server_id, workflow_id, run_id = asyncio.run(_go())
    # The definition row (admin: the builder's create path is not under test).
    _admin_sql(
        admin_url,
        "INSERT INTO workflow_definitions (id, tenant_id, name, slug, definition_json) "
        "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), 'email-on-approval', :slug, "
        "CAST(:d AS jsonb))",
        {
            "id": workflow_id,
            "t": tenant,
            "slug": f"email-{workflow_id[:8]}",
            "d": json.dumps(_definition(workflow_id, server_id)),
        },
    )

    async def _create_run() -> None:
        from app.workflow.run_store import PostgresWorkflowRunStore

        engine = create_async_engine(app_url)
        try:
            await PostgresWorkflowRunStore(
                async_sessionmaker(engine, expire_on_commit=False)
            ).create(
                run_id=run_id,
                workflow_id=workflow_id,
                tenant_id=tenant,
                inputs={"message": "The refund was approved."},
            )
        finally:
            await engine.dispose()

    asyncio.run(_create_run())
    return server_id, workflow_id, run_id


def _definition(workflow_id: str, server_id: str) -> dict[str, Any]:
    """The owner's shape: a message is prepared, then sent by a Gmail tool step."""
    return {
        "id": workflow_id,
        "name": "email-on-approval",
        "steps": [
            {"id": "create_message", "type": "transform"},
            {
                "id": "send_email",
                "type": "tool",
                "depends_on": ["create_message"],
                "server_id": server_id,
                "tool": "gmail_send_message",
                "input": {
                    "to": "ops@example.com",
                    "subject": "Approved",
                    "body": "{{inputs.message}}",
                },
            },
        ],
    }


# ── worker side: exactly what execute_workflow_run does ─────────────────────


def _worker_execute(run_id: str, workflow_id: str, tenant: str) -> None:
    import app.workflow.celery_tasks as ct

    ct._WORKER_RUNNER = None
    runner = ct._build_worker_runner()
    ct._run_async(runner.execute_fresh(run_id, workflow_id, tenant))


def _run_row(admin_url: str, run_id: str) -> dict[str, Any]:
    rows = _admin_sql(
        admin_url,
        "SELECT status, error FROM workflow_runs WHERE id = CAST(:r AS uuid)",
        {"r": run_id},
    )
    steps = _admin_sql(
        admin_url,
        "SELECT step_id, status, error, output FROM workflow_step_results "
        "WHERE run_id = CAST(:r AS uuid) ORDER BY started_at NULLS LAST",
        {"r": run_id},
    )
    return {**dict(rows[0]), "steps": {s["step_id"]: dict(s) for s in steps}}


def _gmail_connector(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "gmail",
        "type": "gmail",
        "url": "https://gmail.googleapis.com/gmail/v1",
        "auth_type": "bearer",
        "auth_config": {"token": TOKEN},
        # The tool is write_high; the owner's connector allows it without a
        # human gate, so the run reaches the dispatch (as in the report).
        "auto_approve": True,
    }
    body.update(overrides)
    return body


def test_worker_tool_step_sends_the_token_entered_at_registration(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_url, app_url, redis_url = backends
    tenant = uuid.uuid4().hex
    server_id, workflow_id, run_id = _api_register_and_create_run(
        app_url, admin_url, redis_url, tenant=tenant, connector=_gmail_connector()
    )
    # At rest the token exists ONLY as a sealed mcp_credentials row.
    creds = _admin_sql(
        admin_url,
        "SELECT server_id, secret_key, encrypted_value FROM mcp_credentials WHERE tenant_id = :t",
        {"t": tenant},
    )
    assert [(c["server_id"], c["secret_key"]) for c in creds] == [(server_id, "token")]
    assert TOKEN not in creds[0]["encrypted_value"]

    # A fresh worker shares nothing with the API but Postgres.
    _flush_redis(redis_url)
    gmail = _GmailEdge()
    gmail.install(monkeypatch)
    _worker_execute(run_id, workflow_id, tenant)

    run = _run_row(admin_url, run_id)
    assert run["status"] == "complete", run
    assert len(gmail.requests) == 1
    sent = gmail.requests[0]
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.url.path == "/gmail/v1/users/me/messages/send"
    output = run["steps"]["send_email"]["output"]
    output = json.loads(output) if isinstance(output, str) else output
    assert output["success"] is True
    assert output["output"]["id"] == "msg-1"


def test_worker_with_another_vault_key_names_vault_master_key(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker on another VAULT_MASTER_KEY: the failure says so (no 're-enter')."""
    from app.providers.vault import _derive_fernet_key, _key_fingerprint

    admin_url, app_url, redis_url = backends
    tenant = uuid.uuid4().hex
    server_id, workflow_id, run_id = _api_register_and_create_run(
        app_url, admin_url, redis_url, tenant=tenant, connector=_gmail_connector()
    )
    _flush_redis(redis_url)
    gmail = _GmailEdge()
    gmail.install(monkeypatch)
    monkeypatch.setenv("VAULT_MASTER_KEY", MASTER_KEY_B)  # this worker's (wrong) key
    _worker_execute(run_id, workflow_id, tenant)

    run = _run_row(admin_url, run_id)
    # The step failed (not retried: retrying on the same key cannot open it);
    # the default on_failure pauses the run for an operator.
    assert run["status"] == "paused", run
    assert run["steps"]["send_email"]["status"] == "failed", run
    error = run["steps"]["send_email"]["error"] or ""
    assert gmail.requests == []  # nothing was sent, with or without a token
    assert "Could not resolve the credential 'token' for connector 'gmail'" in error
    assert "VAULT_MASTER_KEY" in error
    assert "re-enter the connector's credentials" not in error.lower()
    assert "does not help" in error
    # Both sides are named by fingerprint (never the key itself).
    assert _key_fingerprint(_derive_fernet_key(MASTER_KEY_A)) in error
    assert _key_fingerprint(_derive_fernet_key(MASTER_KEY_B)) in error
    assert MASTER_KEY_A not in error and MASTER_KEY_B not in error


def test_worker_tool_step_refreshes_an_expired_oauth_token(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """OAuth connector: the workflow worker reads oauth_tokens and refreshes it."""
    from app.mcp.oauth import OAuthToken, build_worker_oauth_manager

    admin_url, app_url, redis_url = backends
    tenant = uuid.uuid4().hex

    async def _seed_expired_token(app_db: Any, redis: Any, server_id: str) -> None:
        # What /connectors/oauth/callback stores after the user authorized.
        mgr = build_worker_oauth_manager(app_db, redis=redis)
        assert mgr is not None
        await mgr._persist_token_to_db(
            tenant, server_id, OAuthToken(access_token="ya29.expired", refresh_token="1//rt-1")
        )

    connector = _gmail_connector(
        auth_type="oauth_ac",
        auth_config={
            "client_id": "client-123.apps.googleusercontent.com",
            "client_secret": "gocspx-client-secret",
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
        },
    )
    server_id, workflow_id, run_id = _api_register_and_create_run(
        app_url,
        admin_url,
        redis_url,
        tenant=tenant,
        connector=connector,
        before_run=_seed_expired_token,
    )
    _admin_sql(
        admin_url,
        "UPDATE oauth_tokens SET expires_at = NOW() - INTERVAL '1 hour' "
        "WHERE tenant_id = :t AND server_id = :s",
        {"t": tenant, "s": server_id},
    )
    _flush_redis(redis_url)
    gmail = _GmailEdge()
    gmail.install(monkeypatch)
    token_endpoint = _TokenEndpointEdge("ya29.refreshed")
    token_endpoint.install(monkeypatch)
    _worker_execute(run_id, workflow_id, tenant)

    run = _run_row(admin_url, run_id)
    assert run["status"] == "complete", run
    assert len(token_endpoint.posts) == 1
    refresh = token_endpoint.posts[0]
    assert refresh["url"] == "https://oauth2.googleapis.com/token"
    assert refresh["data"]["grant_type"] == "refresh_token"
    assert refresh["data"]["refresh_token"] == "1//rt-1"
    # The confidential client's secret, resolved from its vault reference.
    assert refresh["data"]["client_secret"] == "gocspx-client-secret"
    assert [r.headers["authorization"] for r in gmail.requests] == ["Bearer ya29.refreshed"]
    # Persisted for the API and other workers.
    rows = _admin_sql(
        admin_url,
        "SELECT expires_at > NOW() AS fresh FROM oauth_tokens "
        "WHERE tenant_id = :t AND server_id = :s",
        {"t": tenant, "s": server_id},
    )
    assert rows and rows[0]["fresh"] is True
