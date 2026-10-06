"""Connector health sweep over the durable registry (a02-F034-N1).

``check_mcp_health`` used to SCAN the legacy ``mcp:servers:*`` Redis keys. Since
MCPREG-01 the registry lives in Postgres ``mcp_servers`` (Redis only caches it),
so every connector created after the cutover was never probed while stale
legacy keys kept being probed. It also probed up to 5000 keys serially with 5 s
timeouts from a beat that fires every 30 s (runs overlapped) and restarted the
scan at cursor 0 each run (connectors past the first 5000 were never checked).

The sweep now:

* reads ``mcp_servers`` cross-tenant through the maintenance (BYPASSRLS) session
  in keyset pages ordered by ``(tenant_id, id)`` — bounded queries, no table load;
* probes each page with bounded concurrency and a per-probe timeout, inside a
  wall-clock budget shorter than the beat interval;
* keeps its position in a shared Redis cursor, so consecutive runs (on any
  worker) continue where the last one stopped and every connector is reached;
* holds a Redis lock so two beat runs never overlap (a stuck run's lock expires).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

PAGE_SIZE = 200
CONCURRENCY = 20
PROBE_TIMEOUT_S = 5.0
RUN_BUDGET_S = 25.0  # < the 30 s beat interval
CURSOR_KEY = "mcp:health:cursor:v1"
LOCK_KEY = "mcp:health:lock:v1"

Row = tuple[str, str, dict[str, Any]]
Probe = Callable[[Any], Awaitable[dict[str, Any]]]
Persist = Callable[[list[dict[str, Any]]], Awaitable[int]]


async def fetch_connector_page(
    factory: Any, after: tuple[str, str] | None, limit: int
) -> list[Row]:
    """One keyset page of enabled connectors across tenants (system session)."""
    from sqlalchemy import text

    from app.db.rls import system_session

    sql = (
        "SELECT tenant_id, id, config FROM mcp_servers WHERE enabled IS TRUE "
        + ("AND (tenant_id, id) > (:t, :i) " if after else "")
        + "ORDER BY tenant_id, id LIMIT :n"
    )
    params: dict[str, Any] = {"n": limit}
    if after:
        params.update(t=after[0], i=after[1])
    async with factory() as session, session.begin(), system_session(session):
        rows = (await session.execute(text(sql), params)).all()
    out: list[Row] = []
    for tenant_id, server_id, config in rows:
        cfg = config if isinstance(config, dict) else json.loads(config or "{}")
        out.append((str(tenant_id), str(server_id), cfg))
    return out


def classify_health(status_code: int) -> str:
    """HTTP status -> connector health (5xx is never 'ok')."""
    if status_code < 400:
        return "healthy"
    if status_code < 500:
        return "degraded"
    return "unhealthy"


_MCP_PROTOCOL_VERSION = "2025-03-26"
_FALLBACK_TO_ROOT = frozenset({404, 405})


def _http_error(status_code: int) -> str:
    if status_code in (401, 403):
        return (
            f"HTTP {status_code}: the server is up but refused the unauthenticated probe"
        )
    return f"HTTP {status_code}"


def _mcp_initialize() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": f"health-{secrets.token_hex(4)}",
        "method": "initialize",
        "params": {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "agentverse-health-probe", "version": "1"},
        },
    }


def _jsonrpc_error(resp: Any) -> str | None:
    """The JSON-RPC error of a JSON initialize answer (None for a result / SSE)."""
    if "json" not in str(resp.headers.get("content-type", "")).lower():
        return None
    try:
        body = resp.json()
    except ValueError:
        return "initialize answered with invalid JSON"
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return f"MCP error: {str(body['error'].get('message') or body['error'])[:160]}"
    if isinstance(body, dict) and "result" in body:
        return None
    return "initialize answered without a JSON-RPC result"


async def probe_connector(cfg: Any) -> dict[str, Any]:
    """SSRF-guarded liveness probe of one connector (pinned client).

    a02-F034-01: it was always ``GET {base}/health``, and a standard MCP server
    (which has no such route) answered 404 and was recorded ``degraded``. Now an
    MCP endpoint (``…/mcp``) gets the protocol's own ``initialize`` request (any
    session it opens is closed again), and a REST/OpenAPI connector gets
    ``GET {base}/health``, falling back to ``GET {base}`` when the server has no
    health route (404/405).
    """
    from app.mcp.client import _absolute_http_url, _is_mcp_endpoint
    from app.net.ssrf_guard import private_access_networks, public_async_client, request_public

    base = _absolute_http_url((cfg.base_url or cfg.url or "").rstrip("/"))
    t0 = time.monotonic()
    nets = private_access_networks()
    # Private networks only when ALLOW_PRIVATE_NETWORK_ACCESS is on; otherwise the
    # call is exactly the public-only probe it always was.
    net_kw: dict[str, Any] = {"allowed_networks": nets} if nets is not None else {}
    protocol_error: str | None = None
    try:
        async with public_async_client(timeout=PROBE_TIMEOUT_S, **net_kw) as client:
            if _is_mcp_endpoint(base):
                resp = await request_public(
                    client,
                    "POST",
                    base,
                    context="mcp health check",
                    json=_mcp_initialize(),
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    **net_kw,
                )
                if resp.status_code < 400:
                    protocol_error = _jsonrpc_error(resp)
                session = resp.headers.get("mcp-session-id")
                if session:
                    try:  # best effort: do not leave the probe's session open
                        await request_public(
                            client,
                            "DELETE",
                            base,
                            context="mcp health check",
                            headers={"Mcp-Session-Id": session},
                            **net_kw,
                        )
                    except Exception as exc:
                        logger.debug("mcp_health_session_close_failed error=%s", exc)
            else:
                resp = await request_public(
                    client, "GET", f"{base}/health", context="mcp health check", **net_kw
                )
                if resp.status_code in _FALLBACK_TO_ROOT:
                    resp = await request_public(
                        client, "GET", base, context="mcp health check", **net_kw
                    )
    except Exception as exc:
        return {"status": "unreachable", "latency_ms": None, "error": str(exc)[:200]}
    status = classify_health(resp.status_code)
    error: str | None = None if status == "healthy" else _http_error(resp.status_code)
    if status == "healthy" and protocol_error:
        status, error = "degraded", protocol_error
    return {
        "status": status,
        "latency_ms": round((time.monotonic() - t0) * 1000),
        "error": error,
    }


def builtin_readiness(cfg: Any) -> dict[str, Any]:
    """In-process readiness of a built-in (``builtin://``) connector (a02-F034-02).

    Built-ins have no URL of their own to probe, so the sweep skipped them and
    they never had a snapshot. Without calling the vendor API (that needs the
    tenant's secrets, and is what the connector test does), the sweep now
    records whether the connector can run here: ``disabled`` (operator kill
    switch), ``unhealthy`` (no built-in handler for its type), ``degraded`` (the
    vendor API needs credentials the connector does not have) or ``ready``
    (handler present, enabled, credentials configured or none needed).
    """
    from app.mcp.builtin_kill_switch import disabled_reason
    from app.mcp.servers.registry_wiring import (
        builtin_config_for_type,
        builtin_type_of,
        has_tenant_credentials,
        infer_builtin_type_from_name,
    )

    declared = builtin_type_of(cfg)
    url = (cfg.base_url or cfg.url or "").strip()
    url_type = url.removeprefix("builtin://").split("/", 1)[0] if url else ""
    spec = (
        (builtin_config_for_type(declared) if declared else None)
        or (builtin_config_for_type(url_type) if url_type else None)
        or (
            builtin_config_for_type(inferred)
            if (inferred := infer_builtin_type_from_name(str(cfg.name or "")))
            else None
        )
    )
    if spec is None:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "error": f"no built-in handler for {declared or url_type or cfg.name!r}",
        }
    builtin_type = str(spec["server_id"])
    reason = disabled_reason(builtin_type)
    if reason is not None:
        return {"status": "disabled", "latency_ms": None, "error": reason}
    required = tuple(spec.get("requires_env", []) or ())
    credentials = cfg.auth_config if isinstance(cfg.auth_config, dict) else {}
    if required and not has_tenant_credentials(credentials, required):
        return {
            "status": "degraded",
            "latency_ms": None,
            "error": f"credentials not configured for {builtin_type}",
        }
    return {"status": "ready", "latency_ms": 0, "error": None}


def _probe_target(config: dict[str, Any]) -> Any | None:
    """The validated config to probe, or None when there is nothing to probe."""
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig.model_validate(config)
    base = (cfg.base_url or cfg.url or "").strip()
    if not base and not _is_builtin(cfg):
        return None
    return cfg


def _is_builtin(cfg: Any) -> bool:
    """Dispatched by an in-process handler (a built-in type wins over its URL,
    exactly as in ``MCPClient._call_tool_impl``)."""
    from app.mcp.servers.registry_wiring import builtin_type_of

    base = (cfg.base_url or cfg.url or "").strip()
    return base.startswith("builtin://") or bool(builtin_type_of(cfg))


async def _probe_row(row: Row, probe: Probe, sem: asyncio.Semaphore) -> dict[str, Any] | None:
    tenant_id, server_id, config = row
    try:
        cfg = _probe_target(config)
    except Exception as exc:
        return {
            "tenant_id": tenant_id, "server_id": server_id, "status": "invalid_config",
            "latency_ms": None, "error": str(exc)[:200],
        }
    if cfg is None:
        return None
    if _is_builtin(cfg):
        try:
            outcome = builtin_readiness(cfg)
        except Exception as exc:
            outcome = {"status": "unhealthy", "latency_ms": None, "error": str(exc)[:200]}
        return {"tenant_id": tenant_id, "server_id": server_id, **outcome}
    async with sem:
        try:
            outcome = await asyncio.wait_for(probe(cfg), timeout=PROBE_TIMEOUT_S + 1)
        except TimeoutError:
            outcome = {"status": "unreachable", "latency_ms": None, "error": "probe timed out"}
        except Exception as exc:
            outcome = {"status": "unreachable", "latency_ms": None, "error": str(exc)[:200]}
    return {"tenant_id": tenant_id, "server_id": server_id, **outcome}


async def _acquire_lock(redis: Any, ttl_s: int) -> str | None:
    token = secrets.token_hex(8)
    ok = await redis.set(LOCK_KEY, token, nx=True, ex=ttl_s)
    return token if ok else None


async def _release_lock(redis: Any, token: str) -> None:
    current = await redis.get(LOCK_KEY)
    if isinstance(current, bytes):
        current = current.decode()
    if current == token:
        await redis.delete(LOCK_KEY)


async def _load_cursor(redis: Any) -> tuple[str, str] | None:
    raw = await redis.get(CURSOR_KEY)
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        tenant_id, server_id = json.loads(raw)
        return str(tenant_id), str(server_id)
    except (ValueError, TypeError):
        return None


async def run_health_sweep(
    *,
    factory: Any,
    redis: Any,
    persist: Persist,
    probe: Probe = probe_connector,
    fetch: Callable[[Any, tuple[str, str] | None, int], Awaitable[list[Row]]] = (
        fetch_connector_page
    ),
    page_size: int = PAGE_SIZE,
    concurrency: int = CONCURRENCY,
    budget_s: float = RUN_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Probe connectors from the cursor onward until the budget or the end."""
    token: str | None = None
    if redis is not None:
        token = await _acquire_lock(redis, int(budget_s + PROBE_TIMEOUT_S * 2 + 10))
        if token is None:
            logger.info("mcp_health_sweep_skipped_previous_run_active")
            return {"status": "skipped", "reason": "previous run still active",
                    "servers_checked": 0, "snapshots_persisted": 0, "results": []}
    deadline = clock() + budget_s
    sem = asyncio.Semaphore(max(1, concurrency))
    checked = persisted = 0
    sample: list[dict[str, Any]] = []
    wrapped = False
    try:
        cursor = await _load_cursor(redis) if redis is not None else None
        while clock() < deadline:
            rows = await fetch(factory, cursor, page_size)
            outcomes = await asyncio.gather(*(_probe_row(r, probe, sem) for r in rows))
            snaps = [o for o in outcomes if o is not None]
            checked += len(snaps)
            persisted += await persist(snaps)
            sample.extend(snaps[: max(0, 20 - len(sample))])
            if len(rows) < page_size:
                cursor, wrapped = None, True  # end of the table: next run restarts
                break
            cursor = (rows[-1][0], rows[-1][1])
        if redis is not None:
            if cursor is None:
                await redis.delete(CURSOR_KEY)
            else:
                await redis.set(CURSOR_KEY, json.dumps(list(cursor)))
    finally:
        if redis is not None and token is not None:
            try:
                await _release_lock(redis, token)
            except Exception as exc:  # the lock's TTL frees it anyway
                logger.warning("mcp_health_sweep_lock_release_failed error=%s", exc)
    return {
        "status": "ok",
        "servers_checked": checked,
        "snapshots_persisted": persisted,
        "completed_pass": wrapped,
        "results": sample,
    }


DEFAULT_RETENTION_DAYS = 7


def snapshot_retention_days() -> int:
    """``CONNECTOR_HEALTH_RETENTION_DAYS`` (default 7, at least 1)."""
    import os

    raw = os.getenv("CONNECTOR_HEALTH_RETENTION_DAYS", "")
    try:
        days = int(raw) if raw.strip() else DEFAULT_RETENTION_DAYS
    except ValueError:
        logger.warning("connector_health_retention_days_invalid value=%r", raw)
        days = DEFAULT_RETENTION_DAYS
    return max(1, days)


async def prune_health_snapshots(
    factory: Any,
    *,
    retention_days: int,
    batch_size: int = 5000,
    max_batches: int = 200,
) -> int:
    """Delete ``connector_health_snapshots`` older than the retention window (HEALTH-06).

    One row per connector per sweep had no retention at all. Cross-tenant, so it
    runs on the maintenance (BYPASSRLS) session; each batch is its own short
    transaction driven by the ``checked_at`` index, and a run is capped at
    ``max_batches`` so a large backlog drains over several runs.
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    deleted = 0
    for _ in range(max_batches):
        async with factory() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(
                    "DELETE FROM connector_health_snapshots WHERE id IN ("
                    "SELECT id FROM connector_health_snapshots "
                    "WHERE checked_at < NOW() - make_interval(days => :d) "
                    "ORDER BY checked_at LIMIT :n)"
                ),
                {"d": retention_days, "n": batch_size},
            )
        count = int(getattr(result, "rowcount", 0) or 0)
        deleted += count
        if count < batch_size:
            break
    return deleted


__all__ = [
    "CURSOR_KEY",
    "LOCK_KEY",
    "builtin_readiness",
    "classify_health",
    "fetch_connector_page",
    "probe_connector",
    "prune_health_snapshots",
    "run_health_sweep",
    "snapshot_retention_days",
]
