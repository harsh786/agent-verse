"""GET /models/configured never presents a refused embedding model as selected.

Live ONPREM-EMBED-DIMENSION evidence: the listing reported
``selected_model_id="Qwen/Qwen3-Embedding-0.6B"`` (1024-d, ``dimension_mismatch``
true) while the process kept embedding with the env NVIDIA 2048-d model
(``active_embedder``). A model refused for its vector width is now marked
``refused`` with the reason, and ``selected`` follows the active embedder.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api.model_registry import router as models_router
from app.providers.embedder_factory import EmbedderResolution
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_ISOLATE_PROVIDER_ENV = True

_QWEN = "Qwen/Qwen3-Embedding-0.6B"  # 1024-d (catalog)
_ENV_MODEL = "nvidia/llama-nemotron-embed-1b-v2"
_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
)
_HDR = {"X-API-Key": "ak_admin"}


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get(self, k: str) -> Any:
        return self.store.get(k)

    def set(self, k: str, v: Any) -> None:
        self.store[k] = v

    def incr(self, k: str) -> int:
        self.store[k] = str(int(self.store.get(k) or 0) + 1)
        return int(self.store[k])


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.registry_store as rs
    import app.ai_router.selection as sel
    from app.core.config import get_settings

    for name in ("NVIDIA_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("EMBEDDING_DIM", "2048")
    get_settings.cache_clear()
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(_FakeRedis()))
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})
    get_settings.cache_clear()


def _register(provider: str, model_id: str, **extra: Any) -> None:
    model_registry.register_configured(
        ModelEndpoint(
            provider=provider,
            model_id=model_id,
            display_name=model_id,
            capabilities=[ModelCapability.EMBEDDING],
            base_url="http://192.168.63.104:30082/v1" if provider == "onprem" else None,
            extra={"source": "override", **extra},
        )
    )


def _client(resolution: EmbedderResolution | None) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "ak_admin" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    if resolution is not None:
        app.state.embedder_resolution = resolution
    return TestClient(app)


def _embedding_group(client: TestClient) -> dict[str, Any]:
    listing = client.get("/models/configured", headers=_HDR).json()
    return next(g for g in listing["capabilities"] if g["capability"] == "embedding")


def _env_resolution() -> EmbedderResolution:
    return EmbedderResolution(
        embedder=object(),
        provider="dedicated",
        model=_ENV_MODEL,
        dimension=2048,
        source="env",
        registry_refusal=f"onprem/{_QWEN} produces 1024-d vectors but the index is 2048-d",
    )


def test_a_dimension_refused_model_first_in_the_order_is_not_selected() -> None:
    _register("onprem", _QWEN)
    _register("nvidia", _ENV_MODEL, dimensions=2048)
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}", f"nvidia/{_ENV_MODEL}"]})

    group = _embedding_group(_client(_env_resolution()))

    assert group["active_embedder"]["model"] == _ENV_MODEL
    assert group["selected_model_id"] == _ENV_MODEL  # consistent with active_embedder
    assert group["refused_model_ids"] == [_QWEN]
    qwen = next(r for r in group["models"] if r["model_id"] == _QWEN)
    assert qwen["dimension_mismatch"] is True
    assert qwen["refused"] is True and qwen["selected"] is False
    assert "EMBEDDING_DIM" in qwen["refusal_reason"] and "1024" in qwen["refusal_reason"]
    env_row = next(r for r in group["models"] if r["model_id"] == _ENV_MODEL)
    assert env_row["refused"] is False and env_row["selected"] is True
    assert env_row["refusal_reason"] == ""


def test_selected_follows_the_active_embedder_even_when_it_is_not_a_registry_row() -> None:
    _register("onprem", _QWEN)
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}"]})

    group = _embedding_group(_client(_env_resolution()))

    assert group["selected_model_id"] == _ENV_MODEL
    assert all(r["selected"] is False for r in group["models"])
    assert group["failover_providers"] == []


def test_without_a_known_active_embedder_the_first_non_refused_row_is_selected() -> None:
    _register("onprem", _QWEN)
    _register("nvidia", _ENV_MODEL, dimensions=2048)
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}", f"nvidia/{_ENV_MODEL}"]})

    group = _embedding_group(_client(None))  # no resolution on app.state: "unknown"

    assert group["active_embedder"] == {"status": "unknown"}
    rows = {r["model_id"]: r for r in group["models"]}
    expected = _ENV_MODEL if rows[_ENV_MODEL]["provider_ready"] else None
    assert group["selected_model_id"] == expected
    assert group["selected_model_id"] != _QWEN
    assert rows[_QWEN]["refused"] is True


def test_nothing_is_selected_when_no_embedder_is_available() -> None:
    _register("onprem", _QWEN)
    resolution = EmbedderResolution(errors=[("dedicated", "connection refused")])

    group = _embedding_group(_client(resolution))

    assert group["active_embedder"]["status"] == "unavailable"
    assert group["selected_model_id"] is None
    assert all(r["selected"] is False for r in group["models"])


def test_a_fitting_registry_model_that_is_active_is_selected_and_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    get_settings.cache_clear()
    _register("onprem", _QWEN)
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}"]})
    resolution = EmbedderResolution(
        embedder=object(), provider="onprem", model=_QWEN, dimension=1024, source="registry"
    )

    group = _embedding_group(_client(resolution))

    qwen = group["models"][0]
    assert qwen["dimension_mismatch"] is False and qwen["refused"] is False
    assert qwen["selected"] is True and group["selected_model_id"] == _QWEN
    assert group["refused_model_ids"] == []
