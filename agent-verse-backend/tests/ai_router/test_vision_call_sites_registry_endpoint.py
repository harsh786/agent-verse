"""Vision call sites run the Model Registry's vision model at ITS endpoint.

The image parser, the browser agent (RPA / workflow / page analyzer) and the
multimodal pipeline used to send a literal (``gpt-4o``, ``claude-opus-4-5``,
``claude-3-5-sonnet-20241022``) through a raw SDK client on ``OPENAI_BASE_URL``
or through whatever provider they were handed. Here a real local
OpenAI-compatible HTTP server stands in at the network edge as the registry
vision model's ``base_url``: each call must reach it with the registry model id,
the model's own key and the image — and fail over along the vision chain.
"""

from __future__ import annotations

# Isolate ambient provider/model env so only the registry configures models.
_ISOLATE_PROVIDER_ENV = True

import base64
from collections.abc import Iterator
from typing import Any

import pytest

from app.ai_router.registry import model_registry
from tests.providers._registry_llm_server import (
    VISION_KEY,
    VISION_MODEL,
    LocalLLMServer,
    add_registry_vision_model,
)

CAPTION = "A red bicycle leaning on a brick wall."
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI6QAAAABJRU5ErkJggg=="
)
_PNG_B64 = base64.b64encode(_PNG).decode()


class _NonVisionPlatform:
    """The platform provider: a text model that cannot see images. Vision
    availability must not depend on it, and it must never get the image."""

    _default_model = "platform-text-llm"

    def __init__(self) -> None:
        self.calls = 0

    def supports_vision(self) -> bool:
        return False

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        raise AssertionError("the vision call went to the platform provider")


@pytest.fixture
def vision_server() -> Iterator[LocalLLMServer]:
    server = LocalLLMServer(reply=CAPTION, fail_models={"broken-vlm"}).start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.registry_store as store_mod
    import app.ai_router.selection as sel
    from app.providers import guarded_completion

    async def _no_charge(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(guarded_completion, "_charge", _no_charge)
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.setenv("VISION_MAX_ATTEMPTS", "1")
    for name in ("VISION_MODEL", "NVIDIA_VISION_MODEL", "OCR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _assert_vision_request(server: LocalLLMServer, model: str = VISION_MODEL) -> None:
    hits = [r for r in server.requests if r.model == model]
    assert hits, [r.model for r in server.requests]
    req = hits[-1]
    assert req.authorization == f"Bearer {VISION_KEY}"
    assert "data:image/" in str(req.body.get("messages")), "the image never reached the model"


# ── VisionParser (ingestion) ─────────────────────────────────────────────────


async def test_vision_parser_uses_the_registry_vision_model_endpoint(
    vision_server: LocalLLMServer,
) -> None:
    from app.ingestion.parsers.vision_parser import VisionParser

    add_registry_vision_model(vision_server.url)
    platform = _NonVisionPlatform()
    result = await VisionParser(provider=platform).parse_image_bytes(_PNG, "bike.png")  # type: ignore[arg-type]

    assert result.error is None
    assert result.description == CAPTION
    assert result.model_used == VISION_MODEL
    assert platform.calls == 0
    _assert_vision_request(vision_server)


async def test_vision_parser_without_injected_provider_reaches_the_endpoint(
    vision_server: LocalLLMServer,
) -> None:
    from app.ingestion.parsers.vision_parser import VisionParser

    add_registry_vision_model(vision_server.url)
    result = await VisionParser().parse_image_bytes(_PNG, "bike.png")
    assert result.description == CAPTION
    _assert_vision_request(vision_server)


async def test_vision_parser_fails_over_along_the_vision_chain(
    vision_server: LocalLLMServer,
) -> None:
    from app.ingestion.parsers.vision_parser import VisionParser

    add_registry_vision_model(vision_server.url, model_id="broken-vlm", cost=0.0)
    add_registry_vision_model(vision_server.url, cost=0.5)
    model_registry.set_preferences({"vision": ["openai_compatible/broken-vlm"]})

    result = await VisionParser(provider=_NonVisionPlatform()).parse_image_bytes(  # type: ignore[arg-type]
        _PNG, "bike.png"
    )
    assert result.description == CAPTION
    assert result.model_used == VISION_MODEL
    assert [r.model for r in vision_server.requests] == ["broken-vlm", VISION_MODEL]


async def test_vision_parser_with_no_vision_model_is_an_honest_error() -> None:
    from app.ingestion.parsers.vision_parser import VisionParser

    result = await VisionParser(provider=_NonVisionPlatform()).parse_image_bytes(  # type: ignore[arg-type]
        _PNG, "bike.png"
    )
    assert result.model_used == "fallback"
    assert result.error and "vision" in result.error and "VISION_MODEL" in result.error


async def test_warm_up_pings_the_registry_vision_endpoint(
    vision_server: LocalLLMServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ingestion.parsers import vision_parser as vp

    monkeypatch.setattr(vp, "_warmed_up", False)
    add_registry_vision_model(vision_server.url)
    assert await vp.warm_up_vision_model() is True
    assert [r.model for r in vision_server.requests] == [VISION_MODEL]
    assert vision_server.requests[0].authorization == f"Bearer {VISION_KEY}"


# ── BrowserAgent (RPA executor / workflow rpa_step / page analyzer) ──────────


async def test_browser_agent_vision_is_decided_by_the_registry(
    vision_server: LocalLLMServer,
) -> None:
    from app.perception.browser_agent import BrowserAgent

    platform = _NonVisionPlatform()
    agent = BrowserAgent(vision_provider=platform)
    assert agent.has_vision is False  # no registry vision model
    assert await agent.analyze_screenshot(_PNG_B64, "What is shown?") == (
        "No vision provider configured."
    )

    add_registry_vision_model(vision_server.url)
    # The injected provider cannot see images; the registry still decides.
    assert agent.has_vision is True
    answer = await agent.analyze_screenshot(_PNG_B64, "What is shown?", raise_errors=True)
    assert answer == CAPTION
    assert platform.calls == 0
    _assert_vision_request(vision_server)


async def test_browser_agent_fails_over_along_the_vision_chain(
    vision_server: LocalLLMServer,
) -> None:
    from app.perception.browser_agent import BrowserAgent

    add_registry_vision_model(vision_server.url, model_id="broken-vlm", cost=0.0)
    add_registry_vision_model(vision_server.url, cost=0.5)
    answer = await BrowserAgent().analyze_screenshot(_PNG_B64, "What is shown?", raise_errors=True)
    assert answer == CAPTION
    assert [r.model for r in vision_server.requests] == ["broken-vlm", VISION_MODEL]


# ── MultimodalPipeline (image captioning) ────────────────────────────────────


async def test_pipeline_captions_with_the_registry_vision_model(
    vision_server: LocalLLMServer,
) -> None:
    from app.multimodal.pipeline import MultimodalPipeline

    add_registry_vision_model(vision_server.url, model_id="broken-vlm", cost=0.0)
    add_registry_vision_model(vision_server.url, cost=0.5)
    pipeline = MultimodalPipeline()
    pipeline.set_provider(_NonVisionPlatform())
    job = await pipeline.ingest_image(_PNG_B64, "tenant-vision")

    assert job.status == "completed", job.error
    assert job.spans[0].content == CAPTION
    assert job.metadata["extractor_model"] == "broken-vlm"
    assert [r.model for r in vision_server.requests] == ["broken-vlm", VISION_MODEL]
    _assert_vision_request(vision_server)


async def test_pipeline_without_a_vision_model_fails_honestly() -> None:
    from app.multimodal.pipeline import MultimodalPipeline

    pipeline = MultimodalPipeline()
    pipeline.set_provider(_NonVisionPlatform())
    job = await pipeline.ingest_image(_PNG_B64, "tenant-vision")
    assert job.status == "failed"
    assert job.metadata["extractor_model"] == ""
    assert job.error and "vision" in job.error and "Model Registry" in job.error


async def test_model_gateway_vision_profile_uses_the_registry(
    vision_server: LocalLLMServer,
) -> None:
    from app.ai_router.resolve import ModelNotConfiguredError
    from app.org.model_gateway import ModelGateway

    gateway = ModelGateway()
    with pytest.raises(ModelNotConfiguredError):
        await gateway.select_model("vision", quality_req=0.93, latency_budget_ms=15_000)

    add_registry_vision_model(vision_server.url, model_id="vlm-a", cost=0.0)
    add_registry_vision_model(vision_server.url, model_id="vlm-b", cost=0.5)
    selection = await gateway.select_model("vision", quality_req=0.93, latency_budget_ms=15_000)
    assert selection.profile_name == "vision"
    assert (selection.model_id, selection.fallback_model) == ("vlm-a", "vlm-b")


# ── OCR engine (LLM-vision OCR) ──────────────────────────────────────────────


async def test_ocr_engine_reads_the_page_with_the_registry_ocr_model(
    vision_server: LocalLLMServer,
) -> None:
    from PIL import Image

    from app.ocr.engine import OcrEngine

    add_registry_vision_model(vision_server.url)
    engine = OcrEngine()
    text, _conf, engine_used = await engine._llm_vision_ocr(
        Image.new("RGB", (8, 8), "white"), provider=_NonVisionPlatform()
    )
    assert (text, engine_used) == (CAPTION, "llm_vision")
    _assert_vision_request(vision_server)
