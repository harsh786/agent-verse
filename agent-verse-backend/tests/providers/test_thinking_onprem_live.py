"""LIVE: the on-prem thinking model ``Qwen/Qwen3.5-4B`` (vLLM, reasoning parser).

Opt-in (real network call, nothing mocked)::

    AGENTVERSE_LIVE_ONPREM=1 uv run pytest tests/providers/test_thinking_onprem_live.py -m slow

``RW_ONPREM_CHAT_URL`` overrides the endpoint (default
``http://192.168.63.104:30080/v1``). Skipped when the endpoint is unreachable.
Read-only: two short chat completions per test.
"""

_ISOLATE_PROVIDER_ENV = True

import os
import time

import httpx
import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.providers import openai_compatible as oc
from app.providers.base import CompletionRequest, Message
from app.providers.openai_compatible import OpenAICompatibleProvider

CHAT_URL = os.getenv("RW_ONPREM_CHAT_URL", "http://192.168.63.104:30080/v1").rstrip("/")
QWEN = "Qwen/Qwen3.5-4B"


def _reachable() -> bool:
    try:
        return httpx.get(f"{CHAT_URL}/models", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("AGENTVERSE_LIVE_ONPREM") != "1",
        reason="live on-prem test: set AGENTVERSE_LIVE_ONPREM=1",
    ),
]


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    if not _reachable():
        pytest.skip(f"on-prem endpoint {CHAT_URL} unreachable")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    oc._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    oc._THINKING_AUTO_OFF.clear()
    model_registry.clear_configured()
    yield
    oc._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    oc._THINKING_AUTO_OFF.clear()
    model_registry.clear_configured()


def _register(thinking: str | None) -> None:
    extra = {"source": "override", **({"thinking": thinking} if thinking else {})}
    model_registry.register_configured(
        ModelEndpoint(provider="openai_compatible", model_id=QWEN, display_name=QWEN,
                      capabilities=[ModelCapability.TEXT_GENERATION], base_url=CHAT_URL,
                      extra=extra)
    )


def _req(max_tokens: int = 300) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="What is 17*23? Reply with just the number.")],
        model=QWEN,
        max_tokens=max_tokens,
    )


async def test_live_qwen_thinking_off_answers_directly() -> None:
    _register("off")
    provider = OpenAICompatibleProvider(api_key="EMPTY", base_url=CHAT_URL, default_model=QWEN)
    start = time.monotonic()
    resp = await provider.complete(_req())
    assert "391" in resp.content, resp
    assert resp.thinking_disabled is True and resp.reasoning_tokens == 0
    assert time.monotonic() - start < 30


async def test_live_qwen_auto_recovers_from_a_reasoning_only_reply() -> None:
    _register(None)  # auto
    provider = OpenAICompatibleProvider(api_key="EMPTY", base_url=CHAT_URL, default_model=QWEN)
    resp = await provider.complete(_req(200))
    assert "391" in resp.content, resp
    assert resp.thinking_observed is True and resp.thinking_disabled is True
    assert (CHAT_URL, QWEN) in oc._THINKING_AUTO_OFF
