"""No hardcoded model name and no env-only model read in app/ outside an allowlist.

Owner requirement: agents, goal execution, knowledge, chat, connectors and
workflows use the REASONING model configured in the Model Registry
(``app.ai_router.resolve.resolve_reasoning``) — no hardcoded model, no env-only
model. This scan parses every module under ``app/`` and fails on:

* a string constant that IS a model id (``"gpt-4o"``, ``"claude-sonnet-4-5"``,
  ``"nvidia/llama-3.1-nemotron-70b-instruct"``, ...) — docstrings and comments
  are prose and are not scanned;
* an env read of a model variable (``os.getenv("NVIDIA_MODEL")``,
  ``os.environ["X_MODEL"]``) — env models reach the platform through the
  registry seeder and ``configured_default_model`` (the resolver's env tier);
* a ``configured_default_model(...)`` call (the env-only default) outside the
  resolver.

A legitimate exception goes in ``ALLOWED_FILES`` / ``ALLOWED_LITERALS`` /
``ALLOWED_ENV_READS`` with the reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

MODEL_ID = re.compile(
    r"""^(?:
        gpt-[0-9][\w.\-]*
      | o[134](?:-mini|-pro)?
      | claude-[\w.\-]+
      | gemini-[0-9][\w.\-]*
      | gemini-pro[\w.\-]*
      | llama-?[0-9][\w.\-]*
      | mistral-[\w.\-]+
      | mixtral[\w.\-]*
      | qwen[0-9][\w.\-:]*
      | [\w.\-]+/[\w.\-]*(?:llama|nemotron|qwen|gemma|kimi|mistral|gpt-oss|deepseek)[\w.\-]*
      | text-embedding-[\w\-]+
      | voyage-[\w.\-]+
      | whisper-1
      | tts-1(?:-hd)?
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
ENV_MODEL_VAR = re.compile(r"^[A-Z0-9_]*MODEL[A-Z0-9_]*$")

# path (relative to app/, prefix match) -> why literals there are legitimate.
ALLOWED_FILES: dict[str, str] = {
    "db/migrations/": "historical migrations (never edited)",
    "ai_router/model_catalog.py": "catalog of importable models (operator picks from it)",
    "ai_router/registry.py": "built-in model catalog entries (capabilities / prices)",
    "intelligence/cost_tracker.py": "pricing table (the single pricing source)",
    "agent/execution_strategy.py": "behaviour detection: capability profiles keyed by family",
    "observability/metrics.py": "behaviour detection: metric label families",
    "ai_router/seeder.py": "seeds the registry from env/config (how env models enter it)",
    "providers/": "provider adapters: SDK defaults (only used when the registry is empty), "
    "provider model catalogs, model-family behaviour detection, BYOK defaults",
    "core/config.py": "deployment settings defaults (on-prem / NVIDIA / Ollama endpoint "
    "models, used only when that deployment is enabled; seeded into the registry)",
    "mcp/servers/": "vendor MCP connectors: tool-argument defaults of the vendor's own API "
    "on the user's own key (documented per module)",
    # Other capabilities, resolved by their own resolver branches (vision / OCR,
    # embeddings, rerank, speech) — not reasoning models.
    "ingestion/parsers/vision_parser.py": "vision/OCR resolver branch",
    "ingestion/embedding_policy_selector.py": "embedding resolver branch",
    "ingestion/parsers/audio_parser.py": "speech resolver branch",
    "embedding/": "embedding resolver branch",
    "perception/": "vision resolver branch",
    "multimodal/": "vision / speech resolver branch",
    "voice/": "speech resolver branch",
    "ocr/": "vision/OCR resolver branch",
    "rag/cross_encoder.py": "rerank resolver branch (local cross-encoder asset)",
    "rag_platform/hosted_reranker.py": "rerank resolver branch",
}

# (path relative to app/, literal) -> why.
ALLOWED_LITERALS: dict[tuple[str, str], str] = {
    ("ai_router/model_orchestrator.py", "gpt-5.2"): "_MODEL_PROVIDER reference map (provider "
    "attribution for health failover), not a selection",
    ("ai_router/model_orchestrator.py", "gpt-4o"): "_MODEL_PROVIDER map / vision tables "
    "(vision resolver branch)",
    ("ai_router/model_orchestrator.py", "gpt-4o-mini"): "_MODEL_PROVIDER reference map",
    ("ai_router/model_orchestrator.py", "gpt-4o-audio"): "speech tables (speech branch)",
    ("ai_router/model_orchestrator.py", "claude-3-5-sonnet"): "_MODEL_PROVIDER map / vision "
    "tables (vision resolver branch)",
    ("ai_router/model_orchestrator.py", "claude-3-haiku"): "_MODEL_PROVIDER reference map",
    ("ai_router/model_orchestrator.py", "gemini-2.5-pro"): "_MODEL_PROVIDER map / vision and "
    "speech tables (their resolver branches)",
    ("ai_router/model_orchestrator.py", "text-embedding-3-large"): "embedder tier table "
    "(embedding resolver branch)",
    ("ai_router/model_orchestrator.py", "text-embedding-3-small"): "embedder tier table "
    "(embedding resolver branch)",
    ("ai_router/model_orchestrator.py", "voyage-3-lite"): "embedder tier table (embedding "
    "resolver branch)",
    ("db/models/knowledge.py", "voyage-4-large"): "collection embedder column default "
    "(embedding resolver branch)",
    ("main.py", "text-embedding-3-small"): "OpenAI embedder fallback (embedding branch)",
    ("orchestration/runtime_profile.py", "text-embedding-3-small"): "multimodal profile "
    "embedding model (embedding resolver branch)",
    ("api/agents.py", "gpt-5.2"): "OpenAI-assistant EXPORT format: the document is imported "
    "into the vendor's own platform, which needs one of its model ids",
    ("api/agents.py", "claude-opus-4-5"): "Anthropic EXPORT format (see above)",
}

# (path relative to app/, env var) -> why.
ALLOWED_ENV_READS: dict[tuple[str, str], str] = {
    ("ai_router/resolve.py", "OLLAMA_OCR_MODEL"): "resolve_ocr's env-pin tier (vision/OCR "
    "resolver)",
    ("ai_router/shadow_router.py", "AGENTVERSE_SHADOW_MODEL"): "flag-gated shadow-evaluation "
    "candidate (uncharged, never serves a user)",
    ("coordination/pattern_runs/moa.py", "MOA_PROPOSER_MODELS"): "Mixture-of-Agents "
    "proposers are, by design, an explicit operator list of extra models (the run's "
    "primary deployment uses the resolver)",
    ("scaling/tasks.py", "EMBEDDING_MODEL"): "embedding resolver branch",
    ("api/ingestion.py", "EMBEDDING_MODEL"): "embedding resolver branch",
    ("api/ingestion.py", "NVIDIA_EMBED_MODEL"): "embedding resolver branch",
}

# Files that may call configured_default_model() (the env tier of the resolver).
ALLOWED_ENV_DEFAULT_CALLERS = {
    "providers/model_defaults.py": "defines it (and the vision fallback, vision branch)",
    "ai_router/resolve.py": "resolve_reasoning's env-default tier",
    "ai_router/seeder.py": "seeds the env model into the registry",
}


def _allowed_file(rel: str) -> bool:
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in ALLOWED_FILES)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _env_var_of(node: ast.AST) -> str | None:
    """``X`` for ``os.getenv("X")`` / ``os.environ.get("X")`` / ``os.environ["X"]``."""
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in {"getenv", "get"}:
            target = func.value if isinstance(func, ast.Attribute) else None
            is_env = name == "getenv" or (
                isinstance(target, ast.Attribute) and target.attr == "environ"
            )
            value = node.args[0].value
            if is_env and isinstance(value, str):
                return value
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
        if node.value.attr == "environ" and isinstance(node.slice, ast.Constant):
            value = node.slice.value
            return value if isinstance(value, str) else None
    return None


def _scan() -> list[str]:
    hits: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        file_ok = _allowed_file(rel)
        for node in ast.walk(tree):
            if (
                not file_ok
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and MODEL_ID.match(node.value.strip())
                and (rel, node.value.strip()) not in ALLOWED_LITERALS
            ):
                hits.append(f"app/{rel}:{node.lineno} model literal {node.value!r}")
            var = _env_var_of(node)
            if (
                var
                and ENV_MODEL_VAR.match(var)
                and not rel.startswith(("providers/", "ai_router/seeder.py"))
                and not _allowed_file(rel)
                and (rel, var) not in ALLOWED_ENV_READS
            ):
                hits.append(f"app/{rel}:{node.lineno} env model read {var}")
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) in {
                    "configured_default_model", "_configured_default_model"}
                and rel not in ALLOWED_ENV_DEFAULT_CALLERS
            ):
                hits.append(f"app/{rel}:{node.lineno} configured_default_model() call")
    return hits


def test_no_hardcoded_model_or_env_only_model_outside_the_allowlist() -> None:
    hits = _scan()
    assert not hits, (
        "Hardcoded model ids / env-only model reads (resolve through "
        "app.ai_router.resolve.resolve_reasoning, or add a justified allowlist entry):\n"
        + "\n".join(f"  {h}" for h in hits)
    )


def test_allowlist_entries_still_match_code() -> None:
    """A stale allowlist entry would silently allow a future regression."""
    stale: list[str] = []
    for prefix in ALLOWED_FILES:
        if not (APP / prefix).exists():
            stale.append(prefix)
    for rel, literal in ALLOWED_LITERALS:
        path = APP / rel
        if not path.exists() or f'"{literal}"' not in path.read_text(encoding="utf-8"):
            stale.append(f"{rel}:{literal}")
    for rel, var in ALLOWED_ENV_READS:
        path = APP / rel
        if not path.exists() or var not in path.read_text(encoding="utf-8"):
            stale.append(f"{rel}:{var}")
    assert not stale, stale


def test_the_scan_recognises_model_ids_and_env_reads() -> None:
    for literal in ("gpt-4o", "gpt-4o-mini", "claude-haiku-3-5", "claude-opus-4-8",
                    "gemini-pro", "gemini-2.5-pro", "llama-3.1-8b-instant", "llama3.2",
                    "nvidia/llama-3.1-nemotron-70b-instruct", "Qwen/Qwen3.5-4B",
                    "moonshotai/kimi-k2", "text-embedding-3-small", "o3-mini"):
        assert MODEL_ID.match(literal), literal
    for prose in ("gpt", "claude", "default", "openai", "anthropic", "mistral", "llama",
                  "text_generation", "a/b", "planning"):
        assert not MODEL_ID.match(prose), prose
    tree = ast.parse('import os\nos.getenv("NVIDIA_MODEL")\nos.environ["DEFAULT_MODEL"]\n')
    found = {v for n in ast.walk(tree) if (v := _env_var_of(n))}
    assert found == {"NVIDIA_MODEL", "DEFAULT_MODEL"}
