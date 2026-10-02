"""ENT-48: the debug span view is tenant-scoped, bounded, shared across replicas.

GET /analytics/observability/spans used to return the process-wide
InMemorySpanExporter list (every tenant's spans) and that exporter was never
trimmed. Spans now go through a BatchSpanProcessor into a Redis-backed,
per-tenant, capped + TTL'd store; the route serves only the caller's spans and
answers 503 when no store is wired.
"""

from __future__ import annotations

from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

import app.observability.tracing as tracing_module
from app.api.analytics import router as analytics_router
from app.observability.span_store import RedisSpanStore, TenantSpanExporter
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_KEYS = {
    "key-a": TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka"),
    "key-b": TenantContext(tenant_id="tenant-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb"),
}


def _store(max_spans: int = 1000, ttl: int = 3600) -> RedisSpanStore:
    return RedisSpanStore(
        client=fakeredis.FakeRedis(), max_spans_per_tenant=max_spans, ttl_seconds=ttl
    )


def _record(store: RedisSpanStore, tenant: str | None, name: str) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(TenantSpanExporter(store)))
    tracer = provider.get_tracer("t")
    attrs: dict[str, Any] = {"gen_ai.request.model": "m"}
    if tenant:
        attrs["agentverse.tenant_id"] = tenant
    with tracer.start_as_current_span(name, attributes=attrs):
        pass


def _app(store: RedisSpanStore | None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(analytics_router)
    app.state.span_store = store
    return app


def test_route_returns_only_callers_spans() -> None:
    store = _store()
    _record(store, "tenant-a", "a.work")
    _record(store, "tenant-b", "b.secret")
    _record(store, None, "untenanted.http")
    client = TestClient(_app(store))

    a = client.get("/analytics/observability/spans", headers={"X-API-Key": "key-a"})
    b = client.get("/analytics/observability/spans", headers={"X-API-Key": "key-b"})

    assert a.status_code == 200
    assert [s["name"] for s in a.json()] == ["a.work"]
    assert [s["name"] for s in b.json()] == ["b.secret"]
    assert a.json()[0]["attributes"]["agentverse.tenant_id"] == "tenant-a"


def test_route_503_when_no_store() -> None:
    client = TestClient(_app(None))
    resp = client.get("/analytics/observability/spans", headers={"X-API-Key": "key-a"})
    assert resp.status_code == 503


def test_route_requires_tenant() -> None:
    client = TestClient(_app(_store()))
    resp = client.get("/analytics/observability/spans")
    assert resp.status_code == 401


def test_route_limit_is_validated() -> None:
    client = TestClient(_app(_store()))
    resp = client.get(
        "/analytics/observability/spans?limit=100000", headers={"X-API-Key": "key-a"}
    )
    assert resp.status_code == 422


def test_store_is_capped_per_tenant_and_newest_first() -> None:
    store = _store(max_spans=5)
    for i in range(12):
        _record(store, "tenant-a", f"s{i}")
    spans = store.recent("tenant-a", limit=100)
    assert [s["name"] for s in spans] == ["s11", "s10", "s9", "s8", "s7"]
    assert store.client.llen(store.key("tenant-a")) == 5


def test_store_sets_ttl() -> None:
    store = _store(ttl=120)
    _record(store, "tenant-a", "x")
    ttl = store.client.ttl(store.key("tenant-a"))
    assert 0 < ttl <= 120


def test_store_read_error_propagates() -> None:
    class _Broken:
        def lrange(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

    store = RedisSpanStore(client=_Broken())
    with pytest.raises(ConnectionError):
        store.recent("tenant-a", limit=10)


def test_route_503_on_store_read_error() -> None:
    class _Broken:
        def lrange(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

    client = TestClient(_app(RedisSpanStore(client=_Broken())))
    resp = client.get("/analytics/observability/spans", headers={"X-API-Key": "key-a"})
    assert resp.status_code == 503
    assert "redis down" not in resp.text


def test_configure_tracing_never_installs_in_memory_exporter() -> None:
    assert not hasattr(tracing_module, "_in_memory_exporter")
    assert not hasattr(tracing_module, "get_recent_spans")


def test_production_otlp_init_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import opentelemetry.exporter.otlp.proto.grpc.trace_exporter as otlp

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("grpc init failed")

    monkeypatch.setattr(otlp, "OTLPSpanExporter", _boom)
    with pytest.raises(RuntimeError, match="OTLP"):
        tracing_module.build_tracer_provider(
            "svc", otlp_endpoint="http://collector:4317", production=True
        )


def test_dev_otlp_init_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    import opentelemetry.exporter.otlp.proto.grpc.trace_exporter as otlp

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("grpc init failed")

    monkeypatch.setattr(otlp, "OTLPSpanExporter", _boom)
    provider = tracing_module.build_tracer_provider(
        "svc", otlp_endpoint="http://collector:4317", production=False
    )
    assert provider is not None


def test_provider_wires_span_store_when_given() -> None:
    store = _store()
    provider = tracing_module.build_tracer_provider("svc", span_store=store)
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("x", attributes={"agentverse.tenant_id": "tenant-a"}):
        pass
    provider.force_flush()
    assert [s["name"] for s in store.recent("tenant-a", limit=10)] == ["x"]
    provider.shutdown()


@pytest.mark.integration
def test_real_redis_spans_shared_across_replicas_and_tenant_scoped(redis_url: str) -> None:
    """Two processes' exporters (two stores, one Redis) feed one tenant view."""
    replica_1 = RedisSpanStore(redis_url=redis_url, max_spans_per_tenant=3, ttl_seconds=60)
    replica_2 = RedisSpanStore(redis_url=redis_url, max_spans_per_tenant=3, ttl_seconds=60)
    replica_1.client.delete(replica_1.key("tenant-a"), replica_1.key("tenant-b"))
    _record(replica_1, "tenant-a", "api.step")
    _record(replica_2, "tenant-a", "worker.step")
    _record(replica_2, "tenant-b", "other.step")
    for i in range(3):
        _record(replica_2, "tenant-b", f"b{i}")

    client = TestClient(_app(replica_1))
    a = client.get("/analytics/observability/spans", headers={"X-API-Key": "key-a"})
    assert [s["name"] for s in a.json()] == ["worker.step", "api.step"]
    assert [s["name"] for s in replica_1.recent("tenant-b", 10)] == ["b2", "b1", "b0"]
    assert 0 < replica_1.client.ttl(replica_1.key("tenant-b")) <= 60
