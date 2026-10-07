"""Thinking (reasoning) models on OpenAI-compatible endpoints.

Live finding: the on-prem ``Qwen/Qwen3.5-4B`` (vLLM, reasoning parser) spends a
200-1000 token budget reasoning — ``finish_reason=length``, ``content=None``,
everything in ``reasoning``, ``reasoning_tokens == completion_tokens`` — so a
goal ended with an empty answer. ``chat_template_kwargs={"enable_thinking":
false}`` makes it answer in ~1 s. The Model Registry's per-model ``thinking``
(auto / off / on) drives it; every case here goes through the real OpenAI SDK
over an ``httpx.MockTransport``.
"""

_ISOLATE_PROVIDER_ENV = True

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.core.errors import EmptyCompletionError
from app.providers import openai_compatible as oc
from app.providers.base import CompletionRequest, Message
from app.providers.openai_compatible import OpenAICompatibleProvider, strip_reasoning

LAN = "http://192.168.63.104:30080/v1"
QWEN = "Qwen/Qwen3.5-4B"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("LLM_CLIENT_MAX_RETRIES", "0")
    oc._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    oc._THINKING_AUTO_OFF.clear()
    model_registry.clear_configured()
    yield
    oc._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    oc._THINKING_AUTO_OFF.clear()
    model_registry.clear_configured()


# ── helpers ──────────────────────────────────────────────────────────────────


def _completion(
    content: str | None,
    *,
    finish: str = "stop",
    reasoning: str | None = None,
    reasoning_tokens: int = 0,
    completion_tokens: int = 2,
    model: str = QWEN,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "reasoning": reasoning},
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": 16,
            "completion_tokens": completion_tokens,
            "total_tokens": 16 + completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }


def _reasoning_only(max_tokens: int = 300, *, finish: str = "length") -> dict[str, Any]:
    """What the live Qwen returns with thinking on and a small budget."""
    return _completion(
        None,
        finish=finish,
        reasoning="Thinking Process:\n\n1. **Analyze the Request:**",
        reasoning_tokens=max_tokens,
        completion_tokens=max_tokens,
    )


class _Server:
    """Records every chat request; answers from *handler(body) -> httpx.Response*."""

    def __init__(self, handler: Callable[[dict[str, Any]], httpx.Response]) -> None:
        self.handler = handler
        self.bodies: list[dict[str, Any]] = []
        self.hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.bodies.append(body)
        self.hosts.append(request.url.host)
        return self.handler(body)


def _thinking_off(body: dict[str, Any]) -> bool:
    return (body.get("chat_template_kwargs") or {}).get("enable_thinking") is False


def _qwen_like(body: dict[str, Any]) -> httpx.Response:
    """A thinking model: reasoning-only unless thinking is off."""
    if _thinking_off(body):
        return httpx.Response(200, json=_completion("OK"))
    return httpx.Response(200, json=_reasoning_only(body.get("max_tokens", 300)))


def _provider(server: _Server, base_url: str | None = LAN, **kw: Any) -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return OpenAICompatibleProvider(
        api_key="EMPTY", base_url=base_url, default_model=QWEN, http_client=client, **kw
    )


def _req(max_tokens: int = 300, model: str = QWEN) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="What is 17*23? Reply with the number.")],
        model=model,
        max_tokens=max_tokens,
    )


def _register(thinking: str | None = None, budget: int | None = None, *,
              base_url: str | None = LAN, provider: str = "openai_compatible",
              model: str = QWEN) -> None:
    extra: dict[str, Any] = {"source": "override"}
    if thinking:
        extra["thinking"] = thinking
    if budget:
        extra["thinking_budget_tokens"] = budget
    model_registry.register_configured(
        ModelEndpoint(provider=provider, model_id=model, display_name=model,
                      capabilities=[ModelCapability.TEXT_GENERATION], base_url=base_url,
                      extra=extra)
    )


def _counter(outcome: str) -> float:
    from app.observability.metrics import LLM_THINKING_DISABLED_TOTAL

    return LLM_THINKING_DISABLED_TOTAL.labels(outcome=outcome)._value.get()


# ── strip_reasoning ──────────────────────────────────────────────────────────


def test_strip_reasoning_drops_only_a_leading_block() -> None:
    assert strip_reasoning("<think>\nhmm\n</think>\n\n391") == "391"
    assert strip_reasoning("hmm, 17*23...\n</think>\n391") == "391"  # template opened it
    assert strip_reasoning("<think>still thinking when the budget ran out") == ""
    assert strip_reasoning("391") == "391"
    assert strip_reasoning("") == ""


# ── "off" ────────────────────────────────────────────────────────────────────


async def test_thinking_off_sends_the_kwarg_from_the_first_call() -> None:
    _register("off")
    server = _Server(_qwen_like)
    resp = await _provider(server).complete(_req())
    assert resp.content == "OK"
    assert len(server.bodies) == 1 and _thinking_off(server.bodies[0])
    assert resp.thinking_disabled is True


async def test_thinking_off_on_a_server_that_refuses_the_kwarg_still_answers() -> None:
    _register("off")

    def handler(body: dict[str, Any]) -> httpx.Response:
        if "chat_template_kwargs" in body:
            return httpx.Response(400, json={"error": {
                "message": "property 'chat_template_kwargs' is unsupported", "code": 400}})
        return httpx.Response(200, json=_completion("391"))

    server = _Server(handler)
    resp = await _provider(server).complete(_req())
    assert resp.content == "391" and resp.thinking_disabled is False
    assert [("chat_template_kwargs" in b) for b in server.bodies] == [True, False]
    # Remembered: the next call does not send it again.
    await _provider(server).complete(_req())
    assert "chat_template_kwargs" not in server.bodies[-1]


# ── "auto" ───────────────────────────────────────────────────────────────────


async def test_auto_retries_a_reasoning_only_reply_with_thinking_off() -> None:
    server = _Server(_qwen_like)  # no registry entry: auto
    recovered = _counter("recovered")
    provider = _provider(server)

    resp = await provider.complete(_req(300))
    assert resp.content == "OK"
    assert [_thinking_off(b) for b in server.bodies] == [False, True]
    assert server.bodies[1]["max_tokens"] == 300  # not the doubled-budget retry
    assert resp.thinking_observed is True and resp.thinking_disabled is True
    assert _counter("recovered") == recovered + 1

    # Remembered per endpoint + model: later calls go straight to thinking off.
    resp = await provider.complete(_req(300))
    assert resp.content == "OK" and len(server.bodies) == 3 and _thinking_off(server.bodies[2])


async def test_auto_treats_reasoning_with_finish_stop_as_no_answer() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        if _thinking_off(body):
            return httpx.Response(200, json=_completion("391"))
        return httpx.Response(200, json=_reasoning_only(40, finish="stop"))

    server = _Server(handler)
    resp = await _provider(server).complete(_req())
    assert resp.content == "391"  # the reasoning text was never taken as the answer
    assert len(server.bodies) == 2 and _thinking_off(server.bodies[1])


async def test_auto_treats_an_inline_think_block_as_no_answer() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        if _thinking_off(body):
            return httpx.Response(200, json=_completion("391"))
        return httpx.Response(200, json=_completion("<think>17*23 is", finish="length"))

    server = _Server(handler)
    resp = await _provider(server).complete(_req())
    assert resp.content == "391" and len(server.bodies) == 2


async def test_inline_think_block_is_stripped_from_an_answer() -> None:
    server = _Server(lambda b: httpx.Response(
        200, json=_completion("<think>17*23 = 391</think>\n\n391")))
    resp = await _provider(server).complete(_req())
    assert resp.content == "391" and len(server.bodies) == 1
    assert resp.thinking_observed is True


async def test_auto_rejected_kwarg_falls_back_to_the_doubled_budget_and_is_cached() -> None:
    unsupported = _counter("unsupported")

    def handler(body: dict[str, Any]) -> httpx.Response:
        if "chat_template_kwargs" in body:
            return httpx.Response(400, json={"error": {
                "message": "Unrecognized request argument: chat_template_kwargs", "code": 400}})
        if body["max_tokens"] >= 600:
            return httpx.Response(200, json=_completion("391", completion_tokens=500,
                                                        reasoning_tokens=480))
        return httpx.Response(200, json=_reasoning_only(body["max_tokens"]))

    server = _Server(handler)
    provider = _provider(server)
    resp = await provider.complete(_req(300))
    assert resp.content == "391"
    assert [("chat_template_kwargs" in b, b["max_tokens"]) for b in server.bodies] == [
        (False, 300), (True, 300), (False, 600)]
    assert LAN in oc._TEMPLATE_KWARGS_UNSUPPORTED
    assert _counter("unsupported") == unsupported + 1

    # Cached: the kwarg is never tried again on this endpoint.
    server.bodies.clear()
    resp = await provider.complete(_req(300))
    assert resp.content == "391"
    assert [("chat_template_kwargs" in b, b["max_tokens"]) for b in server.bodies] == [
        (False, 300), (False, 600)]


async def test_auto_still_raises_when_everything_is_empty() -> None:
    still_empty = _counter("still_empty")
    server = _Server(lambda b: httpx.Response(200, json=_reasoning_only(b["max_tokens"])))
    with pytest.raises(EmptyCompletionError, match="reasoning only"):
        await _provider(server).complete(_req(300))
    # first call, thinking-off retry, doubled-budget retry — then fallback routing
    assert [(_thinking_off(b), b["max_tokens"]) for b in server.bodies] == [
        (False, 300), (True, 300), (False, 600)]
    assert _counter("still_empty") == still_empty + 1


async def test_auto_does_nothing_new_for_a_non_thinking_empty_reply() -> None:
    server = _Server(lambda b: httpx.Response(200, json=_completion(None, finish="length")))
    with pytest.raises(EmptyCompletionError):
        await _provider(server).complete(_req(300))
    assert [("chat_template_kwargs" in b) for b in server.bodies] == [False, False]


# ── "on" ─────────────────────────────────────────────────────────────────────


async def test_thinking_on_keeps_reasoning_and_adds_the_budget() -> None:
    _register("on", 2048)
    server = _Server(lambda b: httpx.Response(200, json=_completion(
        "391", reasoning="17*23...", reasoning_tokens=900, completion_tokens=903)))
    resp = await _provider(server).complete(_req(500))
    assert resp.content == "391" and resp.reasoning_tokens == 900
    assert server.bodies[0]["max_tokens"] == 500 + 2048
    assert "chat_template_kwargs" not in server.bodies[0]


async def test_thinking_on_overrides_an_endpoint_default_of_off() -> None:
    _register("on", provider="onprem")
    server = _Server(lambda b: httpx.Response(200, json=_completion("391")))
    provider = _provider(server, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    await provider.complete(_req())
    assert server.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}


async def test_thinking_on_does_not_auto_disable() -> None:
    _register("on")
    server = _Server(_qwen_like)
    with pytest.raises(EmptyCompletionError):
        await _provider(server).complete(_req(300))
    assert not any(_thinking_off(b) for b in server.bodies)


# ── hosts that must never get chat_template_kwargs ───────────────────────────


@pytest.mark.parametrize("base_url", [None, "https://api.openai.com/v1",
                                      "https://api.groq.com/openai/v1"])
async def test_hosted_apis_never_get_the_kwarg(base_url: str | None) -> None:
    _register("off", base_url=base_url)
    server = _Server(lambda b: httpx.Response(200, json=_reasoning_only(b["max_tokens"])))
    with pytest.raises(EmptyCompletionError):
        await _provider(server, base_url=base_url).complete(_req(300))
    assert server.bodies and not any("chat_template_kwargs" in b for b in server.bodies)
    if base_url is None:
        assert set(server.hosts) == {"api.openai.com"}


async def test_nvidia_nemotron_off_uses_the_system_prompt_switch() -> None:
    model = "nvidia/llama-3.3-nemotron-super-49b-v1"
    base = "https://integrate.api.nvidia.com/v1"
    _register("off", base_url=base, provider="nvidia", model=model)
    server = _Server(lambda b: httpx.Response(200, json=_completion("391", model=model)))
    resp = await _provider(server, base_url=base).complete(_req(model=model))
    body = server.bodies[0]
    assert "chat_template_kwargs" not in body
    assert body["messages"][0] == {"role": "system", "content": "detailed thinking off"}
    assert resp.thinking_disabled is True


async def test_nvidia_other_models_are_left_unchanged() -> None:
    model = "moonshotai/kimi-k2-thinking"
    base = "https://integrate.api.nvidia.com/v1"
    _register("off", base_url=base, provider="nvidia", model=model)
    server = _Server(lambda b: httpx.Response(200, json=_completion("391", model=model)))
    await _provider(server, base_url=base).complete(_req(model=model))
    body = server.bodies[0]
    assert "chat_template_kwargs" not in body
    assert body["messages"][0]["role"] == "user"


# ── registry lookup ──────────────────────────────────────────────────────────


def test_registry_setting_is_matched_by_endpoint() -> None:
    from app.ai_router.model_endpoints import registry_thinking_settings

    _register("off", base_url=LAN, provider="openai_compatible")
    _register(None, base_url="http://10.0.0.9:8000/v1", provider="onprem")
    assert registry_thinking_settings(QWEN, LAN) == ("off", None)
    # The same model at another endpoint without a setting: auto there.
    assert registry_thinking_settings(QWEN, "http://10.0.0.9:8000/v1") == (None, None)
    assert registry_thinking_settings("unknown-model", LAN) == (None, None)


# ── streaming ────────────────────────────────────────────────────────────────


def _sse(chunks: list[dict[str, Any]]) -> httpx.Response:
    lines = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=lines, headers={"content-type": "text/event-stream"})


def _chunk(content: str | None = None, reasoning: str | None = None,
           usage: dict[str, Any] | None = None) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    out: dict[str, Any] = {"id": "c", "object": "chat.completion.chunk", "created": 1,
                           "model": QWEN, "choices": [{"index": 0, "delta": delta}]}
    if usage is not None:
        out["choices"] = []
        out["usage"] = usage
    return out


async def test_stream_tokens_never_emits_a_think_block() -> None:
    server = _Server(lambda b: _sse([
        _chunk("<thi"), _chunk("nk>17*23"), _chunk(" = 391</thi"), _chunk("nk>\n\n39"),
        _chunk("1")]))
    tokens: list[str] = []

    async def on_token(t: str) -> None:
        tokens.append(t)

    resp = await _provider(server).stream_tokens(_req(), on_token)
    assert "".join(tokens) == "391" and resp.content == "391"
    assert resp.thinking_observed is True


async def test_reasoning_only_stream_is_answered_with_thinking_off() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        if body.get("stream"):
            return _sse([_chunk(reasoning="Thinking Process"), _chunk(reasoning="...")])
        return _qwen_like(body)

    server = _Server(handler)
    tokens: list[str] = []

    async def on_token(t: str) -> None:
        tokens.append(t)

    resp = await _provider(server).stream_tokens(_req(), on_token)
    assert resp.content == "OK" and tokens == []  # nothing reasoning-ish was streamed
    assert [b.get("stream", False) for b in server.bodies] == [True, False, False]
    assert _thinking_off(server.bodies[-1])


async def test_stream_complete_off_sends_the_kwarg_and_strips_reasoning() -> None:
    _register("off")
    server = _Server(lambda b: _sse([_chunk("<think></think>"), _chunk("39"), _chunk("1")]))
    out = [t async for t in _provider(server).stream_complete(_req())]
    assert "".join(out) == "391"
    assert _thinking_off(server.bodies[0])


async def test_plain_stream_passes_through_untouched() -> None:
    server = _Server(lambda b: _sse([_chunk("Hel"), _chunk("lo")]))
    out = [t async for t in _provider(server).stream_complete(_req())]
    assert out == ["Hel", "lo"]
    assert "chat_template_kwargs" not in server.bodies[0]
