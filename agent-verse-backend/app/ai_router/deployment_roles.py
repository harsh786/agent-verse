"""Which configured model serves each agent role, from the deployment's own config.

Both routers (``ModelRouter`` and ``ModelOrchestratorAdapter``) used to pick a
reasoning model from the configured-model registry by "cheapest, then highest
quality". Self-hosted and free-tier models are all unpriced (cost 0), and the
registry was seeded only with ``NVIDIA_MODEL`` — the on-prem Qwen/Gemma models
were never registered — so on a hybrid deployment the slow hosted model served
EVERY role. Measured against the real cluster: every executor call went to
kimi-k3 on NVIDIA (~100 s each, 763 s of a 774 s goal) while the 2 s on-prem
Qwen sat idle, and the ``model_route_selected`` event claimed the executor was
Qwen.

The role map is PROVIDER-AWARE: it is computed per goal from the models the
goal's own provider can serve (:func:`servable_models`). A goal running on a
tenant-configured Anthropic/OpenAI provider, or in a worker whose provider has
only one endpoint, gets an empty map and keeps the router's previous logic —
it is never handed an on-prem model id its provider cannot serve.

Resolution order, per role (restricted to servable models):

1. Operator pins — ``Settings.default_{planning,execution,verification}_model``
   (env ``DEFAULT_*_MODEL``, including ``.env``).
2. The deployment profile implied by what is configured:

   * on-prem + NVIDIA ("hybrid") — NVIDIA plans (the top model); the local
     reasoning model executes and verifies (fast; per-step traffic stays local);
   * on-prem only — the local reasoning model for every role, the small/fast
     model for verification only when its context window is KNOWN to be at
     least :data:`MIN_ROLE_CONTEXT` (fail closed: an unprobed window is unsafe);
   * NVIDIA only — NVIDIA for every role.

3. Nothing configured/servable → ``{}``.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = [
    "MIN_ROLE_CONTEXT",
    "deployment_role_models",
    "probe_model_windows",
    "record_context_window",
    "role_fallback_chain",
    "servable_models",
]

# Smallest context window we hand a planner/executor/verifier prompt to.
MIN_ROLE_CONTEXT = 4096

# model id -> max_model_len, filled by probe_model_windows() at startup.
_CONTEXT_WINDOWS: dict[str, int] = {}

_ROLES = ("planning", "execution", "verification")
ROLE_ALIASES = {
    "reflection": "planning",
    "think": "planning",
    "thinking": "planning",
    "supervisor": "planning",
    "classification": "execution",
}


def record_context_window(model: str, max_len: int | None) -> None:
    if model and isinstance(max_len, int) and max_len > 0:
        _CONTEXT_WINDOWS[model] = max_len


def _known_to_fit(model: str) -> bool:
    window = _CONTEXT_WINDOWS.get(model)
    return window is not None and window >= MIN_ROLE_CONTEXT


def _too_small(model: str) -> bool:
    window = _CONTEXT_WINDOWS.get(model)
    return window is not None and window < MIN_ROLE_CONTEXT


def servable_models(provider: Any) -> set[str] | None:
    """Model ids *provider* can route, or ``None`` when it is not a multi-endpoint dispatcher.

    Only the deployment's model→endpoint dispatcher (``build_onprem_provider``:
    on-prem, NVIDIA, or both) gets a role map; any other provider keeps the
    router's existing single-provider behaviour.
    """
    endpoints = getattr(provider, "_endpoints", None)
    if isinstance(endpoints, dict) and endpoints:
        return {str(m) for m in endpoints}
    return None


def _pin(settings: Any, role: str) -> str:
    value = str(getattr(settings, f"default_{role}_model", "") or "").strip()
    return value or (os.getenv(f"DEFAULT_{role.upper()}_MODEL") or "").strip()


def deployment_role_models(
    settings: Any = None, *, servable: set[str] | None = None
) -> dict[str, str]:
    """``{"planning"|"execution"|"verification": model}`` for this deployment.

    With ``servable`` given, only models in it are returned (a role whose model
    the provider cannot serve is omitted).
    """
    from app.core.config import get_settings

    s = settings or get_settings()
    onprem = bool(getattr(s, "onprem_enabled", False)) and bool(
        (getattr(s, "onprem_qwen_base_url", "") or "").strip()
    )
    nvidia = bool((getattr(s, "nvidia_api_key", "") or "").strip())
    qwen = (getattr(s, "onprem_qwen_model", "") or "").strip() if onprem else ""
    small = ""
    if onprem and (getattr(s, "onprem_gemma_base_url", "") or "").strip():
        small = (getattr(s, "onprem_gemma_model", "") or "").strip()
    top = (getattr(s, "nvidia_model", "") or "").strip() if nvidia else ""

    roles: dict[str, str] = {}
    if onprem and nvidia:
        roles = {"planning": top, "execution": qwen, "verification": qwen}
    elif onprem:
        verifier = small if small and _known_to_fit(small) else qwen
        roles = {"planning": qwen, "execution": qwen, "verification": verifier}
    elif nvidia:
        roles = {"planning": top, "execution": top, "verification": top}

    for role in _ROLES:
        pinned = _pin(s, role)
        if pinned:
            roles[role] = pinned
    return {
        role: model
        for role, model in roles.items()
        if model and (servable is None or model in servable) and not _too_small(model)
    }


def role_fallback_chain(role_map: dict[str, str]) -> list[str]:
    """Distinct models of a role map, fastest-first (execution, verification, planning)."""
    ordered = [role_map.get("execution", ""), role_map.get("verification", ""),
               role_map.get("planning", "")]
    return [m for i, m in enumerate(ordered) if m and m not in ordered[:i]]


async def probe_model_windows(settings: Any = None, *, timeout: float = 3.0) -> dict[str, int]:
    """Read ``max_model_len`` from each configured OpenAI-compatible ``/v1/models``.

    Best-effort: an unreachable endpoint leaves its models unknown, which keeps
    the small model OUT of the verification role (fail closed).
    """
    import httpx

    from app.core.config import get_settings

    s = settings or get_settings()
    bases = {
        (getattr(s, "onprem_qwen_base_url", "") or "").strip(),
        (getattr(s, "onprem_gemma_base_url", "") or "").strip(),
    } - {""}
    async with httpx.AsyncClient(timeout=timeout) as client:
        for base in bases:
            try:
                resp = await client.get(base.rstrip("/") + "/models")
                for m in resp.json().get("data", []):
                    record_context_window(str(m.get("id", "")), m.get("max_model_len"))
            except Exception:
                continue
    return dict(_CONTEXT_WINDOWS)
