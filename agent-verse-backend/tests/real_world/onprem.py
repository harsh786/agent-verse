"""The on-prem vLLM models and the Model Registry calls an operator makes by hand.

Real models on the LAN (base URLs overridable, never mocked):

* ``RW_ONPREM_CHAT_URL``   ``Qwen/Qwen3.5-4B`` — chat / reasoning, max_model_len 32768
* ``RW_ONPREM_EMBED_URL``  ``Qwen/Qwen3-Embedding-0.6B`` — 1024-dim embeddings
* ``RW_ONPREM_RERANK_URL`` ``Qwen/Qwen3-Reranker-0.6B`` — ``/v1/rerank``
* ``RW_ONPREM_SMALL_URL``  ``google/gemma-4-E2B`` — max_model_len 1024 (context overflow)

The registry (``/models/configured``, ``/models/preferences``) is GLOBAL and its
mutations need a platform admin: the tenant key of a platform-admin tenant, or
``RW_PLATFORM_ADMIN_KEY`` sent as ``X-Admin-Key``. Because it is global, every change
a scenario makes is snapshotted first and restored in a ``finally``
(:class:`RegistrySandbox`). Nothing here imports ``app``.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from typing import Any

from tests.real_world.helpers import LiveAPI, mask, register_secret, wait_until

CHAT_URL = os.getenv("RW_ONPREM_CHAT_URL", "http://192.168.63.104:30080/v1").rstrip("/")
EMBED_URL = os.getenv("RW_ONPREM_EMBED_URL", "http://192.168.63.104:30082/v1").rstrip("/")
RERANK_URL = os.getenv("RW_ONPREM_RERANK_URL", "http://192.168.63.104:30083/v1").rstrip("/")
SMALL_URL = os.getenv("RW_ONPREM_SMALL_URL", "http://192.168.63.104:30081/v1").rstrip("/")
PROVIDER = os.getenv("RW_ONPREM_PROVIDER", "onprem")

CHAT_MODEL = "Qwen/Qwen3.5-4B"
EMBED_MODEL = "Qwen/Qwen3-Embedding-0.6B"
RERANK_MODEL = "Qwen/Qwen3-Reranker-0.6B"
SMALL_MODEL = "google/gemma-4-E2B"
EMBED_DIM = 1024
SMALL_CONTEXT = 1024

MODELS: dict[str, dict[str, Any]] = {
    "chat": {"model_id": CHAT_MODEL, "base_url": CHAT_URL, "display_name": "Qwen3.5 4B (on-prem)",
             "capabilities": ["text_generation", "tool_use", "structured_output"],
             "supports_tools": True, "supports_structured_output": True,
             "cost_per_1k_input": 0, "cost_per_1k_output": 0, "quality_score": 0.7},
    "embed": {"model_id": EMBED_MODEL, "base_url": EMBED_URL,
              "display_name": "Qwen3 Embedding 0.6B (on-prem)", "capabilities": ["embedding"],
              "cost_per_1k_input": 0, "cost_per_1k_output": 0},
    "rerank": {"model_id": RERANK_MODEL, "base_url": RERANK_URL,
               "display_name": "Qwen3 Reranker 0.6B (on-prem)", "capabilities": ["rerank"],
               "cost_per_1k_input": 0, "cost_per_1k_output": 0},
    "small": {"model_id": SMALL_MODEL, "base_url": SMALL_URL,
              "display_name": "Gemma 4 E2B (on-prem, 1k context)",
              "capabilities": ["text_generation"], "cost_per_1k_input": 0,
              "cost_per_1k_output": 0, "quality_score": 0.3},
}
# Cloud providers that must never serve an on-prem-pinned goal.
CLOUD_PROVIDERS = ("anthropic", "openai", "azure_openai", "gemini", "google", "voyage", "groq",
                   "xai", "openrouter", "bedrock", "vertex", "mistral", "cohere", "nvidia")
CLOUD_MODEL_HINTS = ("claude", "gpt-", "gemini", "o1", "o3", "o4", "voyage", "mistral-",
                     "command-r", "grok", "llama-3.1-nemotron", "nemotron")


def key(model: dict[str, Any]) -> str:
    return f"{PROVIDER}/{model['model_id']}"


def admin_client(api: LiveAPI) -> LiveAPI:
    """``api`` itself, or a client sending ``X-Admin-Key`` when RW_PLATFORM_ADMIN_KEY is set."""
    admin_key = os.getenv("RW_PLATFORM_ADMIN_KEY", "").strip()
    if not admin_key:
        return api
    register_secret(admin_key)
    client = LiveAPI(str(api.client.headers.get("X-API-Key", "")))
    client.client.headers["X-Admin-Key"] = admin_key
    return client


def require_registry_admin(admin: LiveAPI) -> dict[str, Any]:
    import pytest

    access = admin.get("/models/configured/access")
    body = access.json() if access.status_code == 200 else {}
    if not body.get("can_modify"):
        pytest.skip("the Model Registry needs a platform admin: use a platform-admin tenant "
                    "key or set RW_PLATFORM_ADMIN_KEY (sent as X-Admin-Key); "
                    f"/models/configured/access -> {access.status_code} {mask(body)[:160]}")
    return dict(body)


def test_endpoint(admin: LiveAPI, model: dict[str, Any], **over: Any) -> dict[str, Any]:
    body = {"provider": PROVIDER, "model_id": model["model_id"], "base_url": model["base_url"],
            "capabilities": model["capabilities"], **over}
    started = time.monotonic()
    resp = admin.post("/models/configured/test-endpoint", json=body, timeout=90)
    out = resp.json() if resp.content else {}
    if not isinstance(out, dict):
        out = {"raw": str(out)[:200]}
    out["_http"] = resp.status_code
    out["_s"] = round(time.monotonic() - started, 1)
    return out


def configured(api: LiveAPI) -> dict[str, Any]:
    return dict(api.json_ok("GET", "/models/configured"))


def configured_keys(api: LiveAPI) -> set[str]:
    body = configured(api)
    keys: set[str] = set()
    for group in body.get("capabilities") or []:
        for m in group.get("models") or []:
            keys.add(str(m.get("key") or f"{m.get('provider')}/{m.get('model_id')}"))
    return keys


def capability_group(api: LiveAPI, capability: str) -> dict[str, Any]:
    body = configured(api)
    return next((dict(g) for g in body.get("capabilities") or []
                 if g.get("capability") == capability), {})


def preferences(api: LiveAPI) -> dict[str, list[str]]:
    return dict(api.json_ok("GET", "/models/preferences").get("preferences") or {})


@dataclass
class RegistrySandbox:
    """Snapshot the global registry state a scenario touches; restore it afterwards."""

    admin: LiveAPI
    added: list[str] = field(default_factory=list)  # provider/model_id this run created
    prefs_before: dict[str, list[str]] = field(default_factory=dict)
    touched_prefs: set[str] = field(default_factory=set)
    keys_before: set[str] = field(default_factory=set)
    policies_touched: set[str] = field(default_factory=set)
    log: list[str] = field(default_factory=list)

    def __enter__(self) -> RegistrySandbox:
        self.prefs_before = preferences(self.admin)
        self.keys_before = configured_keys(self.admin)
        return self

    def register(self, model: dict[str, Any], **over: Any) -> dict[str, Any]:
        body = {"provider": PROVIDER, "is_available": True, **model, **over}
        resp = self.admin.post("/models/configured", json=body)
        out = resp.json() if resp.content else {}
        k = f"{body['provider']}/{body['model_id']}"
        if resp.status_code < 300 and k not in self.keys_before and k not in self.added:
            self.added.append(k)
        self.log.append(f"register {k} -> {resp.status_code}")
        return {"_http": resp.status_code, **(out if isinstance(out, dict) else {})}

    def set_order(self, capability: str, order: list[str]) -> dict[str, Any]:
        self.touched_prefs.add(capability)
        resp = self.admin.put(f"/models/preferences/{capability}", json={"order": order})
        self.log.append(f"order {capability} {order} -> {resp.status_code}")
        out = resp.json() if resp.content else {}
        return {"_http": resp.status_code, **(out if isinstance(out, dict) else {})}

    def pin_role(self, task_type: str, model_id: str) -> dict[str, Any]:
        self.policies_touched.add(task_type)
        resp = self.admin.put(f"/models/routing-policies/{task_type}", json={
            "routing_mode": "model_pinned", "preferred_provider": PROVIDER,
            "preferred_model": model_id, "fallback_chain": []})
        out = resp.json() if resp.content else {}
        return {"_http": resp.status_code, **(out if isinstance(out, dict) else {})}

    def __exit__(self, *exc: object) -> None:
        for task_type in sorted(self.policies_touched):
            with contextlib.suppress(Exception):
                self.admin.put(f"/models/routing-policies/{task_type}", json={
                    "routing_mode": "tenant_default", "preferred_provider": "",
                    "preferred_model": "", "fallback_chain": []})
        for capability in sorted(self.touched_prefs):
            before = self.prefs_before.get(capability) or []
            with contextlib.suppress(Exception):
                if before:
                    self.admin.put(f"/models/preferences/{capability}", json={"order": before})
                else:
                    self.admin.delete(f"/models/preferences/{capability}")
        for k in reversed(self.added):
            provider, _, model_id = k.partition("/")
            with contextlib.suppress(Exception):
                self.admin.delete(f"/models/configured/{provider}/{model_id}")


# ── Proof of which model served each role ───────────────────────────────────


def role_trace(api: LiveAPI, goal_id: str, timeout: float = 120) -> dict[str, Any]:
    """``POST /agent-runtime/traces`` then ``GET`` it: role_calls / model_selections."""
    created = api.json_ok("POST", "/agent-runtime/traces", json={"goal_id": goal_id})
    trace_id = str(created.get("trace_id") or created.get("id"))

    def probe() -> dict[str, Any]:
        resp = api.get(f"/agent-runtime/traces/{trace_id}")
        return resp.json() if resp.status_code == 200 else {}

    return dict(wait_until(probe, timeout=timeout, interval=3, desc=f"trace of goal {goal_id}",
                           done=lambda t: bool(t.get("role_calls") or t.get("model_selections")
                                               or t.get("error"))))


def models_by_role(trace: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in (trace.get("role_calls") or []) + (trace.get("model_selections") or []):
        role = str(row.get("role") or "")
        model = str(row.get("model") or "")
        if role and model:
            out.setdefault(role, set()).add(model)
    return out


def cloud_models(models: set[str]) -> list[str]:
    """Models that look like a hosted cloud model (never expected on an on-prem run)."""
    return sorted(m for m in models if any(h in m.lower() for h in CLOUD_MODEL_HINTS)
                  or m.split("/", 1)[0].lower() in CLOUD_PROVIDERS)


def wait_goal(api: LiveAPI, goal_id: str, timeout: float) -> dict[str, Any]:
    return dict(wait_until(lambda: api.json_ok("GET", f"/goals/{goal_id}"), timeout=timeout,
                           interval=5, desc=f"goal {goal_id}",
                           done=lambda g: str(g.get("status")) in (
                               "complete", "completed", "failed", "cancelled")))


def goal_text(goal: dict[str, Any]) -> str:
    for k in ("result", "final_answer", "output", "answer", "summary"):
        v = goal.get(k)
        if v:
            return v if isinstance(v, str) else str(v)
    return ""
