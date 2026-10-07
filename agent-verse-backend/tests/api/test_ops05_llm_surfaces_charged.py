"""OPS-05: skills, insights, multimodal and OCR LLM calls are charged, use BYOK.

Each surface called ``provider.complete`` on the platform provider: no tenant
budget preflight or charge, no circuit breaker, and a tenant's own key (BYOK)
was ignored. Multimodal also mutated the shared pipeline's provider per request,
and OCR re-resolved a fresh platform provider for every page because
``app.state.provider`` is never set.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.providers import guarded_completion as gc
from app.providers.base import CompletionResponse
from app.providers.guarded_completion import DecisionBudgetExceededError
from app.tenancy.context import PlanTier, TenantContext
from tests.ai_router._vision_registry import registry_vision  # noqa: F401  (fixture)

_CTX = TenantContext(tenant_id="t-ops05", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Provider:
    def __init__(self, reply: str = "ok", name: str = "platform") -> None:
        self._default_model = "gpt-4o-mini"
        self.name, self.reply = name, reply
        self.calls = 0

    def supports_vision(self) -> bool:
        return True

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            content=self.reply, model="gpt-4o-mini", input_tokens=50, output_tokens=5
        )


@dataclass
class _Controller:
    remaining: bool = True
    recorded: list[str] = field(default_factory=list)

    async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any) -> bool:
        self.recorded.append(tenant_ctx.tenant_id)
        return True

    async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
        return self.remaining


@pytest.fixture
def controller() -> Any:
    saved = gc._platform_services
    ctrl = _Controller()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    yield ctrl
    gc.set_platform_cost_services(saved)


class _Store:
    """LLM config store with (or without) a BYOK config for the tenant."""

    def __init__(self, cfg: dict[str, Any] | None) -> None:
        self.cfg = cfg

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        return self.cfg


def _request(platform: Any, *, byok: Any = None, monkeypatch: Any = None, **state: Any) -> Any:
    request = MagicMock()
    request.state = SimpleNamespace(tenant=_CTX)
    cfg = {"provider": "openai", "encrypted_key": "x"} if byok is not None else None
    request.app.state = SimpleNamespace(
        _app_provider=platform, llm_config_store=_Store(cfg), **state
    )
    if byok is not None and monkeypatch is not None:
        import app.providers.tenant_provider as tp

        monkeypatch.setattr(tp, "build_tenant_provider", lambda cfg, *, tenant_id: byok)
    return request


# ── skills ────────────────────────────────────────────────────────────────────


async def test_execute_skill_uses_byok_and_charges_the_tenant(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.skills_runtime import ExecuteSkillRequest, _platform_skills, execute_skill

    platform, byok = _Provider(name="platform"), _Provider("done", name="byok")
    skill_id = next(iter(_platform_skills))
    out = await execute_skill(
        _request(platform, byok=byok, monkeypatch=monkeypatch),
        skill_id,
        ExecuteSkillRequest(input_context="hi"),
    )
    assert out["success"] is True
    assert (byok.calls, platform.calls) == (1, 0)
    assert controller.recorded == ["t-ops05"]


async def test_execute_skill_budget_refusal_is_raised(controller: _Controller) -> None:
    from app.api.skills_runtime import ExecuteSkillRequest, _platform_skills, execute_skill

    controller.remaining = False
    platform = _Provider()
    with pytest.raises(DecisionBudgetExceededError):
        await execute_skill(
            _request(platform), next(iter(_platform_skills)), ExecuteSkillRequest(input_context="x")
        )
    assert platform.calls == 0


async def test_skill_executor_charges_and_surfaces_refusal(controller: _Controller) -> None:
    from app.skills_runtime.executor import SkillExecutor
    from app.skills_runtime.models import SkillDefinition, SkillScope

    skill = SkillDefinition(
        skill_id="s", name="S", description="d", scope=list(SkillScope)[0], instructions="i"
    )
    provider = _Provider()
    executor = SkillExecutor(provider=provider)
    executor._permission_checker.is_allowed = lambda *a, **k: True  # type: ignore[method-assign]
    result = await executor.execute(skill=skill, input_context="x", tenant_id="t-ops05")
    assert result.success and controller.recorded == ["t-ops05"]

    controller.remaining = False
    with pytest.raises(DecisionBudgetExceededError):
        await executor.execute(skill=skill, input_context="x", tenant_id="t-ops05")
    assert provider.calls == 1


# ── insights ──────────────────────────────────────────────────────────────────


class _GoalService:
    async def get_goal(self, *, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {"goal": "g", "status": "failed", "verification_feedback": "timeout", "steps": []}


async def test_analyze_failure_uses_byok_charges_and_surfaces_refusal(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.insights import analyze_failure

    platform = _Provider()
    byok = _Provider('{"failure_reason": "slow", "suggestions": []}', name="byok")
    req = _request(platform, byok=byok, monkeypatch=monkeypatch, goal_service=_GoalService())
    out = await analyze_failure("g1", req)
    assert out["failure_reason"] == "slow"
    assert (byok.calls, platform.calls) == (1, 0)
    assert controller.recorded == ["t-ops05"]

    controller.remaining = False
    with pytest.raises(DecisionBudgetExceededError):
        await analyze_failure("g1", req)
    assert byok.calls == 1


async def test_nl_query_budget_refusal_is_raised(controller: _Controller) -> None:
    from app.api.insights import NLQueryRequest, natural_language_query

    controller.remaining = False
    platform = _Provider('{"days": 1}')
    with pytest.raises(DecisionBudgetExceededError):
        await natural_language_query(_request(platform), NLQueryRequest(query="failed goals"))
    assert platform.calls == 0


# ── multimodal ────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("registry_vision")
async def test_multimodal_image_uses_per_call_provider_and_charges(
    controller: _Controller,
) -> None:
    from app.multimodal.pipeline import MultimodalPipeline

    pipeline = MultimodalPipeline()
    shared = _Provider("shared")
    pipeline.set_provider(shared)
    tenant_provider = _Provider("a cat")
    job = await pipeline.ingest_image("aGVsbG8=", "t-ops05", provider=tenant_provider)
    assert job.status == "completed" and job.spans[0].content == "a cat"
    assert (tenant_provider.calls, shared.calls) == (1, 0)
    assert pipeline._provider is shared  # not mutated per request
    assert controller.recorded == ["t-ops05"]


@pytest.mark.usefixtures("registry_vision")
async def test_multimodal_api_does_not_mutate_the_shared_pipeline(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.multimodal import IngestRequest, ingest_asset
    from app.multimodal.pipeline import MultimodalPipeline

    pipeline = MultimodalPipeline()
    byok = _Provider("a dog", name="byok")
    req = _request(_Provider(), byok=byok, monkeypatch=monkeypatch, multimodal_pipeline=pipeline)
    out = await ingest_asset(req, IngestRequest(modality="image", base64_data="aGVsbG8="))
    assert out["status"] == "completed"
    assert byok.calls == 1 and pipeline._provider is None


@pytest.mark.usefixtures("registry_vision")
async def test_multimodal_budget_refusal_is_raised(controller: _Controller) -> None:
    from app.multimodal.pipeline import MultimodalPipeline

    controller.remaining = False
    provider = _Provider()
    with pytest.raises(DecisionBudgetExceededError):
        await MultimodalPipeline().ingest_image("aGVsbG8=", "t-ops05", provider=provider)
    assert provider.calls == 0


# ── OCR ───────────────────────────────────────────────────────────────────────


def _png_b64() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def test_ocr_vision_charges_and_surfaces_refusal(controller: _Controller) -> None:
    from PIL import Image

    from app.ocr.engine import OcrEngine

    provider = _Provider("TEXT")
    img = Image.new("RGB", (8, 8), "white")
    text, _conf, engine = await OcrEngine()._llm_vision_ocr(
        img, provider=provider, tenant_id="t-ops05"
    )
    assert (text, engine) == ("TEXT", "llm_vision")
    assert controller.recorded == ["t-ops05"]

    controller.remaining = False
    with pytest.raises(DecisionBudgetExceededError):
        await OcrEngine()._llm_vision_ocr(img, provider=provider, tenant_id="t-ops05")


async def test_ocr_structured_extractor_surfaces_refusal(controller: _Controller) -> None:
    from app.ocr.extractors.general import LlmStructuredExtractor

    controller.remaining = False
    provider = _Provider("{}")
    with gc.tenant_charge_scope(_CTX), pytest.raises(DecisionBudgetExceededError):
        await LlmStructuredExtractor(provider).extract_async("some text")
    assert provider.calls == 0


async def test_ocr_api_passes_the_tenant_provider_once(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.ocr as ocr_api

    seen: list[Any] = []

    async def _execute(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("provider"))
        return {"raw_text": "", "document_type": "general", "fields": {},
                "engine_used": "none", "overall_confidence": 0.0, "page_count": 1}

    monkeypatch.setattr(ocr_api._tool, "execute", _execute)
    byok = _Provider(name="byok")
    req = _request(_Provider(), byok=byok, monkeypatch=monkeypatch)

    async def _json() -> dict[str, Any]:
        return {"image_base64": _png_b64()}

    req.json = _json
    await ocr_api.extract_document(req, file=None, persist_to_kb=False, collection_id="",
                                   filename="document")
    assert seen == [byok]
