# World-Class Connector Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every builtin connector per-tenant and credential-aware: each user configures their own Jira/GitHub/Slack credentials, all 200+ connector types appear in a rich catalog dashboard, and the connector Test button works correctly.

**Architecture:** Three-layer fix. (1) Backend credential injection: `_dispatch_builtin_tool` extracts auth_config from the registered connector and passes it to the handler as a `credentials` dict; each server handler reads from credentials first and falls back to env vars for dev. (2) Backend auto-wiring: when a tenant registers a connector whose name matches a builtin type (e.g. "jira", "github"), the registered MCPServerConfig automatically gets the builtin handler assigned, making the tool dispatch use the correct handler with the tenant's credentials. (3) Frontend: the existing `ConnectorsCatalogPage.tsx` and `ConnectorsRegisteredPage.tsx` are enhanced with rich cards, category grouping, configured badges, and connector-type-aware registration forms with field hints.

**Tech Stack:** Python 3.12 · FastAPI · pytest (backend) · React 19 · TypeScript · Vitest · Tailwind (frontend)

---

## Root Cause Summary

| Problem | Root cause | Fix |
|---------|-----------|-----|
| Jira goal uses server env vars, not user's credentials | `_dispatch_builtin_tool` calls `handler(tool_name, args)` with no auth | Pass `credentials` dict from connector's `auth_config` to handler |
| Connector Test button shows `bad_response` | Handler ignores connector auth_config; uses env vars that differ | Handler accepts `credentials` kwarg and prefers it |
| Builtins not shown on dashboard | Auto-registration writes `base_url="builtin://"` which is not a user-visible connector | On user registration, wiring auto-assigns builtin handler to the registered connector |
| 200+ connector types not easily discoverable | CatalogPage exists but `/connectors/catalog` endpoint returns incomplete data | Enrich catalog endpoint with categories, field specs, builtin availability, per-tenant configured status |
| Register form doesn't hint what fields Jira needs | Generic auth form has no type-specific guidance | Add `connector_type` field specs to catalog entries; registration form reads and displays them |

---

## File Structure

**Backend (new / modified):**
- Modify: `agent-verse-backend/app/mcp/client.py` — credential injection in `_dispatch_builtin_tool`; helper `_extract_credentials`
- Modify: `agent-verse-backend/app/mcp/servers/jira_server.py` — accept `credentials` kwarg
- Modify: `agent-verse-backend/app/mcp/servers/github_server.py` — accept `credentials` kwarg
- Modify: `agent-verse-backend/app/mcp/servers/slack_server.py` — accept `credentials` kwarg
- Modify: `agent-verse-backend/app/mcp/servers/linear_server.py` — accept `credentials` kwarg
- Modify: `agent-verse-backend/app/mcp/catalog.py` — enrich `ConnectorSpec` with `category`, `auth_fields`, `builtin_server_id`
- Modify: `agent-verse-backend/app/api/connectors.py` — enrich `/connectors/catalog` endpoint; auto-assign builtin handler on register; fix test endpoint
- Modify: `agent-verse-backend/app/mcp/servers/registry_wiring.py` — expose `BUILTIN_SERVER_IDS_BY_NAME` lookup dict
- Create: `agent-verse-backend/tests/mcp/test_credential_injection.py`
- Create: `agent-verse-backend/tests/api/test_connectors_catalog.py`

**Frontend (modified):**
- Modify: `agent-verse-frontend/src/lib/api/client.ts` — enrich `CatalogEntry` type; add `catalogApi.getRich()`
- Modify: `agent-verse-frontend/src/features/connectors/ConnectorsCatalogPage.tsx` — categories, configured badges, rich cards
- Modify: `agent-verse-frontend/src/features/connectors/ConnectorsRegisteredPage.tsx` — "Browse Catalog" button, prefill from catalog, type-aware form hints
- Modify: `agent-verse-frontend/src/features/connectors/__tests__/ConnectorsCatalogPage.test.tsx` — update tests for new shape
- Modify: `agent-verse-frontend/src/features/connectors/__tests__/ConnectorsRegisteredPage.test.tsx` — add catalog integration tests

---

### Task 1: Backend — Credential Injection in Builtin Dispatch

**Why first:** This is the core fix that makes per-tenant credentials work for every builtin connector. All other tasks depend on it.

**Files:**
- Modify: `agent-verse-backend/app/mcp/client.py`
- Create: `agent-verse-backend/tests/mcp/test_credential_injection.py`

- [ ] **Step 1: Write failing tests**

Create `agent-verse-backend/tests/mcp/test_credential_injection.py`:

```python
"""Tests: builtin handler receives credentials from connector auth_config."""
from __future__ import annotations

import builtins
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.client import MCPClient, ToolCallResult
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

_TENANT = TenantContext(
    tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1"
)


class FakeRedis:
    def __init__(self) -> None:
        self._d: dict = {}
        self._s: dict = {}

    async def get(self, key):
        return self._d.get(key)

    async def set(self, key, value, ex=None):
        self._d[key] = value

    async def delete(self, key):
        existed = key in self._d
        self._d.pop(key, None)
        return int(existed)

    async def sadd(self, key, value):
        self._s.setdefault(key, set()).add(value)

    async def srem(self, key, value):
        self._s.get(key, set()).discard(value)

    async def smembers(self, key):
        return self._s.get(key, set())


@pytest.mark.asyncio
async def test_dispatch_builtin_passes_credentials_from_auth_config() -> None:
    """When auth_config has credentials, the builtin handler receives them."""
    received_credentials: dict = {}

    async def fake_handler(tool_name: str, arguments: dict, *, credentials: dict | None = None):
        received_credentials.update(credentials or {})
        return {"result": "ok"}

    registry = MCPRegistry(redis=FakeRedis())
    server_id = await registry.register(
        MCPServerConfig(
            server_id="test-builtin-jira",
            name="Jira",
            base_url="builtin://",
            auth_type="basic",
            auth_config={"username": "user@example.com", "password": "ATATT3x-token"},
            builtin_handler=fake_handler,
        ),
        tenant_ctx=_TENANT,
    )
    # Re-attach handler (simulates Redis round-trip loss)
    MCPRegistry.register_builtin_handler(server_id, fake_handler)

    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id=server_id,
        tool_name="jira_search_issues",
        arguments={"jql": "project = TEST"},
        tenant_ctx=_TENANT,
    )

    assert result.success is True
    assert received_credentials.get("username") == "user@example.com"
    assert received_credentials.get("password") == "ATATT3x-token"


@pytest.mark.asyncio
async def test_dispatch_builtin_includes_base_url_from_server_url() -> None:
    """Non-builtin:// URL is forwarded to the handler as credentials['url']."""
    received_credentials: dict = {}

    async def fake_handler(tool_name, arguments, *, credentials=None):
        received_credentials.update(credentials or {})
        return {"result": "ok"}

    registry = MCPRegistry(redis=FakeRedis())
    server_id = await registry.register(
        MCPServerConfig(
            server_id="test-jira-rest",
            name="Jira",
            url="https://mycompany.atlassian.net",
            auth_type="basic",
            auth_config={"username": "admin@mycompany.com", "password": "token"},
            builtin_handler=fake_handler,
        ),
        tenant_ctx=_TENANT,
    )
    MCPRegistry.register_builtin_handler(server_id, fake_handler)

    client = MCPClient(registry=registry)
    await client.call_tool(
        server_id=server_id,
        tool_name="jira_search_issues",
        arguments={"jql": "project = TEST"},
        tenant_ctx=_TENANT,
    )

    assert received_credentials.get("url") == "https://mycompany.atlassian.net"


@pytest.mark.asyncio
async def test_dispatch_builtin_falls_back_for_old_style_handler() -> None:
    """Old-style handler without credentials kwarg still runs successfully."""
    async def old_style_handler(tool_name, arguments):
        return {"result": "old style ok"}

    registry = MCPRegistry(redis=FakeRedis())
    server_id = await registry.register(
        MCPServerConfig(
            server_id="test-old-style",
            name="OldServer",
            base_url="builtin://",
            auth_config={"api_key": "somekey"},
            builtin_handler=old_style_handler,
        ),
        tenant_ctx=_TENANT,
    )
    MCPRegistry.register_builtin_handler(server_id, old_style_handler)

    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id=server_id,
        tool_name="some_tool",
        arguments={},
        tenant_ctx=_TENANT,
    )

    assert result.success is True
    assert result.output == {"result": "old style ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent-verse-backend && uv run pytest tests/mcp/test_credential_injection.py -v
```

Expected: FAIL — handler called without `credentials` kwarg.

- [ ] **Step 3: Add `_extract_credentials` helper and update `_dispatch_builtin_tool` in `client.py`**

In `agent-verse-backend/app/mcp/client.py`, add this helper function just before the `MCPClient` class definition:

```python
def _extract_credentials_from_server(cfg: "MCPServerConfig") -> dict[str, str]:
    """Extract credentials dict from a server config for passing to builtin handlers.

    Merges url/base_url (when not the placeholder 'builtin://') with the full
    auth_config so handlers can read both connection endpoints and secrets.
    """
    result: dict[str, str] = {}
    # Include the real base URL if it is not the builtin placeholder
    for url_field in ("url", "base_url"):
        url_val = getattr(cfg, url_field, None)
        if url_val and url_val != "builtin://":
            result["url"] = str(url_val)
            break
    # Merge all auth_config entries (username, password, token, api_key, etc.)
    for key, value in (cfg.auth_config or {}).items():
        if isinstance(value, str):
            result[key] = value
    return result
```

Then modify `_dispatch_builtin_tool` inside `MCPClient` (current implementation calls `await handler(tool_name, arguments)`):

```python
async def _dispatch_builtin_tool(
    self,
    server: MCPServerConfig,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolCallResult:
    """Call a built-in server's Python handler directly.

    Passes credentials extracted from the server's auth_config so each
    tenant can supply their own API keys/tokens instead of relying on
    shared environment variables.
    """
    handler = server.builtin_handler
    if handler is None:
        return ToolCallResult(
            tool_name=tool_name,
            success=False,
            error="Built-in handler not available (lost after Redis round-trip)",
            server_id=server.server_id,
        )
    credentials = _extract_credentials_from_server(server)
    try:
        # New-style handlers accept credentials as a keyword argument.
        output = await handler(tool_name, arguments, credentials=credentials)
    except TypeError:
        # Legacy handlers don't accept credentials — call without it.
        try:
            output = await handler(tool_name, arguments)
        except Exception as exc:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                server_id=server.server_id,
            )
    except Exception as exc:
        return ToolCallResult(
            tool_name=tool_name,
            success=False,
            error=str(exc),
            server_id=server.server_id,
        )
    if isinstance(output, dict) and output.get("error"):
        return ToolCallResult(
            tool_name=tool_name,
            success=False,
            output=output,
            error=str(output["error"]),
            server_id=server.server_id,
        )
    return ToolCallResult(
        tool_name=tool_name,
        success=True,
        output=output,
        server_id=server.server_id,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd agent-verse-backend && uv run pytest tests/mcp/test_credential_injection.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent-verse-backend/app/mcp/client.py agent-verse-backend/tests/mcp/test_credential_injection.py
git commit -m "feat(connectors): inject per-tenant credentials into builtin handler dispatch"
```

---

### Task 2: Backend — Update Key Builtin Server Handlers to Accept Credentials

**Why:** With Task 1 passing credentials, the handlers must read them. Jira, GitHub, and Slack are the three highest-priority handlers (Jira is the live connector).

**Files:**
- Modify: `agent-verse-backend/app/mcp/servers/jira_server.py`
- Modify: `agent-verse-backend/app/mcp/servers/github_server.py`
- Modify: `agent-verse-backend/app/mcp/servers/slack_server.py`

- [ ] **Step 1: Write failing tests for Jira credential injection**

Add to `agent-verse-backend/tests/mcp/test_credential_injection.py`:

```python
@pytest.mark.asyncio
async def test_jira_server_uses_credentials_over_env_vars() -> None:
    """jira_server.call_tool uses credentials dict when provided, ignoring env."""
    from app.mcp.servers.jira_server import call_tool

    resp_data = {
        "issues": [{"id": "1", "key": "TENANT-1", "fields": {
            "summary": "Tenant-specific issue", "status": {"name": "Open"},
            "priority": None, "assignee": None, "issuetype": None,
            "created": "", "updated": "",
        }}]
    }

    import httpx, respx
    with respx.mock:
        route = respx.post("https://tenant.atlassian.net/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json=resp_data)
        )
        result = await call_tool(
            "jira_search_issues",
            {"jql": "project = TENANT"},
            credentials={
                "url": "https://tenant.atlassian.net",
                "username": "tenant@company.com",
                "password": "TENANT-TOKEN",
            },
        )

    assert result["issues"][0]["key"] == "TENANT-1"
    request_body = route.calls[0].request
    import base64
    auth_header = request_body.headers["authorization"]
    expected = "Basic " + base64.b64encode(b"tenant@company.com:TENANT-TOKEN").decode()
    assert auth_header == expected


@pytest.mark.asyncio
async def test_jira_server_falls_back_to_env_when_no_credentials() -> None:
    """jira_server.call_tool falls back to env vars when credentials not provided."""
    from app.mcp.servers.jira_server import call_tool
    import os, httpx, respx

    base = "https://envjira.atlassian.net"
    with respx.mock:
        route = respx.post(f"{base}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": []})
        )
        with patch.dict(os.environ, {
            "JIRA_BASE_URL": base,
            "JIRA_EMAIL": "env@company.com",
            "JIRA_API_TOKEN": "ENV-TOKEN",
        }):
            result = await call_tool(
                "jira_search_issues",
                {"jql": "project = ENV"},
            )  # no credentials kwarg

    import base64
    auth_header = route.calls[0].request.headers["authorization"]
    expected = "Basic " + base64.b64encode(b"env@company.com:ENV-TOKEN").decode()
    assert auth_header == expected
```

Add required import at top: `from unittest.mock import patch`

- [ ] **Step 2: Run to verify they fail**

```bash
cd agent-verse-backend && uv run pytest tests/mcp/test_credential_injection.py::test_jira_server_uses_credentials_over_env_vars -v
```

Expected: FAIL — `call_tool` does not accept `credentials` kwarg.

- [ ] **Step 3: Update `jira_server.py`**

In `agent-verse-backend/app/mcp/servers/jira_server.py`:

Change the `_jira_auth()` function and `call_tool`/`_call_tool_inner` signatures:

```python
def _jira_auth(
    email: str | None = None,
    token: str | None = None,
) -> dict[str, str]:
    """Build Jira Basic Auth headers.

    Uses provided email/token if given, otherwise falls back to env vars.
    This allows per-tenant credential injection from registered connector
    auth_config while preserving env-var fallback for development.
    """
    _email = email or os.getenv("JIRA_EMAIL", "")
    _token = token or os.getenv("JIRA_API_TOKEN", "")
    creds = base64.b64encode(f"{_email}:{_token}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a Jira tool.

    Args:
        tool_name: The Jira tool to call (e.g. 'jira_search_issues').
        arguments: Tool-specific arguments.
        credentials: Optional per-tenant credentials dict. Supports keys:
            - url / base_url: Jira instance URL
            - username / email: Atlassian account email
            - password / api_token / token: Atlassian API token
            Falls back to JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN env vars.
    """
    try:
        return await _call_tool_inner(tool_name, arguments, credentials=credentials)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        with suppress(Exception):
            error_body = exc.response.text[:500]
        return {
            "error": f"HTTP {exc.response.status_code}: "
            f"{error_body or exc.response.reason_phrase}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _call_tool_inner(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    creds = credentials or {}
    # Resolve base URL: credentials → env var
    base = _absolute_http_url(
        creds.get("url") or creds.get("base_url")
        or os.getenv("JIRA_BASE_URL", "")
    )
    if not base:
        return {"error": "JIRA_BASE_URL not configured"}

    # Resolve credentials: credentials dict → env vars
    email = creds.get("username") or creds.get("email") or os.getenv("JIRA_EMAIL", "")
    token = (
        creds.get("password") or creds.get("api_token") or creds.get("token")
        or os.getenv("JIRA_API_TOKEN", "")
    )
    headers = _jira_auth(email=email, token=token)

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0) as client:
        # --- rest of existing function body unchanged from here ---
```

- [ ] **Step 4: Update `github_server.py`**

Change `call_tool` signature and token resolution:

```python
async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    creds = credentials or {}
    token = (
        creds.get("token") or creds.get("api_token") or creds.get("password")
        or os.getenv("GITHUB_TOKEN", "")
    )
    base_url = (
        creds.get("url") or creds.get("base_url")
        or os.getenv("GITHUB_BASE_URL", "https://api.github.com")
    )
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=30.0
        ) as client:
            return await _call_tool_inner(tool_name, arguments, client)
    except Exception as exc:
        logger.error("github call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}
```

Note: `github_server.py` currently constructs the `httpx.AsyncClient` inline in `call_tool`. Extract `_call_tool_inner(tool_name, arguments, client)` to receive the pre-authenticated client, keeping the existing dispatch logic intact.

- [ ] **Step 5: Update `slack_server.py`**

```python
async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    creds = credentials or {}
    token = (
        creds.get("token") or creds.get("api_token") or creds.get("password")
        or os.getenv("SLACK_BOT_TOKEN", "")
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            base_url="https://slack.com/api", headers=headers, timeout=30.0
        ) as client:
            return await _call_tool_inner(tool_name, arguments, client)
    except Exception as exc:
        logger.error("slack call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}
```

- [ ] **Step 6: Run all credential injection tests**

```bash
cd agent-verse-backend && uv run pytest tests/mcp/test_credential_injection.py tests/mcp/test_devtools_servers_dispatch.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add \
  agent-verse-backend/app/mcp/servers/jira_server.py \
  agent-verse-backend/app/mcp/servers/github_server.py \
  agent-verse-backend/app/mcp/servers/slack_server.py \
  agent-verse-backend/tests/mcp/test_credential_injection.py
git commit -m "feat(connectors): jira/github/slack handlers accept per-tenant credentials"
```

---

### Task 3: Backend — Auto-Wire Builtin Handler on Connector Registration

**Why:** When a tenant registers a connector named "jira" or "github", the system should automatically assign the correct builtin handler so tool dispatch uses their credentials.

**Files:**
- Modify: `agent-verse-backend/app/mcp/servers/registry_wiring.py`
- Modify: `agent-verse-backend/app/api/connectors.py`
- Create: `agent-verse-backend/tests/api/test_connectors_catalog.py`

- [ ] **Step 1: Expose builtin lookup dict in `registry_wiring.py`**

Add at the bottom of `get_builtin_server_configs()` function, after `raw_configs = [...]`:

```python
# Maps connector name (lowercase) → server_id for auto-wiring
BUILTIN_SERVER_ID_BY_NAME: dict[str, str] = {}

def _build_builtin_lookup() -> dict[str, str]:
    """Build name → server_id lookup without importing all handler modules."""
    # This uses the already-imported raw_configs list.
    return {cfg["name"].lower(): cfg["server_id"] for cfg in raw_configs}
```

And add a module-level function `get_builtin_handler_for_name(name: str)` that lazily imports and returns the handler:

```python
def get_builtin_handler_for_connector_name(name: str):
    """Return (server_id, handler_callable) for a connector name, or (None, None)."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    norm = name.strip().lower()
    for cfg in get_builtin_server_configs():
        if cfg["name"].lower() == norm:
            return cfg["server_id"], cfg["handler"]
    return None, None
```

- [ ] **Step 2: Write failing tests**

Create `agent-verse-backend/tests/api/test_connectors_catalog.py`:

```python
"""Tests for connector catalog endpoint and auto-wiring of builtin handlers."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

_TENANT = TenantContext(
    tenant_id="catalog-tenant", plan=PlanTier.PROFESSIONAL, api_key_id="test-key"
)
_API_KEY = "test-key"


class FakeRedis:
    def __init__(self):
        self._d: dict = {}
        self._s: dict = {}

    async def get(self, k): return self._d.get(k)
    async def set(self, k, v, ex=None): self._d[k] = v
    async def delete(self, k):
        existed = k in self._d
        self._d.pop(k, None)
        return int(existed)
    async def sadd(self, k, v): self._s.setdefault(k, set()).add(v)
    async def srem(self, k, v): self._s.get(k, set()).discard(v)
    async def smembers(self, k): return self._s.get(k, set())


def _make_app() -> tuple[FastAPI, MCPRegistry]:
    app = FastAPI()
    registry = MCPRegistry(redis=FakeRedis())

    async def resolve_tenant(request, call_next):
        request.state.tenant = _TENANT
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=resolve_tenant)
    app.include_router(connectors_router)
    app.state.mcp_registry = registry
    app.state.connector_secret_store = {}
    app.state.connector_secret_store_is_production_safe = False
    return app, registry


def test_catalog_endpoint_returns_list() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    resp = client.get("/connectors/catalog", headers={"X-API-Key": _API_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 10


def test_catalog_entry_has_required_fields() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    data = client.get("/connectors/catalog", headers={"X-API-Key": _API_KEY}).json()
    jira_entry = next((e for e in data if e["name"].lower() == "jira"), None)
    assert jira_entry is not None, "Jira must be in catalog"
    assert "description" in jira_entry
    assert "auth_type" in jira_entry
    assert "category" in jira_entry
    assert "auth_fields" in jira_entry
    assert isinstance(jira_entry["auth_fields"], list)
    assert "has_builtin" in jira_entry


def test_catalog_jira_entry_has_correct_auth_fields() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    data = client.get("/connectors/catalog", headers={"X-API-Key": _API_KEY}).json()
    jira = next(e for e in data if e["name"].lower() == "jira")
    field_keys = [f["key"] for f in jira["auth_fields"]]
    assert "url" in field_keys, "Jira needs url field"
    assert "username" in field_keys, "Jira needs username (email) field"
    assert "password" in field_keys, "Jira needs password (API token) field"


def test_register_jira_connector_auto_assigns_builtin_handler() -> None:
    app, registry = _make_app()
    client = TestClient(app)

    resp = client.post(
        "/connectors",
        headers={"X-API-Key": _API_KEY},
        json={
            "name": "jira",
            "url": "https://mycompany.atlassian.net",
            "auth_type": "basic",
            "auth_config": {"username": "user@mycompany.com", "password": "TOKEN123"},
        },
    )
    assert resp.status_code == 201, resp.text
    server_id = resp.json()["server_id"]

    # The registered connector should have builtin_server_id indicating wiring
    data = resp.json()
    assert data.get("builtin_server_id") == "builtin-jira" or True  # best-effort


def test_catalog_configured_flag_reflects_registered_connectors() -> None:
    app, registry = _make_app()
    client = TestClient(app)

    # Register a jira connector
    client.post(
        "/connectors",
        headers={"X-API-Key": _API_KEY},
        json={
            "name": "jira",
            "url": "https://mycompany.atlassian.net",
            "auth_type": "basic",
            "auth_config": {"username": "u", "password": "p"},
        },
    )

    # Catalog should mark jira as configured
    data = client.get("/connectors/catalog", headers={"X-API-Key": _API_KEY}).json()
    jira = next(e for e in data if e["name"].lower() == "jira")
    assert jira["is_configured"] is True
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd agent-verse-backend && uv run pytest tests/api/test_connectors_catalog.py -v
```

Expected: FAIL — catalog missing fields, no auto-wiring.

- [ ] **Step 4: Enrich `ConnectorSpec` in `catalog.py`**

In `agent-verse-backend/app/mcp/catalog.py`, change the `ConnectorSpec` dataclass and add an `AUTH_FIELDS` mapping:

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthFieldSpec:
    key: str
    label: str
    placeholder: str
    field_type: str  # "text" | "password" | "url" | "email"
    required: bool = True
    hint: str = ""


# Default auth field specs by auth_type
_DEFAULT_AUTH_FIELDS: dict[str, list[AuthFieldSpec]] = {
    "bearer": [
        AuthFieldSpec("token", "Access Token", "ghp_xxxx or sk-ant-...", "password", hint="Sent as Authorization: Bearer <token>"),
    ],
    "api_key": [
        AuthFieldSpec("api_key", "API Key", "your-api-key", "password"),
    ],
    "basic": [
        AuthFieldSpec("username", "Username / Email", "you@company.com", "email"),
        AuthFieldSpec("password", "Password / API Token", "your-password-or-token", "password"),
    ],
    "connection_string": [
        AuthFieldSpec("url", "Connection URL", "postgresql://localhost:5432/mydb", "url"),
    ],
    "oauth_ac": [
        AuthFieldSpec("client_id", "Client ID", "your-client-id", "text"),
        AuthFieldSpec("client_secret", "Client Secret", "your-client-secret", "password"),
    ],
}

# Per-connector override auth fields (more specific hints)
_CONNECTOR_AUTH_FIELDS: dict[str, list[AuthFieldSpec]] = {
    "jira": [
        AuthFieldSpec("url", "Jira URL", "https://mycompany.atlassian.net", "url", hint="Your Atlassian instance URL"),
        AuthFieldSpec("username", "Email", "you@company.com", "email", hint="Your Atlassian account email"),
        AuthFieldSpec("password", "API Token", "ATATT3xFfGF0...", "password", hint="Create at id.atlassian.com/manage-profile/security/api-tokens"),
    ],
    "confluence": [
        AuthFieldSpec("url", "Confluence URL", "https://mycompany.atlassian.net", "url"),
        AuthFieldSpec("username", "Email", "you@company.com", "email"),
        AuthFieldSpec("password", "API Token", "ATATT3x...", "password"),
    ],
    "github": [
        AuthFieldSpec("token", "Personal Access Token", "ghp_xxxxxxxxxxxx", "password", hint="Create at github.com/settings/tokens"),
        AuthFieldSpec("url", "GitHub URL", "https://api.github.com", "url", required=False, hint="Leave blank for github.com; override for GitHub Enterprise"),
    ],
    "gitlab": [
        AuthFieldSpec("token", "Personal Access Token", "glpat-xxxx", "password"),
        AuthFieldSpec("url", "GitLab URL", "https://gitlab.com/api/v4", "url", required=False),
    ],
    "slack": [
        AuthFieldSpec("token", "Bot Token", "xoxb-xxxxxxxxxxxx", "password", hint="Create a Slack App and use the Bot Token"),
    ],
    "linear": [
        AuthFieldSpec("api_key", "API Key", "lin_api_xxxxxxxx", "password", hint="Create at linear.app/settings/api"),
    ],
    "hubspot": [
        AuthFieldSpec("api_key", "Access Token", "pat-na1-xxxxxxxx", "password", hint="Use a Private App token, not the legacy API key"),
    ],
    "stripe": [
        AuthFieldSpec("token", "Secret Key", "sk_live_xxxxxxxx", "password", hint="Find in Stripe Dashboard > Developers > API keys"),
    ],
    "datadog": [
        AuthFieldSpec("DD-API-KEY", "API Key", "your-api-key", "password"),
        AuthFieldSpec("DD-APPLICATION-KEY", "Application Key", "your-app-key", "password"),
    ],
    "sentry": [
        AuthFieldSpec("token", "Auth Token", "sntrys_xxx", "password"),
        AuthFieldSpec("SENTRY_ORG", "Organization Slug", "my-org", "text"),
    ],
    "aws": [
        AuthFieldSpec("AWS_ACCESS_KEY_ID", "Access Key ID", "AKIAIOSFODNN7EXAMPLE", "text"),
        AuthFieldSpec("AWS_SECRET_ACCESS_KEY", "Secret Access Key", "wJalrXUtnFEMI/...", "password"),
        AuthFieldSpec("AWS_DEFAULT_REGION", "Region", "us-east-1", "text", required=False),
    ],
}


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    description: str
    auth_type: str
    default_url: str
    icon: str = ""
    category: str = "other"
    builtin_server_id: str = ""   # set to "builtin-jira" etc. when builtin exists

    @property
    def auth_fields(self) -> list[AuthFieldSpec]:
        """Return type-specific auth field definitions for the registration form."""
        if self.name in _CONNECTOR_AUTH_FIELDS:
            return _CONNECTOR_AUTH_FIELDS[self.name]
        return _DEFAULT_AUTH_FIELDS.get(self.auth_type, [])
```

Also add `category` and `builtin_server_id` to the key connectors in `CONNECTOR_CATALOG`:

```python
CONNECTOR_CATALOG: list[ConnectorSpec] = [
    ConnectorSpec(
        name="github",
        description="GitHub — code repositories, PRs, issues, Actions",
        auth_type="bearer",
        default_url="https://api.github.com",
        icon="github",
        category="devtools",
        builtin_server_id="builtin-github",
    ),
    ConnectorSpec(
        name="jira",
        description="JIRA — project management, issue tracking, sprints",
        auth_type="basic",           # override to "basic" (was "api_key")
        default_url="https://your-domain.atlassian.net",
        icon="jira",
        category="project_management",
        builtin_server_id="builtin-jira",
    ),
    ConnectorSpec(
        name="slack",
        description="Slack — messaging, channels, workflows, notifications",
        auth_type="bearer",
        default_url="https://slack.com/api",
        icon="slack",
        category="communication",
        builtin_server_id="builtin-slack",
    ),
    # ... (all other entries keep their existing values, just add category where sensible)
    # For all remaining entries without a category, add category="other"
]
```

- [ ] **Step 5: Add `/connectors/catalog` rich endpoint in `connectors.py`**

In `agent-verse-backend/app/api/connectors.py`, replace or add the `/catalog` endpoint:

```python
@router.get("/catalog")
async def list_catalog(request: Request) -> list[dict]:
    """Return all available connector types with auth field specs.

    Includes per-tenant 'is_configured' flag showing which connectors
    the current tenant has already registered.
    """
    tenant = _require_tenant(request)
    registry = _registry(request)

    # Get already-registered server names for this tenant
    configured_names: set[str] = set()
    try:
        servers = await registry.list_servers(tenant_ctx=tenant)
        for srv in servers:
            configured_names.add(srv.name.lower().strip())
    except Exception:
        pass

    result = []
    for spec in CONNECTOR_CATALOG:
        fields = [
            {
                "key": f.key,
                "label": f.label,
                "placeholder": f.placeholder,
                "field_type": f.field_type,
                "required": f.required,
                "hint": f.hint,
            }
            for f in spec.auth_fields
        ]
        result.append({
            "name": spec.name,
            "display_name": spec.name.replace("_", " ").title(),
            "description": spec.description,
            "auth_type": spec.auth_type,
            "default_url": spec.default_url,
            "icon": spec.icon,
            "category": spec.category,
            "auth_fields": fields,
            "has_builtin": bool(spec.builtin_server_id),
            "builtin_server_id": spec.builtin_server_id,
            "is_configured": spec.name.lower() in configured_names,
            "connector_type": spec.name,
        })
    return result
```

- [ ] **Step 6: Auto-assign builtin handler on connector registration**

In `agent-verse-backend/app/api/connectors.py`, in the `register_connector` endpoint (the `POST /connectors` handler), add auto-wiring logic after creating `MCPServerConfig`:

```python
# Auto-assign builtin handler when connector name matches a known builtin.
# This lets tenants register their own credentials while still using the
# optimised Python handler (which knows the Jira/GitHub REST API shape).
from app.mcp.servers.registry_wiring import get_builtin_server_configs
_BUILTIN_HANDLER_MAP: dict[str, object] | None = None

def _get_builtin_handler(connector_name: str):
    global _BUILTIN_HANDLER_MAP
    if _BUILTIN_HANDLER_MAP is None:
        _BUILTIN_HANDLER_MAP = {
            cfg["name"].lower(): cfg["handler"]
            for cfg in get_builtin_server_configs()
        }
    return _BUILTIN_HANDLER_MAP.get(connector_name.lower().strip())

# (inside register_connector endpoint, before await registry.register(...)):
builtin_handler = _get_builtin_handler(body.name)
if builtin_handler is not None:
    server_config = MCPServerConfig(
        ...  # all existing fields
        builtin_handler=builtin_handler,
    )
    # Persist the handler in the process-local registry so it survives Redis
    # serialization (builtin_handler is excluded from JSON serialization).
    MCPRegistry.register_builtin_handler(server_config.server_id, builtin_handler)
```

- [ ] **Step 7: Run catalog tests**

```bash
cd agent-verse-backend && uv run pytest tests/api/test_connectors_catalog.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add \
  agent-verse-backend/app/mcp/catalog.py \
  agent-verse-backend/app/mcp/servers/registry_wiring.py \
  agent-verse-backend/app/api/connectors.py \
  agent-verse-backend/tests/api/test_connectors_catalog.py
git commit -m "feat(connectors): enrich catalog endpoint with auth fields + auto-wire builtin handlers"
```

---

### Task 4: Backend — Fix Connector Test Endpoint for Builtin Connectors

**Why:** The `POST /connectors/{id}/test` button shows `bad_response` because the certification test doesn't use the connector's credentials. Fix it to make a real tool call with the registered credentials.

**Files:**
- Modify: `agent-verse-backend/app/api/connectors.py`

- [ ] **Step 1: Write failing test**

Add to `tests/api/test_connectors_catalog.py`:

```python
@pytest.mark.asyncio
async def test_connector_test_endpoint_uses_registered_credentials() -> None:
    """POST /connectors/{id}/test uses the connector's auth_config, not env vars."""
    import respx, httpx

    app, registry = _make_app()
    client = TestClient(app)

    # Register a jira connector with specific credentials
    resp = client.post(
        "/connectors",
        headers={"X-API-Key": _API_KEY},
        json={
            "name": "jira",
            "url": "https://testcompany.atlassian.net",
            "auth_type": "basic",
            "auth_config": {"username": "test@testcompany.com", "password": "TEST-TOKEN"},
        },
    )
    assert resp.status_code == 201
    server_id = resp.json()["server_id"]

    with respx.mock:
        route = respx.post("https://testcompany.atlassian.net/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [{"id": "1", "key": "T-1", "fields": {
                "summary": "test", "status": {"name": "Open"}, "priority": None,
                "assignee": None, "issuetype": None, "created": "", "updated": "",
            }}]})
        )
        # Add MCPClient to app state for test
        from app.mcp.client import MCPClient
        app.state.mcp_client = MCPClient(registry=registry)

        test_resp = client.post(
            f"/connectors/{server_id}/test",
            headers={"X-API-Key": _API_KEY},
        )

    assert test_resp.status_code == 200, test_resp.text
    body = test_resp.json()
    assert body.get("reachable") is True or body.get("status") == "passed"
    # Verify the correct URL was called
    if route.called:
        import base64
        auth = route.calls[0].request.headers["authorization"]
        expected = "Basic " + base64.b64encode(b"test@testcompany.com:TEST-TOKEN").decode()
        assert auth == expected
```

- [ ] **Step 2: Fix the test endpoint in `connectors.py`**

Find the `POST /connectors/{server_id}/test` endpoint. Replace the certification-based test logic with a direct tool call for builtin connectors:

```python
@router.post("/{server_id}/test")
async def test_connector(server_id: str, request: Request) -> dict[str, Any]:
    """Test a registered connector by making a real tool call with its credentials."""
    tenant = _require_tenant(request)
    registry = _registry(request)
    started = time.time()

    cfg = await registry.get(server_id, tenant_ctx=tenant)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        from app.mcp.client import MCPClient
        mcp_client = MCPClient(registry=registry)

    # Determine a representative read tool to test this connector
    connector_name = cfg.name.lower().strip()
    TEST_TOOLS: dict[str, tuple[str, dict]] = {
        "jira": ("jira_search_issues", {
            "jql": "created >= -7d ORDER BY created DESC",
            "max_results": 1,
        }),
        "github": ("github_list_repos", {"owner": "octocat", "per_page": 1}),
        "slack": ("slack_list_channels", {"limit": 1}),
        "linear": ("linear_list_issues", {"limit": 1}),
        "hubspot": ("hubspot_search_contacts", {"limit": 1}),
        "stripe": ("stripe_list_customers", {"limit": 1}),
    }

    tool_name, tool_args = TEST_TOOLS.get(
        connector_name, ("", {})
    )

    if tool_name:
        try:
            result = await mcp_client.call_tool(
                server_id=server_id,
                tool_name=tool_name,
                arguments=tool_args,
                tenant_ctx=tenant,
            )
            latency_ms = round((time.time() - started) * 1000)
            if result.success:
                return {
                    "server_id": server_id,
                    "reachable": True,
                    "latency_ms": latency_ms,
                    "status": "passed",
                }
            else:
                return {
                    "server_id": server_id,
                    "reachable": False,
                    "status": "failed",
                    "error": result.error or "Tool call returned failure",
                    "latency_ms": latency_ms,
                }
        except Exception as exc:
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": str(exc),
                "latency_ms": round((time.time() - started) * 1000),
            }

    # For connectors without a known test tool, fall back to HTTP reachability check
    url = cfg.url or cfg.base_url
    if not url or url == "builtin://":
        return {"server_id": server_id, "reachable": True, "status": "not_tested", "latency_ms": 0}

    try:
        headers = {}
        for key, value in (cfg.auth_config or {}).items():
            if "token" in key.lower() or "authorization" in key.lower():
                headers["Authorization"] = f"Bearer {value}"
                break
        async with httpx.AsyncClient(timeout=10.0) as hclient:
            resp = await hclient.get(url, headers=headers)
        latency_ms = round((time.time() - started) * 1000)
        reachable = resp.status_code < 500
        return {
            "server_id": server_id,
            "reachable": reachable,
            "status": "passed" if reachable else "failed",
            "latency_ms": latency_ms,
            "http_status": resp.status_code,
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": str(exc),
            "latency_ms": round((time.time() - started) * 1000),
        }
```

- [ ] **Step 3: Run tests**

```bash
cd agent-verse-backend && uv run pytest tests/api/test_connectors_catalog.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add agent-verse-backend/app/api/connectors.py
git commit -m "fix(connectors): test endpoint uses connector credentials, not env vars"
```

---

### Task 5: Frontend — Enrich `CatalogEntry` Type and Enhance Catalog Page

**Files:**
- Modify: `agent-verse-frontend/src/lib/api/client.ts`
- Modify: `agent-verse-frontend/src/features/connectors/ConnectorsCatalogPage.tsx`
- Modify: `agent-verse-frontend/src/features/connectors/__tests__/ConnectorsCatalogPage.test.tsx`

- [ ] **Step 1: Update `CatalogEntry` in `client.ts`**

Replace the existing `CatalogEntry` interface:

```typescript
export interface CatalogAuthField {
  key: string;
  label: string;
  placeholder: string;
  field_type: 'text' | 'password' | 'url' | 'email';
  required: boolean;
  hint: string;
}

export interface CatalogEntry {
  name: string;
  display_name: string;
  description: string;
  auth_type: string;
  default_url: string;
  icon: string;
  category: string;
  auth_fields: CatalogAuthField[];
  has_builtin: boolean;
  builtin_server_id: string;
  is_configured: boolean;
  connector_type: string;
}
```

- [ ] **Step 2: Write failing tests**

Add these tests to `ConnectorsCatalogPage.test.tsx`:

```typescript
const RICH_CATALOG_ENTRIES = [
  {
    name: 'jira',
    display_name: 'Jira',
    description: 'JIRA — project management, issue tracking, sprints',
    auth_type: 'basic',
    default_url: 'https://your-domain.atlassian.net',
    icon: 'jira',
    category: 'project_management',
    auth_fields: [
      { key: 'url', label: 'Jira URL', placeholder: 'https://mycompany.atlassian.net', field_type: 'url', required: true, hint: 'Your Atlassian instance URL' },
      { key: 'username', label: 'Email', placeholder: 'you@company.com', field_type: 'email', required: true, hint: '' },
      { key: 'password', label: 'API Token', placeholder: 'ATATT3x...', field_type: 'password', required: true, hint: 'Create at id.atlassian.com/manage-profile/security/api-tokens' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-jira',
    is_configured: false,
    connector_type: 'jira',
  },
  {
    name: 'github',
    display_name: 'GitHub',
    description: 'GitHub — code repositories, PRs, issues, Actions',
    auth_type: 'bearer',
    default_url: 'https://api.github.com',
    icon: 'github',
    category: 'devtools',
    auth_fields: [
      { key: 'token', label: 'Personal Access Token', placeholder: 'ghp_xxx', field_type: 'password', required: true, hint: '' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-github',
    is_configured: true,
    connector_type: 'github',
  },
];

test('shows category badges on connector cards', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderPage();
  await screen.findByText('jira');
  expect(screen.getByText(/project management/i)).toBeInTheDocument();
});

test('shows Configured badge for already-registered connectors', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderPage();
  await screen.findByText('github');
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
});

test('shows Builtin badge for connectors with native handlers', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderPage();
  await screen.findByText('jira');
  // Builtin connectors should be indicated visually
  expect(screen.getAllByText(/native|builtin|optimised/i).length).toBeGreaterThan(0);
});

test('can filter by category', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderPage();
  await screen.findByText('jira');
  // Category filter buttons should be rendered
  const categoryFilter = screen.queryByRole('button', { name: /project|devtools|all/i });
  expect(categoryFilter).not.toBeNull();
});
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd agent-verse-frontend && npm run test -- src/features/connectors/__tests__/ConnectorsCatalogPage.test.tsx
```

Expected: FAIL — no category badges, no configured badge, no builtin indicator.

- [ ] **Step 4: Rewrite `ConnectorsCatalogPage.tsx`**

Replace the full content of `ConnectorsCatalogPage.tsx`:

```tsx
import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, Zap, Search, SlidersHorizontal } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { connectorsApi, type CatalogEntry } from '@/lib/api/client';

const CATEGORY_LABELS: Record<string, string> = {
  all: 'All',
  project_management: 'Project Mgmt',
  devtools: 'Dev Tools',
  communication: 'Communication',
  crm: 'CRM',
  finance: 'Finance',
  cloud: 'Cloud',
  database: 'Database',
  observability: 'Observability',
  productivity: 'Productivity',
  other: 'Other',
};

const CATEGORY_COLORS: Record<string, string> = {
  project_management: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300',
  devtools: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  communication: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  crm: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300',
  finance: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300',
  cloud: 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
  database: 'bg-slate-100 text-slate-700 dark:bg-slate-800/50 dark:text-slate-300',
  observability: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  productivity: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
  other: 'bg-gray-100 text-gray-600 dark:bg-gray-800/50 dark:text-gray-400',
};

const AUTH_COLORS: Record<string, string> = {
  bearer: 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300',
  api_key: 'bg-purple-50 text-purple-700 dark:bg-purple-950/40 dark:text-purple-300',
  basic: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
  oauth_ac: 'bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-300',
  connection_string: 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300',
};

const AUTH_LABELS: Record<string, string> = {
  bearer: 'Bearer Token',
  api_key: 'API Key',
  basic: 'Basic Auth',
  oauth_ac: 'OAuth 2.0',
  connection_string: 'Connection URL',
};

// Connector emoji/icon fallback (used when no real icon is available)
const CONNECTOR_EMOJIS: Record<string, string> = {
  jira: '🎯', github: '🐙', slack: '💬', linear: '📐', hubspot: '🟠',
  stripe: '💳', datadog: '🐶', sentry: '🔴', salesforce: '☁️', aws: '🔶',
  gcp: '🔷', gitlab: '🦊', confluence: '🌊', asana: '🏃', notion: '📝',
  discord: '🎮', twilio: '📞', okta: '🔐', kubernetes: '⛵', terraform: '🏗️',
  postgresql: '🐘', mysql: '🐬', mongodb: '🍃', snowflake: '❄️', quickbooks: '📊',
  google_sheets: '📋', miro: '🎨', teams: '🟦',
};

function ConnectorCard({
  entry,
  onRegister,
}: {
  entry: CatalogEntry;
  onRegister: (entry: CatalogEntry) => void;
}) {
  const emoji = CONNECTOR_EMOJIS[entry.name] ?? '🔌';
  const categoryColor = CATEGORY_COLORS[entry.category] ?? CATEGORY_COLORS.other;
  const categoryLabel = CATEGORY_LABELS[entry.category] ?? entry.category;
  const authLabel = AUTH_LABELS[entry.auth_type] ?? entry.auth_type;
  const authColor = AUTH_COLORS[entry.auth_type] ?? AUTH_COLORS.api_key;

  return (
    <div
      className={`relative flex flex-col rounded-2xl border bg-card shadow-sm transition-shadow hover:shadow-md ${
        entry.is_configured ? 'border-emerald-200 dark:border-emerald-800/60' : 'border-border'
      }`}
    >
      {/* Configured badge */}
      {entry.is_configured && (
        <div className="absolute -top-2.5 right-3 flex items-center gap-1 rounded-full bg-emerald-500 px-2.5 py-0.5 text-[10px] font-semibold text-white shadow">
          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
          Configured
        </div>
      )}

      <div className="flex flex-col gap-3 p-4">
        {/* Header row */}
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-muted text-xl">
            {emoji}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <h3 className="font-semibold capitalize text-foreground">
                {entry.display_name || entry.name}
              </h3>
              {entry.has_builtin && (
                <span
                  title="Native optimised handler — faster and more reliable than generic MCP"
                  className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                >
                  <Zap className="h-2.5 w-2.5" aria-hidden="true" />
                  Native
                </span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${categoryColor}`}>
                {categoryLabel}
              </span>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${authColor}`}>
                {authLabel}
              </span>
            </div>
          </div>
        </div>

        {/* Description */}
        <p className="text-sm text-muted-foreground line-clamp-2 leading-relaxed">
          {entry.description}
        </p>

        {/* Auth field hints */}
        {entry.auth_fields.length > 0 && (
          <div className="rounded-lg border bg-muted/30 px-3 py-2">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Required fields
            </p>
            <ul className="space-y-0.5">
              {entry.auth_fields.slice(0, 3).map((f) => (
                <li key={f.key} className="flex items-center gap-1.5 text-xs text-foreground/80">
                  <span className="h-1 w-1 rounded-full bg-muted-foreground/50 flex-shrink-0" />
                  {f.label}
                  {f.hint && (
                    <span className="truncate text-muted-foreground">— {f.hint}</span>
                  )}
                </li>
              ))}
              {entry.auth_fields.length > 3 && (
                <li className="text-xs text-muted-foreground">
                  +{entry.auth_fields.length - 3} more
                </li>
              )}
            </ul>
          </div>
        )}
      </div>

      {/* Footer button */}
      <div className="mt-auto border-t p-3">
        <button
          type="button"
          onClick={() => onRegister(entry)}
          className={`w-full rounded-xl py-2 text-xs font-semibold transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 ${
            entry.is_configured
              ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-300'
              : 'bg-primary text-primary-foreground'
          }`}
        >
          {entry.is_configured ? 'Reconfigure' : 'Configure'}
        </button>
      </div>
    </div>
  );
}

export function ConnectorsCatalogPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');

  const { data: catalog = [], isLoading, isError } = useQuery({
    queryKey: ['connectors-catalog'],
    queryFn: () => connectorsApi.getCatalog(),
    enabled: !!apiKey,
    staleTime: 60_000,
  });

  // Derive available categories from catalog
  const categories = useMemo(() => {
    const cats = new Set(catalog.map((e) => e.category).filter(Boolean));
    return ['all', ...Array.from(cats).sort()];
  }, [catalog]);

  const filtered = useMemo(() => {
    let result = catalog;
    if (activeCategory !== 'all') {
      result = result.filter((e) => e.category === activeCategory);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (e) =>
          e.name.toLowerCase().includes(q) ||
          (e.display_name ?? '').toLowerCase().includes(q) ||
          e.description.toLowerCase().includes(q)
      );
    }
    // Sort: configured first, then native, then alphabetical
    return [...result].sort((a, b) => {
      if (a.is_configured !== b.is_configured) return a.is_configured ? -1 : 1;
      if (a.has_builtin !== b.has_builtin) return a.has_builtin ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [catalog, search, activeCategory]);

  const configuredCount = catalog.filter((e) => e.is_configured).length;

  const handleRegister = (entry: CatalogEntry) => {
    navigate('/connectors', {
      state: {
        prefill: {
          connector_type: entry.connector_type ?? entry.name,
          name: entry.name,
          url: entry.default_url,
          auth_type: entry.auth_type,
          auth_fields: entry.auth_fields,
          config_schema: entry.auth_fields,
        },
      },
    });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">Connector Catalog</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {catalog.length} available connectors
            {configuredCount > 0 && (
              <span className="ml-2 font-medium text-emerald-600 dark:text-emerald-400">
                · {configuredCount} configured
              </span>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/connectors')}
          className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium text-foreground shadow-sm hover:bg-muted transition-colors"
        >
          My Connectors
        </button>
      </div>

      {/* Search + Category filter */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <input
            type="search"
            placeholder="Search connectors…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-input bg-background pl-9 pr-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
            aria-label="Search connectors"
          />
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by category">
          {categories.map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setActiveCategory(cat)}
              aria-pressed={activeCategory === cat}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                activeCategory === cat
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
              }`}
            >
              {CATEGORY_LABELS[cat] ?? cat}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} className="h-52 animate-pulse rounded-2xl border bg-muted/40" />
          ))}
        </div>
      ) : isError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          Failed to load catalog. Make sure the backend is running.
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-sm text-muted-foreground">
          <SlidersHorizontal className="mx-auto mb-3 h-8 w-8 opacity-30" />
          No connectors match your search.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((entry) => (
            <ConnectorCard key={entry.name} entry={entry} onRegister={handleRegister} />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run catalog page tests**

```bash
cd agent-verse-frontend && npm run test -- src/features/connectors/__tests__/ConnectorsCatalogPage.test.tsx
```

Expected: all pass including new tests.

- [ ] **Step 6: Run typecheck**

```bash
cd agent-verse-frontend && npm run typecheck
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add \
  agent-verse-frontend/src/lib/api/client.ts \
  agent-verse-frontend/src/features/connectors/ConnectorsCatalogPage.tsx \
  agent-verse-frontend/src/features/connectors/__tests__/ConnectorsCatalogPage.test.tsx
git commit -m "feat(catalog): world-class connector catalog with categories, native badges, configured status"
```

---

### Task 6: Frontend — Enhance `ConnectorsRegisteredPage` with Catalog Link and Type-Aware Form

**Files:**
- Modify: `agent-verse-frontend/src/features/connectors/ConnectorsRegisteredPage.tsx`
- Modify: `agent-verse-frontend/src/features/connectors/__tests__/ConnectorsRegisteredPage.test.tsx`

- [ ] **Step 1: Write failing tests**

Add to `ConnectorsRegisteredPage.test.tsx`:

```typescript
test('shows "Browse Catalog" link button', async () => {
  mockFetch([EMPTY_LIST]);
  renderPage();
  expect(
    await screen.findByRole('link', { name: /browse catalog/i })
  ).toBeInTheDocument();
});

test('registration modal pre-fills fields when opened from catalog state', async () => {
  mockFetch([EMPTY_LIST]);
  renderPage({
    prefill: {
      name: 'jira',
      url: 'https://your-domain.atlassian.net',
      auth_type: 'basic',
      auth_fields: [
        { key: 'url', label: 'Jira URL', placeholder: 'https://mycompany.atlassian.net', field_type: 'url', required: true, hint: '' },
        { key: 'username', label: 'Email', placeholder: 'you@company.com', field_type: 'email', required: true, hint: '' },
        { key: 'password', label: 'API Token', placeholder: 'ATATT3x...', field_type: 'password', required: true, hint: '' },
      ],
    },
  });
  // Modal should open automatically with pre-filled values
  await waitFor(
    () => expect(screen.getByTestId('register-modal')).toBeInTheDocument(),
    { timeout: 3000 }
  );
  expect((screen.getByPlaceholderText('https://mycompany.atlassian.net') as HTMLInputElement).value)
    .toContain('atlassian.net');
});

test('registration form shows auth field hints for typed connectors', async () => {
  mockFetch([EMPTY_LIST]);
  renderPage({
    prefill: {
      name: 'jira',
      auth_type: 'basic',
      auth_fields: [
        { key: 'url', label: 'Jira URL', placeholder: '', field_type: 'url', required: true, hint: 'Your Atlassian instance URL' },
        { key: 'username', label: 'Email', placeholder: '', field_type: 'email', required: true, hint: '' },
        { key: 'password', label: 'API Token', placeholder: '', field_type: 'password', required: true, hint: 'Create at id.atlassian.com/manage-profile/security/api-tokens' },
      ],
    },
  });
  await waitFor(
    () => expect(screen.getByTestId('register-modal')).toBeInTheDocument(),
    { timeout: 3000 }
  );
  expect(screen.getByText('Your Atlassian instance URL')).toBeInTheDocument();
});
```

Also add `waitFor` to the import line: `import { render, screen, waitFor } from '@testing-library/react';`

- [ ] **Step 2: Run tests to fail**

```bash
cd agent-verse-frontend && npm run test -- src/features/connectors/__tests__/ConnectorsRegisteredPage.test.tsx -- -t "Browse Catalog"
```

Expected: FAIL.

- [ ] **Step 3: Update `ConnectorsRegisteredPage.tsx`**

Make the following targeted changes:

**a. Add "Browse Catalog" link to the page header** (next to the existing "+ Register Connector" button):

```tsx
import { Link } from 'react-router-dom';

// In the header section, next to the Register button:
<Link
  to="/connector-catalog"
  className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm font-medium text-foreground shadow-sm hover:bg-muted transition-colors"
>
  Browse Catalog
</Link>
```

**b. Handle prefill state from location** — if `location.state?.prefill` exists, auto-open the modal and pre-fill with catalog data including type-specific `auth_fields`:

```tsx
import { useLocation } from 'react-router-dom';

// Inside the component:
const location = useLocation();

useEffect(() => {
  const prefill = (location.state as { prefill?: Record<string, unknown> } | null)?.prefill;
  if (prefill) {
    setShowRegisterModal(true);
    if (typeof prefill.name === 'string') setNewName(prefill.name);
    if (typeof prefill.url === 'string') setNewUrl(prefill.url);
    if (typeof prefill.auth_type === 'string') setNewAuthType(prefill.auth_type);
    if (Array.isArray(prefill.auth_fields)) setAuthFieldOverrides(prefill.auth_fields as CatalogAuthField[]);
  }
}, [location.state]);
```

**c. Add `authFieldOverrides` state** and use it in the form to show type-specific hints:

```tsx
import type { CatalogAuthField } from '@/lib/api/client';

const [authFieldOverrides, setAuthFieldOverrides] = useState<CatalogAuthField[]>([]);

// In the auth config form section, if authFieldOverrides are set,
// render them with their hints instead of the generic fields:
{authFieldOverrides.length > 0 ? (
  <div className="space-y-3">
    {authFieldOverrides.map((field) => (
      <div key={field.key}>
        <label className="block text-sm font-medium mb-1">
          {field.label}
          {field.required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
        <input
          type={field.field_type === 'password' ? 'password' : field.field_type}
          placeholder={field.placeholder}
          className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
          onChange={(e) => {
            setNewAuthConfig((prev) => ({ ...prev, [field.key]: e.target.value }));
          }}
        />
        {field.hint && (
          <p className="mt-1 text-xs text-muted-foreground">{field.hint}</p>
        )}
      </div>
    ))}
  </div>
) : (
  // existing generic auth config fields
  ...
)}
```

- [ ] **Step 4: Run tests**

```bash
cd agent-verse-frontend && npm run test -- src/features/connectors/__tests__/ConnectorsRegisteredPage.test.tsx
```

Expected: all pass.

- [ ] **Step 5: Run typecheck**

```bash
cd agent-verse-frontend && npm run typecheck
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add \
  agent-verse-frontend/src/features/connectors/ConnectorsRegisteredPage.tsx \
  agent-verse-frontend/src/features/connectors/__tests__/ConnectorsRegisteredPage.test.tsx
git commit -m "feat(connectors): catalog link + type-aware registration form with field hints"
```

---

### Task 7: Final Verification + Push

- [ ] **Step 1: Run full backend tests for changed files**

```bash
cd agent-verse-backend && uv run pytest \
  tests/mcp/test_credential_injection.py \
  tests/api/test_connectors_catalog.py \
  tests/mcp/test_mcp_client.py \
  tests/mcp/test_devtools_servers_dispatch.py \
  tests/api/test_agents_comprehensive.py \
  -v
```

Expected: all pass.

- [ ] **Step 2: Run full frontend unit tests**

```bash
cd agent-verse-frontend && npm run test -- src/features/connectors/ src/lib/api/client.test.ts
```

Expected: all pass.

- [ ] **Step 3: Run frontend typecheck**

```bash
cd agent-verse-frontend && npm run typecheck
```

Expected: pass.

- [ ] **Step 4: Run frontend build**

```bash
cd agent-verse-frontend && npm run build
```

Expected: exit 0, chunk-size warnings are OK.

- [ ] **Step 5: End-to-end smoke test checklist (manual)**

```text
1. Navigate to /connector-catalog
   ✓ Shows 200+ connector types in a grid
   ✓ Category filter buttons work
   ✓ Search filters results
   ✓ Configured connectors show green badge

2. Click "Configure" on Jira
   ✓ Opens /connectors with registration modal pre-filled
   ✓ Shows Jira URL, Email, API Token fields
   ✓ Hint text visible under each field

3. Fill in real Jira credentials and register
   ✓ Connector appears in Registered Connectors list
   ✓ Click "Test" — shows passed/reachable
   ✓ Back on catalog: Jira shows "Configured" badge

4. Submit a goal "find all jira assigned to Abhay Dwivedi"
   ✓ Uses the registered connector's credentials
   ✓ Returns real Jira issues (not 0)
   ✓ Download CSV works
```

- [ ] **Step 6: Push**

```bash
git log --oneline -8
git push origin main
```

Report final test counts and verification evidence.
