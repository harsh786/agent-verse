"""No LLM role picks its model (or builds its provider) around the role resolver.

Every role of the agent/goal runtime and its adjacent subsystems must get its
model from ``app.ai_router.role_preference.resolve_role_model`` (or a router's
``model_for`` with a task type the routers know). The supervisor bypassed it by
sending ``planner._default_model`` — the env-default cloud model — while every
other role followed the operator's on-prem ranking. This scan fails when new code
in role code reintroduces one of those shortcuts:

* reading a provider's ``_default_model`` as the request model;
* ``configured_default_model(...)`` (the env default) as a role's model;
* ``model_for("<x>")`` with a task type the routers do not route (it silently
  falls through to the env single-model fallback — ``"refine"`` did);
* constructing / resolving an LLM provider directly instead of using the goal's
  deployment provider.

A legitimate exception goes in ``ALLOWED`` with the reason.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.ai_router.role_preference import ROUTED_TASK_TYPES

APP = Path(__file__).resolve().parents[2] / "app"

# Role code: the agent/goal runtime and the subsystems whose LLM calls are roles.
SCOPE = (
    "agent",
    "orchestration",
    "coordination",
    "intelligence",
    "evals",
    "memory",
    "guardrails_v2",
    "rag",
    "rag_platform",
    "agent_runtime",
)

PATTERNS: dict[str, re.Pattern[str]] = {
    "provider_default_as_model": re.compile(r"""getattr\([^\n]*["']_default_model["']"""),
    "env_default_model": re.compile(r"\bconfigured_default_model\("),
    "direct_provider_construction": re.compile(
        r"\b(?:Anthropic|OpenAICompatible|Gemini|Groq|Ollama|Nvidia|OnPrem|Voyage)Provider\("
        r"|\bbuild_onprem_provider\(|\bresolve_provider\(|\b_instantiate_provider\("
    ),
}
_MODEL_FOR = re.compile(r"""\.model_for(?:_goal)?\(\s*["'](\w+)["']""")

# (path relative to app/, substring of the line) -> why it is not a role bypass.
ALLOWED: dict[tuple[str, str], str] = {
    ("agent/graph.py", 'return str(getattr(provider, "_default_model", "") or "")'):
        "_routed_model's last resort if the resolver itself raised",
    ("agent/graph.py", 'candidates.append(getattr(getattr(self, "_executor", None)'):
        "failover chain: the provider default is the last fallback, never the primary",
    ("agent/nodes/reasoning_mixin.py", 'return str(getattr(provider, "_default_model", "")'):
        "_role_model's last resort if the resolver itself raised",
    ("agent/nodes/llm_cost.py", 'return self._model or str(getattr(self._inner, "_default_model"'):
        "ChargingProvider: the resolved role model first, the inner default only without one",
    ("agent/nodes/llm_cost.py", 'inner_default = str(getattr(self._inner, "_default_model"'):
        "ChargingProvider: detects a request for the provider default to re-route it",
    ("agent/nodes/planner_mixin.py", ') or configured_default_model("")'):
        "planner: only when the resolver returned nothing (no provider default)",
    ("intelligence/guardrail_engine.py", 'configured_default_model("gpt-4o-mini")'):
        "guardrail judge: only when the resolver returned nothing",
    ("intelligence/self_optimizer_v2.py", 'or configured_default_model("claude-haiku-3-5")'):
        "self optimizer: only when the resolver returned nothing",
    ("coordination/pattern_runs/llm.py", 'for attr in ("default_model", "_default_model", "model"):'):
        "provider_model(): identity of an explicit MoA deployment, not a role choice",
    ("coordination/pattern_runs/tasks.py", "resolved = resolve_provider()"):
        "the deployment provider (same precedence as the API / goal worker)",
    ("coordination/pattern_runs/moa.py", "provider = _instantiate_provider(cfg)"):
        "MoA proposers are, by design, every configured provider at its own model "
        "(app.state.moa_providers); the run's primary deployment uses the resolver",
    ("rag/gateway.py", 'or getattr(self._embedder, "_default_model", "")'):
        "embedder model label for the embedding cache (not an LLM role)",
}


def _scan() -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    for sub in SCOPE:
        for path in sorted((APP / sub).rglob("*.py")):
            rel = path.relative_to(APP).as_posix()
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for name, pattern in PATTERNS.items():
                    if pattern.search(line) and not any(
                        rel == a_path and snippet in line for (a_path, snippet) in ALLOWED
                    ):
                        hits.append((rel, lineno, name, stripped))
                for match in _MODEL_FOR.finditer(line):
                    if match.group(1) not in ROUTED_TASK_TYPES:
                        hits.append((rel, lineno, f"unrouted_task_type:{match.group(1)}",
                                     stripped))
    return hits


def test_no_role_picks_its_model_around_the_resolver() -> None:
    hits = _scan()
    assert not hits, (
        "LLM role code bypasses role_preference.resolve_role_model "
        "(route it through the resolver, or add a justified ALLOWED entry):\n"
        + "\n".join(f"  app/{p}:{n} [{k}] {s}" for p, n, k, s in hits)
    )


def test_every_allowlist_entry_still_matches_code() -> None:
    """A stale allowlist entry would silently allow a future regression."""
    stale = []
    for (rel, snippet), _why in ALLOWED.items():
        path = APP / rel
        if not path.exists() or snippet not in path.read_text(encoding="utf-8"):
            stale.append((rel, snippet))
    assert not stale, stale


def test_the_scan_catches_the_supervisor_bypass(tmp_path: Path) -> None:
    """The exact line that sent the supervisor to the env-default cloud model."""
    line = '            model=getattr(self._planner, "_default_model", "claude-opus-4-8"),'
    assert PATTERNS["provider_default_as_model"].search(line)
    assert _MODEL_FOR.search('self._model_router.model_for("refine")').group(1) == "refine"
