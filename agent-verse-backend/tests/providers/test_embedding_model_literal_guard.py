"""Guard: every embedding uses the Model Registry-configured embedder.

Fails when ``app/`` gains an embedding-model literal (``text-embedding-3-small``,
``voyage-…``, ``…-embed-…``, ``qwen3-embedding``, ``all-MiniLM-…``) or an
env-only embedding-model read (``EMBEDDING_MODEL``, ``NVIDIA_EMBED_MODEL``,
``OLLAMA_EMBED_MODEL``, ``EMBEDDING_PROVIDER``, ``SENTENCE_TRANSFORMERS_MODEL``)
outside the allowlist below. Such literals used to decide which model embedded
queries / documents / memories (the workflow RAG step embedded with the CHAT
provider, content-type routing picked ``voyage-code-3`` on deployments that never
configured it, BYOK providers embedded with ``EMBEDDING_MODEL``), putting vectors
of another model next to an index. The model must come from the resolver
(:func:`app.providers.embedder_factory.resolve_embedder` / a collection's binding).

Docstrings are ignored (prose may name models); every other string constant is
checked. To add an entry, name the file and WHY the literal is not a selection.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

_EMBED_MODEL = re.compile(
    r"^(?:[\w.-]+/)?(?:"
    r"text-embedding-[\w.-]+"
    r"|voyage-[\w.-]+"
    r"|gemini-embedding-[\w.-]+"
    r"|[\w.-]*embed[\w.-]*-\d[\w.:-]*"
    r"|[\w.-]*-embed(?:ding)?(?:[-:][\w.:-]*)?"
    r"|all-MiniLM-[\w.-]+"
    r"|qwen3-embedding[\w.:-]*"
    r")$",
    re.IGNORECASE,
)
# Not model ids although they match the shape.
_NOT_MODELS = frozenset({"re-embed", "memory-embedding-v1"})

_EMBED_ENV = frozenset(
    {
        "EMBEDDING_MODEL",
        "NVIDIA_EMBED_MODEL",
        "OLLAMA_EMBED_MODEL",
        "EMBEDDING_PROVIDER",
        "SENTENCE_TRANSFORMERS_MODEL",
    }
)

# file (relative to app/) -> why its embedding-model literals are not a selection.
LITERAL_ALLOWLIST: dict[str, str] = {
    "ai_router/model_catalog.py": (
        "provider catalog + dimension table, and DEPLOYMENT_DEFAULT_EMBED_MODELS: the "
        "ONE table the resolver reads an env-keyed provider's model from"
    ),
    "ai_router/registry.py": "reference price catalog (BUILTIN_MODELS)",
    "embedding/dimension_policy.py": "known-dimension table",
    "embedding/router.py": "embeddings API price/dimension catalog (BUILTIN_EMBEDDING_CONFIGS)",
    "providers/ollama_provider.py": "Ollama model information table (dimensions, sizes)",
    "core/config.py": (
        "typed Settings defaults of the on-prem / Ollama endpoints (operator "
        "configuration, seeded into the Model Registry)"
    ),
    "db/models/knowledge.py": "legacy collection label column default (migration-defined)",
    "mcp/servers/openai_server.py": "vendor MCP connector's tool-argument default",
    "mcp/servers/gemini_server.py": "vendor MCP connector's tool-argument default",
}

# file (relative to app/) -> why it may read an embedding-model env var.
ENV_ALLOWLIST: dict[str, str] = {
    "providers/model_defaults.py": (
        "configured_embed_model(): the env source the seeder registers INTO the Model Registry"
    ),
    "providers/embedder_factory.py": "the registry embedder resolver (its documented env order)",
    "providers/registry_embedder.py": (
        "the registry embedder resolver's dedicated-endpoint same-model failover"
    ),
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _scan() -> tuple[list[str], list[str]]:
    literals: list[str] = []
    env_reads: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith("db/migrations/"):
            continue  # migrations are history, never edited
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docs:
                continue
            value = node.value
            if (
                rel not in LITERAL_ALLOWLIST
                and value not in _NOT_MODELS
                and _EMBED_MODEL.match(value)
            ):
                literals.append(f"app/{rel}:{node.lineno} {value!r}")
            if value in _EMBED_ENV and rel not in ENV_ALLOWLIST:
                env_reads.append(f"app/{rel}:{node.lineno} {value!r}")
    return literals, env_reads


def test_no_embedding_model_literals_outside_the_allowlist() -> None:
    literals, _ = _scan()
    assert not literals, (
        "embedding-model literals outside the allowlist — take the model from the "
        "Model Registry embedder (resolve_embedder / the collection binding), or add "
        "the file to LITERAL_ALLOWLIST with the reason it is not a selection:\n"
        + "\n".join(literals)
    )


def test_no_env_only_embedding_model_reads_outside_the_allowlist() -> None:
    _, env_reads = _scan()
    assert not env_reads, (
        "env-only embedding-model reads outside the resolver — the env model is "
        "seeded into the Model Registry; read it from there:\n" + "\n".join(env_reads)
    )


def test_allowlisted_files_exist() -> None:
    """A stale entry would silently allow a new file of the same name later."""
    for rel in (*LITERAL_ALLOWLIST, *ENV_ALLOWLIST):
        assert (APP / rel).is_file(), f"allowlisted file app/{rel} no longer exists"


def test_guard_detects_literals_and_env_reads() -> None:
    """The patterns catch what they are meant to (and skip known labels)."""
    for literal in (
        "text-embedding-3-small",
        "voyage-code-3",
        "nvidia/nemotron-3-embed-1b",
        "qwen3-embedding:latest",
        "all-MiniLM-L6-v2",
        "gemini-embedding-001",
        "Qwen/Qwen3-Embedding-0.6B",
        "nomic-embed-text",
    ):
        assert _EMBED_MODEL.match(literal), literal
    for not_model in ("embedding_model", "embedder_resolved", "default", "text"):
        assert not _EMBED_MODEL.match(not_model), not_model
    assert "re-embed" in _NOT_MODELS
