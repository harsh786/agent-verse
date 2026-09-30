"""Native ColBERT late-interaction scorer on plain ``transformers`` (COLBERT).

The ColBERT strategy used to load checkpoints through RAGatouille (the optional
``colbert`` extra), which cannot be installed usefully here: ragatouille 0.0.9
imports ``langchain.retrievers`` (gone in langchain 1.x) and pins the Stanford
``colbert-ai`` backend, and its PyLate successor pins ``transformers<=5.3``
while this service runs transformers 5.12. So every ColBERT request failed with
``colbert_library_unavailable``.

This module scores with a Stanford ColBERT(v2) checkpoint (e.g.
``colbert-ir/colbertv2.0``) using only what the service already ships
(``torch``, ``transformers``, ``safetensors``, ``huggingface_hub`` — all pulled
in by ``sentence-transformers``), reproducing ColBERT's inference exactly:

* the BERT encoder plus the checkpoint's ``linear`` projection (768 -> dim);
* query: ``[CLS] [Q] tokens [SEP]`` padded with ``[MASK]`` to ``query_maxlen``
  (query augmentation; mask positions are embedded but not attended to unless
  ``attend_to_mask_tokens``), every position kept;
* document: ``[CLS] [D] tokens [SEP]`` up to ``doc_maxlen``, padding and (with
  ``mask_punctuation``) punctuation tokens dropped;
* L2-normalised token vectors and the MaxSim score: for every query token the
  best-matching document token, summed.

Hyper-parameters come from the checkpoint's ``artifact.metadata`` when present.
"""

from __future__ import annotations

import json
import string
import threading
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")
_QUERY_MARKER = "[unused0]"
_DOC_MARKER = "[unused1]"


@dataclass(frozen=True)
class ColBERTSettings:
    query_maxlen: int = 32
    doc_maxlen: int = 180
    dim: int = 128
    mask_punctuation: bool = True
    attend_to_mask_tokens: bool = False


def resolve_checkpoint_dir(checkpoint: str, *, local_files_only: bool = False) -> Path:
    """A local directory holding the checkpoint (a path, or a Hugging Face repo id)."""
    path = Path(checkpoint).expanduser()
    if path.is_dir():
        return path
    from huggingface_hub import snapshot_download

    patterns = ["*.json", "*.txt", "artifact.metadata", "model.safetensors"]
    try:
        # A cached checkpoint loads without touching the network.
        return Path(snapshot_download(repo_id=checkpoint, allow_patterns=patterns,
                                      local_files_only=True))
    except Exception:
        if local_files_only:
            raise
    return Path(snapshot_download(repo_id=checkpoint, allow_patterns=patterns))


def _read_settings(directory: Path) -> ColBERTSettings:
    metadata = directory / "artifact.metadata"
    if not metadata.is_file():
        return ColBERTSettings()
    try:
        raw = json.loads(metadata.read_text())
    except ValueError:
        return ColBERTSettings()
    defaults = ColBERTSettings()
    return ColBERTSettings(
        query_maxlen=int(raw.get("query_maxlen") or defaults.query_maxlen),
        doc_maxlen=int(raw.get("doc_maxlen") or defaults.doc_maxlen),
        dim=int(raw.get("dim") or defaults.dim),
        mask_punctuation=bool(raw.get("mask_punctuation", defaults.mask_punctuation)),
        attend_to_mask_tokens=bool(
            raw.get("attend_to_mask_tokens", defaults.attend_to_mask_tokens)
        ),
    )


def _load_state_dict(directory: Path) -> dict[str, Any]:
    for name in _WEIGHT_FILES:
        weights = directory / name
        if not weights.is_file():
            continue
        if name.endswith(".safetensors"):
            from safetensors.torch import load_file

            return dict(load_file(str(weights)))
        import torch

        return dict(torch.load(str(weights), map_location="cpu", weights_only=True))
    raise FileNotFoundError(f"no ColBERT weights ({', '.join(_WEIGHT_FILES)}) in {directory}")


class ColBERTCheckpointModel:
    """A loaded ColBERT checkpoint. ``rerank`` is blocking (call it off the loop)."""

    def __init__(self, directory: Path) -> None:
        import torch
        from transformers import AutoConfig, AutoTokenizer, BertModel

        self.settings = _read_settings(directory)
        state = _load_state_dict(directory)
        linear_weight = state.pop("linear.weight", None)
        if linear_weight is None:
            raise ValueError(f"{directory} is not a ColBERT checkpoint (no linear.weight)")
        bert_state = {
            key.removeprefix("bert."): value
            for key, value in state.items()
            if key.startswith("bert.") and not key.endswith("position_ids")
        }
        config = AutoConfig.from_pretrained(str(directory))
        config.architectures = ["BertModel"]
        self._bert = BertModel(config, add_pooling_layer=False)
        missing, _unexpected = self._bert.load_state_dict(bert_state, strict=False)
        if [key for key in missing if not key.endswith("position_ids")]:
            raise ValueError(f"ColBERT checkpoint is missing encoder weights: {missing[:5]}")
        self._bert.eval()
        out_dim, in_dim = linear_weight.shape
        self._linear = torch.nn.Linear(in_dim, out_dim, bias=False)
        with torch.no_grad():
            self._linear.weight.copy_(linear_weight)
        self._linear.eval()
        self._tokenizer = AutoTokenizer.from_pretrained(str(directory))
        vocab = self._tokenizer.get_vocab()
        self._query_marker_id = int(vocab[_QUERY_MARKER])
        self._doc_marker_id = int(vocab[_DOC_MARKER])
        self._mask_id = int(self._tokenizer.mask_token_id)
        self._pad_id = int(self._tokenizer.pad_token_id)
        self._skip_ids: set[int] = set()
        if self.settings.mask_punctuation:
            for symbol in string.punctuation:
                ids = self._tokenizer.encode(symbol, add_special_tokens=False)
                if ids:
                    self._skip_ids.add(int(ids[0]))
        self._lock = Lock()

    def _encode(self, input_ids: Any, attention_mask: Any) -> Any:
        hidden = self._bert(input_ids=input_ids, attention_mask=attention_mask)[0]
        return self._linear(hidden)  # type: ignore[no-any-return]

    def encode_query(self, query: str) -> Any:
        import torch

        batch = self._tokenizer(
            [". " + query],
            padding="max_length",
            truncation=True,
            max_length=self.settings.query_maxlen,
            return_tensors="pt",
        )
        ids, mask = batch["input_ids"], batch["attention_mask"]
        ids[:, 1] = self._query_marker_id
        ids[ids == self._pad_id] = self._mask_id
        if self.settings.attend_to_mask_tokens:
            mask[ids == self._mask_id] = 1
        with torch.no_grad():
            vectors = self._encode(ids, mask)[0]
        return torch.nn.functional.normalize(vectors, p=2, dim=-1)

    def encode_documents(self, documents: list[str]) -> list[Any]:
        import torch

        batch = self._tokenizer(
            [". " + document for document in documents],
            padding="longest",
            truncation="longest_first",
            max_length=self.settings.doc_maxlen,
            return_tensors="pt",
        )
        ids, mask = batch["input_ids"], batch["attention_mask"]
        ids[:, 1] = self._doc_marker_id
        with torch.no_grad():
            vectors = torch.nn.functional.normalize(self._encode(ids, mask), p=2, dim=-1)
        out: list[Any] = []
        for row, row_ids in zip(vectors, ids, strict=True):
            keep = [
                index
                for index, token in enumerate(row_ids.tolist())
                if token != self._pad_id and token not in self._skip_ids
            ]
            out.append(row[keep])
        return out

    def score(self, query: str, documents: list[str]) -> list[float]:
        """MaxSim score of ``query`` against every document."""
        if not documents:
            return []
        with self._lock:
            query_vectors = self.encode_query(query)
            document_vectors = self.encode_documents(documents)
        scores: list[float] = []
        for doc in document_vectors:
            if doc.shape[0] == 0:
                scores.append(0.0)
                continue
            similarity = query_vectors @ doc.T
            scores.append(float(similarity.max(dim=1).values.sum()))
        return scores

    def rerank(self, query: str, documents: list[str], *, k: int) -> list[dict[str, object]]:
        """RAGatouille-compatible results: best first, carrying ``result_index``."""
        scores = self.score(query, documents)
        order = sorted(range(len(documents)), key=lambda index: -scores[index])[:k]
        return [
            {
                "content": documents[index],
                "score": scores[index],
                "rank": rank,
                "result_index": index,
            }
            for rank, index in enumerate(order, start=1)
        ]


def load_colbert_checkpoint(checkpoint: str) -> ColBERTCheckpointModel:
    """Load (downloading if needed) a ColBERT checkpoint by path or repo id."""
    return ColBERTCheckpointModel(resolve_checkpoint_dir(checkpoint))


def prefetch_checkpoint(settings: Any = None) -> threading.Thread | None:
    """Download the configured checkpoint in the background when it is not cached.

    The ColBERT strategy is only offered once its checkpoint is on local disk
    (readiness never touches the network), so without this a fresh container
    would never download it and ColBERT would stay unavailable forever. Runs at
    API startup and in each Celery worker process; a no-op when ColBERT is
    disabled, ``colbert_prefetch`` is off, or the checkpoint is already local.
    """
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    if not bool(getattr(settings, "enable_colbert", False)):
        return None
    if not bool(getattr(settings, "colbert_prefetch", True)):
        return None
    checkpoint = str(getattr(settings, "colbert_checkpoint", "") or "").strip()
    if not checkpoint or not colbert_backend_available():
        return None
    try:
        resolve_checkpoint_dir(checkpoint, local_files_only=True)
        return None  # already local
    except Exception:
        pass

    def _download() -> None:
        from app.observability.logging import get_logger

        log = get_logger(__name__)
        try:
            path = resolve_checkpoint_dir(checkpoint)
            log.info("colbert_checkpoint_ready", checkpoint=checkpoint, path=str(path))
        except Exception as exc:
            log.warning(
                "colbert_checkpoint_prefetch_failed",
                checkpoint=checkpoint,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )

    thread = threading.Thread(target=_download, name="colbert-prefetch", daemon=True)
    thread.start()
    return thread


def colbert_backend_available() -> bool:
    """True when the libraries the native scorer needs can be imported."""
    import importlib.util

    try:
        return all(
            importlib.util.find_spec(name) is not None
            for name in ("torch", "transformers", "safetensors", "huggingface_hub")
        )
    except (ImportError, ValueError):
        return False


__all__ = [
    "ColBERTCheckpointModel",
    "ColBERTSettings",
    "colbert_backend_available",
    "load_colbert_checkpoint",
    "prefetch_checkpoint",
    "resolve_checkpoint_dir",
]
