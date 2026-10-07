"""Capability-aware Test connection and ``GET /models/resolution``.

* ``POST /models/configured/test-endpoint`` makes one real call per capability:
  a chat completion (reasoning), a chat completion with an image (vision / OCR),
  ``/embeddings`` and ``/rerank`` — each reported in ``checks`` with latency and
  a classified ``error_kind``;
* ``GET /models/resolution`` reports what each capability and agent role runs on
  now, with the source of the choice and the fallbacks.

Model endpoints are faked with ``httpx.MockTransport`` (no network).
"""

_ISOLATE_PROVIDER_ENV = True

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api import model_registry_probes as probes
from app.api.model_registry import router as models_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_LAN = "http://192.168.63.104:30080/v1"
_ADMIN_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
)
_ADMIN = {"X-API-Key": "ak_admin"}


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
def _clean(monkeypatch: pytest.MonkeyPatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    for var in ("VISION_MODEL", "OCR_MODEL", "OLLAMA_OCR_MODEL", "RAG_HOSTED_RERANKER_URL",
                "ONPREM_RERANKER_URL"):
        monkeypatch.delenv(var, raising=False)
    # No local tiers unless a test turns one on.
    monkeypatch.setenv("OCR_TESSERACT_ENABLED", "false")
    import app.rag.cross_encoder as ce

    monkeypatch.setattr(ce, "local_cross_encoder_configured", lambda settings=None: False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _client(app_state: dict[str, Any] | None = None) -> TestClient:
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    app = FastAPI()

    async def _resolve(key: str) -> Any:
        return _ADMIN_CTX if key == "ak_admin" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    for k, v in (app_state or {}).items():
        setattr(app.state, k, v)
    return TestClient(app)


def _fake_endpoint(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    """Route every model-endpoint call to *handler*; returns the requests seen."""
    import app.ai_router.model_endpoints as me

    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        me,
        "endpoint_http_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(_record), **kw),
    )
    return seen


def _chat(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 2},
    }


def _probe(client: TestClient, caps: list[str], model_id: str = "m-1") -> dict[str, Any]:
    r = client.post(
        "/models/configured/test-endpoint",
        headers=_ADMIN,
        json={"provider": "openai_compatible", "model_id": model_id, "base_url": _LAN,
              "capabilities": caps},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _has_image(request: httpx.Request) -> bool:
    body = json.loads(request.content)
    content = body["messages"][-1]["content"]
    return isinstance(content, list) and any(p.get("type") == "image_url" for p in content)


# ── vision / OCR probe ───────────────────────────────────────────────────────


def test_vision_probe_sends_an_image_and_reports_the_reply(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m-1"}]})
        return httpx.Response(200, json=_chat("HELLO"))

    seen = _fake_endpoint(monkeypatch, handler)
    out = _probe(_client(), ["vision", "ocr"])
    assert out["ok"] is True and out["probe"] == "vision", out
    chat_calls = [r for r in seen if r.url.path.endswith("/chat/completions")]
    assert len(chat_calls) == 1 and _has_image(chat_calls[0])
    url = json.loads(chat_calls[0].content)["messages"][-1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,iVBOR")
    [check] = out["checks"]
    assert check["capabilities"] == ["ocr", "vision"]
    assert check["text_matched"] is True and check["reply"] == "HELLO"
    assert check["latency_ms"] >= 0 and out["model_listed"] is True


def test_vision_probe_reports_a_wrong_reading_without_failing(monkeypatch) -> None:
    _fake_endpoint(monkeypatch, lambda r: httpx.Response(200, json=_chat("A white square")))
    out = _probe(_client(), ["ocr"])
    assert out["ok"] is True
    assert out["checks"][0]["text_matched"] is False
    assert "expected the word HELLO" in out["detail"]


def test_vision_probe_on_a_text_only_model_is_unsupported(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(404)
        return httpx.Response(
            400, json={"error": {"message": "m-1 is not a multimodal model"}}
        )

    _fake_endpoint(monkeypatch, handler)
    out = _probe(_client(), ["vision"])
    assert out["ok"] is False
    assert out["error_kind"] == "unsupported"
    assert "multimodal" in out["error"]


# ── rerank probe ─────────────────────────────────────────────────────────────


def test_rerank_probe_posts_a_query_and_two_documents_and_reports_scores(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m-1"}]})
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.01}, {"index": 0, "relevance_score": 0.97},
        ]})

    seen = _fake_endpoint(monkeypatch, handler)
    out = _probe(_client(), ["rerank"])
    assert out["ok"] is True and out["probe"] == "rerank", out
    [call] = [r for r in seen if r.url.path.endswith("/rerank")]
    body = json.loads(call.content)
    assert body["query"] == probes.RERANK_QUERY
    assert body["documents"] == list(probes.RERANK_DOCUMENTS)
    check = out["checks"][0]
    assert [s["index"] for s in check["scores"]] == [0, 1]
    assert check["scores"][0]["score"] == pytest.approx(0.97)
    assert check["relevant_first"] is True
    assert out["detail"] == "2 documents scored"


def test_rerank_probe_without_a_rerank_route_is_unsupported(monkeypatch) -> None:
    _fake_endpoint(monkeypatch, lambda r: httpx.Response(404, text="Not Found"))
    out = _probe(_client(), ["rerank"])
    assert out["ok"] is False and out["error_kind"] == "unsupported"
    assert "/rerank route" in out["error"]


def test_rerank_probe_with_no_scores_is_an_invalid_response(monkeypatch) -> None:
    _fake_endpoint(monkeypatch, lambda r: httpx.Response(200, json={"results": []}))
    out = _probe(_client(), ["rerank"])
    assert out["ok"] is False and out["error_kind"] == "invalid_response"


# ── several capabilities, error kinds ────────────────────────────────────────


def test_each_capability_is_probed_and_a_failing_one_fails_the_test(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m-1"}]})
        if _has_image(request):
            return httpx.Response(401, json={"error": "invalid api key"})
        return httpx.Response(200, json=_chat("OK"))

    _fake_endpoint(monkeypatch, handler)
    out = _probe(_client(), ["text_generation", "vision"])
    assert [c["probe"] for c in out["checks"]] == ["chat", "vision"]
    assert out["checks"][0]["ok"] is True
    assert out["checks"][1]["ok"] is False and out["checks"][1]["error_kind"] == "auth"
    # The top level is the primary (chat) probe, but ok needs every check.
    assert out["probe"] == "chat" and out["ok"] is False
    assert out["error"].startswith("vision: HTTP 401") and out["error_kind"] == "auth"


def test_a_model_the_endpoint_does_not_serve_is_reported_as_such(monkeypatch) -> None:
    _fake_endpoint(monkeypatch, lambda r: httpx.Response(
        404, json={"error": {"message": "The model `m-1` does not exist."}}))
    out = _probe(_client(), ["text_generation"])
    assert out["ok"] is False and out["error_kind"] == "model_not_served"


def test_an_unreachable_endpoint_is_not_probed_twice(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    seen = _fake_endpoint(monkeypatch, handler)
    out = _probe(_client(), ["text_generation", "vision"])
    assert out["ok"] is False and out["error_kind"] == "unreachable"
    assert out["checks"][1]["error"].startswith("skipped:")
    assert len([r for r in seen if r.url.path.endswith("/chat/completions")]) == 1


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        ("HTTP 401: Unauthorized", "auth"),
        ('HTTP 400: {"message": "Please pass a valid API key"}', "auth"),
        ("HTTP 404: model 'x' not found", "model_not_served"),
        ("HTTP 400: unknown model x", "model_not_served"),
        ("HTTP 405: Method Not Allowed", "unsupported"),
        ("HTTP 400: image input is not supported", "unsupported"),
        ("HTTP 502: bad gateway", "http_error"),
        ("thinking model: spent the whole 16-token budget reasoning", "thinking_budget"),
        ("ConnectError: [Errno 61] Connection refused", "unreachable"),
        ("the endpoint returned no embedding", "invalid_response"),
        (None, None),
    ],
)
def test_error_kind_classification(error: str | None, kind: str | None) -> None:
    assert probes.error_kind(error) == kind


# ── GET /models/resolution ───────────────────────────────────────────────────


def _resolution(client: TestClient) -> dict[str, Any]:
    r = client.get("/models/resolution", headers=_ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _cap(out: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in out["capabilities"] if c["capability"] == name)


def _task(out: dict[str, Any], task: str) -> dict[str, Any]:
    return next(r for r in out["roles"] if r["task_type"] == task)


def _add(client: TestClient, model_id: str, caps: list[str], **extra: Any) -> None:
    r = client.post("/models/configured", headers=_ADMIN, json={
        "provider": "openai_compatible", "model_id": model_id, "capabilities": caps,
        "base_url": _LAN, **extra,
    })
    assert r.status_code == 200, r.text


def test_resolution_with_nothing_configured_warns_per_capability(monkeypatch) -> None:
    from app.ai_router import speech

    # No local speech engine either (faster-whisper / macOS say may be installed here).
    monkeypatch.setattr(speech, "local_stt_available", lambda: False)
    monkeypatch.setattr(speech, "local_tts_engine_available", lambda engine: False)
    out = _resolution(_client())
    assert [c["capability"] for c in out["capabilities"]] == [
        "reasoning", "embedding", "vision", "ocr", "rerank", "speech_to_text", "text_to_speech",
    ]
    for c in out["capabilities"]:
        assert c["routed"] is True
        assert c["model"] is None and c["source"] == "none", c
        assert c["warning"]
    assert any("No reranker" in w for w in out["warnings"])
    planning = _task(out, "planning")
    assert "planner" in planning["roles"] and "answer_synthesis" in planning["roles"]
    assert planning["model"] is None and planning["warning"]
    # Every role label of the runtime is listed under its task type.
    from app.ai_router.role_preference import ROLE_TASK_TYPES

    listed = {role for r in out["roles"] for role in r["roles"]}
    assert listed == set(ROLE_TASK_TYPES)


def test_resolution_follows_the_saved_order_with_fallbacks() -> None:
    client = _client()
    _add(client, "small-llm", ["text_generation"], supports_tools=True)
    _add(client, "big-llm", ["text_generation"], supports_tools=True)
    r = client.put("/models/preferences/text_generation", headers=_ADMIN, json={
        "order": ["openai_compatible/big-llm", "openai_compatible/small-llm"]})
    assert r.status_code == 200, r.text
    out = _resolution(client)
    reasoning = _cap(out, "reasoning")
    assert reasoning["model"]["model_id"] == "big-llm"
    assert reasoning["source"] == "registry_order"
    assert reasoning["source_label"] == "Registry order"
    assert [f["model_id"] for f in reasoning["fallbacks"]] == ["small-llm"]
    planning = _task(out, "planning")
    assert planning["model"]["model_id"] == "big-llm" and planning["source"] == "registry_order"
    assert [f["model_id"] for f in planning["fallbacks"]] == ["small-llm"]
    assert planning["model"]["servable"] is True
    judge = _task(out, "judge")
    # judge is routed by both goal routers (resolve_reasoning, like every role)
    assert judge["model"]["model_id"] == "big-llm" and judge["routed_by_goal_router"] is True


def test_resolution_without_an_order_is_cheapest_first() -> None:
    client = _client()
    _add(client, "only-llm", ["text_generation"], supports_tools=True)
    out = _resolution(client)
    assert _cap(out, "reasoning")["source"] == "registry_cheapest"
    assert _task(out, "execution")["source"] == "registry_cheapest"


def test_a_tenant_routing_pin_wins_for_its_role_only() -> None:
    client = _client()
    _add(client, "a-llm", ["text_generation"], supports_tools=True)
    _add(client, "b-llm", ["text_generation"], supports_tools=True)
    client.put("/models/preferences/text_generation", headers=_ADMIN, json={
        "order": ["openai_compatible/a-llm", "openai_compatible/b-llm"]})
    r = client.put("/models/routing-policies/planning", headers=_ADMIN,
                   json={"routing_mode": "tenant_default", "preferred_model": "b-llm"})
    assert r.status_code == 200, r.text
    out = _resolution(client)
    planning = _task(out, "planning")
    assert planning["model"]["model_id"] == "b-llm" and planning["source"] == "tenant_pin"
    # supervisor aliases planning for pins; verification and judge do not.
    assert _task(out, "supervisor")["source"] == "tenant_pin"
    assert _task(out, "verification")["model"]["model_id"] == "a-llm"
    assert _task(out, "judge")["source"] == "registry_order"


def test_vision_never_falls_back_to_the_reasoning_model(monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "plain-llm")
    monkeypatch.delenv("OCR_TESSERACT_ENABLED", raising=False)
    out = _resolution(_client())
    vision = _cap(out, "vision")
    assert vision["model"] is None and vision["source"] == "none"
    assert "image understanding is unavailable" in vision["warning"]
    assert "VISION_MODEL" in vision["warning"]  # the resolver's own hint
    # The env model is the reasoning fallback, and the role default.
    assert _cap(out, "reasoning")["source"] == "env_pin"
    assert _task(out, "planning")["source"] == "default"


def test_vision_and_ocr_use_each_others_order_and_env_pins(monkeypatch) -> None:
    client = _client()
    _add(client, "vl-model", ["vision"], supports_vision=True)
    out = _resolution(client)
    assert _cap(out, "vision")["model"]["model_id"] == "vl-model"
    ocr = _cap(out, "ocr")
    assert ocr["model"]["model_id"] == "vl-model" and "Vision order" in ocr["note"]

    model_registry.clear_configured()
    monkeypatch.setenv("OCR_MODEL", "ocr-env")
    out = _resolution(_client())
    assert _cap(out, "ocr")["model"]["model_id"] == "ocr-env"
    assert _cap(out, "ocr")["source"] == "env_pin"


def test_rerank_uses_a_registry_reranker_with_its_own_endpoint() -> None:
    client = _client()
    _add(client, "qwen3-reranker", ["rerank"])
    out = _resolution(client)
    rerank = _cap(out, "rerank")
    assert rerank["model"]["model_id"] == "qwen3-reranker"
    assert rerank["source"] == "registry_cheapest"
    assert "Registry rerank models in order" in rerank["note"]


def test_without_a_reranker_retrieval_is_reported_as_degraded() -> None:
    rerank = _cap(_resolution(_client()), "rerank")
    assert rerank["model"] is None and rerank["source"] == "none"
    assert "score order" in rerank["warning"] and "RAG_HOSTED_RERANKER_URL" in rerank["warning"]


def test_local_tiers_are_reported_with_their_own_source(monkeypatch) -> None:
    import app.rag.cross_encoder as ce

    monkeypatch.setattr(ce, "local_cross_encoder_configured", lambda settings=None: True)
    monkeypatch.setenv("OCR_TESSERACT_ENABLED", "true")
    out = _resolution(_client())
    rerank = _cap(out, "rerank")
    assert rerank["source"] == "local_default" and rerank["source_label"] == "Local default"
    assert "local cross-encoder" in rerank["note"]
    ocr = _cap(out, "ocr")
    assert ocr["model"]["model_id"] == "tesseract" and ocr["source"] == "local_default"
    assert "Tesseract" in ocr["note"]


def test_embedding_reports_the_running_embedder() -> None:
    from app.providers.embedder_factory import EmbedderResolution

    resolution = EmbedderResolution(
        embedder=object(), provider="openai", model="text-embedding-3-small", dimension=1536,
        source="env",
    )
    out = _resolution(_client({"embedder_resolution": resolution}))
    emb = _cap(out, "embedding")
    assert emb["model"]["model_id"] == "text-embedding-3-small"
    assert emb["model"]["provider"] == "openai"
    assert emb["source"] == "env_pin" and "1536-d" in emb["note"]

    none = EmbedderResolution(errors=[("voyage", "no key")])
    emb = _cap(_resolution(_client({"embedder_resolution": none})), "embedding")
    assert emb["model"] is None and "No embedder is running" in emb["warning"]


def test_resolution_requires_a_tenant() -> None:
    assert _client().get("/models/resolution").status_code == 401
