"""Local-only runtime readiness probes for optional RAG dependencies."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from app.rag.catalogue import ReadinessFact


def snapshot_download(*, repo_id: str, local_files_only: bool) -> str:
    """Resolve a Hugging Face snapshot while forbidding network access."""
    from huggingface_hub import snapshot_download as huggingface_snapshot_download

    return str(
        huggingface_snapshot_download(
            repo_id=repo_id,
            local_files_only=local_files_only,
        )
    )


def probe_colbert_library() -> ReadinessFact:
    """Check that RAGatouille imports successfully without downloading artifacts."""
    try:
        available = importlib.util.find_spec("ragatouille") is not None
        if available:
            importlib.import_module("ragatouille")
    except Exception:
        available = False
    return ReadinessFact(
        available,
        "ready" if available else "colbert_library_unavailable",
    )


def probe_colbert_readiness(
    checkpoint: str,
    *,
    library_fact: ReadinessFact | None = None,
) -> ReadinessFact:
    """Check configured ColBERT artifacts using local paths or local HF cache only."""
    resolved_library_fact = library_fact or probe_colbert_library()
    if not resolved_library_fact.available:
        return ReadinessFact(False, "colbert_library_unavailable")
    configured_path = Path(checkpoint).expanduser()
    if _has_local_artifacts(configured_path):
        return ReadinessFact(True, "ready")
    try:
        cached_path = snapshot_download(
            repo_id=checkpoint,
            local_files_only=True,
        )
    except Exception:
        return ReadinessFact(False, "colbert_checkpoint_unavailable")
    has_cached_artifacts = _has_local_artifacts(Path(cached_path))
    return ReadinessFact(
        has_cached_artifacts,
        "ready" if has_cached_artifacts else "colbert_checkpoint_unavailable",
    )


def _has_local_artifacts(path: Path) -> bool:
    if path.is_file():
        return True
    return path.is_dir() and any(item.is_file() for item in path.rglob("*"))


__all__ = ["probe_colbert_library", "probe_colbert_readiness"]
