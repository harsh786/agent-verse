"""A real local OpenAI-compatible HTTP server for registry-LLM tests.

Answers ``POST /v1/chat/completions`` (plain and ``stream: true``) by role:
the agent planner gets a one-step JSON plan, the verifier a success verdict,
the grounding checker "grounded", everything else a short text answer. Every
request is recorded (model, role, Authorization header).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry

MODEL = "registry-qwen"
MODEL_KEY = "sk-registry-model-key"
_TG = ModelCapability.TEXT_GENERATION
_TU = ModelCapability.TOOL_USE
_SO = ModelCapability.STRUCTURED_OUTPUT


def _role_of(text: str) -> str:
    if "task planner" in text or "agent planner" in text:
        return "planner"
    if "goal-completion verifier" in text:
        return "verifier"
    if "grounding verifier" in text:
        return "grounding"
    if "task executor" in text:
        return "executor"
    return "other"


_REPLIES = {
    "planner": '{"steps": ["Step 1: say hello"]}',
    "verifier": '{"success": true, "reason": "the greeting was produced"}',
    "grounding": '{"grounded": true, "reason": "ok"}',
}
DEFAULT_REPLY = "Hello from the registry model."


@dataclass
class RecordedRequest:
    path: str
    model: str
    role: str
    authorization: str
    stream: bool
    body: dict[str, Any] = field(default_factory=dict)


class LocalLLMServer:
    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                return

            def do_GET(self) -> None:
                payload = json.dumps({"object": "list", "data": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                text = "\n".join(
                    str(m.get("content") or "") for m in body.get("messages") or []
                )
                role = _role_of(text)
                stream = bool(body.get("stream"))
                server.requests.append(
                    RecordedRequest(
                        path=self.path,
                        model=str(body.get("model") or ""),
                        role=role,
                        authorization=str(self.headers.get("Authorization") or ""),
                        stream=stream,
                        body=body,
                    )
                )
                reply = _REPLIES.get(role, DEFAULT_REPLY)
                model = str(body.get("model") or "")
                if stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    chunk = {
                        "id": "c1",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": model,
                        "choices": [
                            {"index": 0, "delta": {"role": "assistant", "content": reply},
                             "finish_reason": None}
                        ],
                    }
                    done = {
                        "id": "c1",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                                  "total_tokens": 10},
                    }
                    for event in (chunk, done):
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                payload = json.dumps(
                    {
                        "id": "c1",
                        "object": "chat.completion",
                        "created": 0,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": reply},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                                  "total_tokens": 10},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def roles(self) -> set[str]:
        return {r.role for r in self.requests}

    def start(self) -> LocalLLMServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def add_registry_model(base_url: str | None, *, model_id: str = MODEL, key: str = MODEL_KEY,
                        provider: str = "openai_compatible") -> None:
    from app.ai_router.model_endpoints import encrypt_endpoint_api_key

    model_registry.register_configured(
        ModelEndpoint(
            provider=provider,
            model_id=model_id,
            display_name="Registry Qwen",
            capabilities=[_TG, _TU, _SO],
            supports_tools=True,
            supports_structured_output=True,
            base_url=base_url,
            extra={
                "source": "override",
                "origin": "manual",
                "api_key_encrypted": encrypt_endpoint_api_key(key),
            },
        )
    )


def find_registry(p: Any) -> Any:
    """The RegistryLLMProvider inside role / trace / circuit wrappers, else *p*."""
    from app.providers.registry_llm import RegistryLLMProvider

    for _ in range(10):
        if isinstance(p, RegistryLLMProvider):
            return p
        own = getattr(p, "__dict__", {})
        nxt = next(
            (own[a] for a in ("_inner", "_provider", "_wrapped", "inner", "provider")
             if own.get(a) is not None),
            None,
        )
        if nxt is None:
            return p
        p = nxt
    return p


class _DictRedis:
    """The few sync-redis calls ModelRegistryStore makes, in memory."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, k: str) -> str | None:
        return self.data.get(k)

    def set(self, k: str, v: str) -> None:
        self.data[k] = v

    def incr(self, k: str) -> int:
        self.data[k] = str(int(self.data.get(k) or 0) + 1)
        return int(self.data[k])


def shared_store_with_registry_model(base_url: str, *, model_id: str = MODEL,
                                     key: str = MODEL_KEY) -> Any:
    """A shared ModelRegistryStore holding one operator-added text model."""
    from app.ai_router.model_endpoints import encrypt_endpoint_api_key
    from app.ai_router.registry_store import ModelRegistryStore

    store = ModelRegistryStore(_DictRedis())
    store.upsert(
        {
            "provider": "openai_compatible",
            "model_id": model_id,
            "display_name": "Registry Qwen",
            "capabilities": ["text_generation", "tool_use", "structured_output"],
            "supports_tools": True,
            "supports_structured_output": True,
            "base_url": base_url,
            "api_key_encrypted": encrypt_endpoint_api_key(key),
        }
    )
    return store
