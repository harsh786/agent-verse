"""COLBERT: the ColBERT strategy runs on a native late-interaction scorer.

RAGatouille (the optional ``colbert`` extra) cannot work against this service's
pins (it imports ``langchain.retrievers``, gone in langchain 1.x; PyLate pins
``transformers<=5.3``), so ColBERT was always ``colbert_library_unavailable`` and
its tests skipped. These tests build a tiny Stanford-format ColBERT checkpoint on
disk (BERT weights under ``bert.*`` + a ``linear`` projection + tokenizer +
``artifact.metadata``) and run the real scorer end to end — no network, no skip.
"""

from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

import pytest
import torch

from app.rag.agentic.patterns.colbert import (
    ColBERTLateInteractionReranker,
    ColBERTPattern,
)
from app.rag.colbert_model import ColBERTCheckpointModel, load_colbert_checkpoint
from app.rag.readiness import probe_colbert_library, probe_colbert_readiness

_WORDS = ["python", "database", "weather", "sunny", "query", "index", "java", "hello", "world"]


def _write_checkpoint(directory: Path, *, dim: int = 8) -> Path:
    from safetensors.torch import save_file
    from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors
    from transformers import BertConfig, BertModel

    vocab = ["[PAD]", "[unused0]", "[unused1]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]
    vocab += list(string.punctuation) + _WORDS
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "vocab.txt").write_text("\n".join(vocab) + "\n")
    tokenizer = Tokenizer(
        models.WordPiece(vocab={token: i for i, token in enumerate(vocab)}, unk_token="[UNK]")
    )
    tokenizer.normalizer = normalizers.BertNormalizer(lowercase=True)
    tokenizer.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 4), ("[SEP]", 5)]
    )
    tokenizer.save(str(directory / "tokenizer.json"))
    (directory / "tokenizer_config.json").write_text(
        json.dumps(
            {
                "tokenizer_class": "BertTokenizer",
                "do_lower_case": True,
                "pad_token": "[PAD]",
                "unk_token": "[UNK]",
                "cls_token": "[CLS]",
                "sep_token": "[SEP]",
                "mask_token": "[MASK]",
            }
        )
    )
    torch.manual_seed(7)
    config = BertConfig(
        vocab_size=len(vocab),
        hidden_size=16,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=32,
        max_position_embeddings=64,
    )
    config.architectures = ["HF_ColBERT"]
    config.save_pretrained(str(directory))
    bert = BertModel(config, add_pooling_layer=False)
    state = {f"bert.{key}": value.contiguous() for key, value in bert.state_dict().items()}
    state["linear.weight"] = torch.randn(dim, 16)
    save_file(state, str(directory / "model.safetensors"))
    (directory / "artifact.metadata").write_text(
        json.dumps({"query_maxlen": 12, "doc_maxlen": 20, "dim": dim, "mask_punctuation": True})
    )
    return directory


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write_checkpoint(tmp_path_factory.mktemp("colbert") / "tiny-colbert")


@pytest.fixture(scope="module")
def model(checkpoint: Path) -> ColBERTCheckpointModel:
    return load_colbert_checkpoint(str(checkpoint))


def test_library_probe_is_ready_without_ragatouille() -> None:
    assert probe_colbert_library().available is True


def test_local_checkpoint_is_ready(checkpoint: Path) -> None:
    assert probe_colbert_readiness(str(checkpoint)).available is True


def test_query_is_augmented_to_query_maxlen_and_projected(model: ColBERTCheckpointModel) -> None:
    vectors = model.encode_query("python database")
    assert tuple(vectors.shape) == (12, 8)  # [MASK]-augmented to query_maxlen, dim from linear
    norms = vectors.norm(dim=-1)
    assert torch.allclose(norms, torch.ones_like(norms), atol=1e-5)


def test_document_drops_padding_and_punctuation(model: ColBERTCheckpointModel) -> None:
    short, long = model.encode_documents(["hello , world !", "python database index query"])
    # [CLS] [D] hello world [SEP] — the comma and "!" are masked, padding dropped.
    assert short.shape[0] == 5
    assert long.shape[0] == 7


def test_score_is_maxsim_over_token_vectors(model: ColBERTCheckpointModel) -> None:
    documents = ["python database", "sunny weather today"]
    scores = model.score("python database", documents)
    query = model.encode_query("python database")
    expected = [float((query @ doc.T).max(dim=1).values.sum()) for doc in
                model.encode_documents(documents)]
    assert scores == pytest.approx(expected, rel=1e-5)
    assert all(abs(score) <= 12.0 + 1e-4 for score in scores)  # at most one per query token


def test_rerank_results_carry_candidate_indices(model: ColBERTCheckpointModel) -> None:
    documents = ["weather", "python database", "java"]
    results = model.rerank("python database", documents, k=3)
    assert sorted(r["result_index"] for r in results) == [0, 1, 2]
    assert [r["content"] for r in results] == [documents[int(r["result_index"])] for r in results]
    ranked = [float(r["score"]) for r in results]  # type: ignore[arg-type]
    assert ranked == sorted(ranked, reverse=True)


async def test_reranker_loads_the_configured_checkpoint_and_scores(checkpoint: Path) -> None:
    reranker = ColBERTLateInteractionReranker(checkpoint=str(checkpoint))
    try:
        scores = await reranker.score("python database", ["weather report", "python database"])
    finally:
        await reranker.aclose()
    expected = load_colbert_checkpoint(str(checkpoint)).score(
        "python database", ["weather report", "python database"]
    )
    assert scores == pytest.approx(expected, rel=1e-5)


async def test_pattern_reranks_with_the_native_model(checkpoint: Path) -> None:
    pattern = ColBERTPattern(
        reranker=ColBERTLateInteractionReranker(checkpoint=str(checkpoint)), owns_reranker=True
    )
    chunks: list[dict[str, Any]] = [
        {"chunk_id": "c1", "content": "python database", "score": 0.1},
        {"chunk_id": "c2", "content": "sunny weather", "score": 0.2},
    ]
    try:
        reranked = await pattern.rerank_async("python database", chunks)
    finally:
        await pattern.aclose()
    assert {c["chunk_id"] for c in reranked} == {"c1", "c2"}
    assert all("colbert_score" in c for c in reranked)


def _prefetch_settings(**overrides: Any) -> Any:
    from types import SimpleNamespace

    values = {
        "enable_colbert": True,
        "colbert_prefetch": True,
        "colbert_checkpoint": "acme/colbert",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_prefetch_downloads_a_missing_checkpoint_in_the_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import colbert_model

    calls: list[bool] = []

    def fake_resolve(checkpoint: str, *, local_files_only: bool = False) -> Path:
        calls.append(local_files_only)
        if local_files_only:
            raise FileNotFoundError(checkpoint)
        return Path("/nonexistent/acme-colbert")

    monkeypatch.setattr(colbert_model, "resolve_checkpoint_dir", fake_resolve)
    thread = colbert_model.prefetch_checkpoint(_prefetch_settings())
    assert thread is not None and thread.daemon
    thread.join(timeout=5)
    assert calls == [True, False]  # offline probe first, then the download


@pytest.mark.parametrize(
    "overrides",
    [{"enable_colbert": False}, {"colbert_prefetch": False}, {"colbert_checkpoint": ""}],
)
def test_prefetch_is_off_when_colbert_is_not_used(
    overrides: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.rag import colbert_model

    def fail(*_a: Any, **_k: Any) -> Path:
        raise AssertionError("must not resolve")

    monkeypatch.setattr(colbert_model, "resolve_checkpoint_dir", fail)
    assert colbert_model.prefetch_checkpoint(_prefetch_settings(**overrides)) is None


def test_prefetch_skips_a_cached_checkpoint(checkpoint: Path) -> None:
    from app.rag import colbert_model

    settings = _prefetch_settings(colbert_checkpoint=str(checkpoint))
    assert colbert_model.prefetch_checkpoint(settings) is None
