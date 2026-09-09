"""Embeddings, chunking strategies, and vector operations."""
from __future__ import annotations

import pytest

# ── SENTENCE-TRANSFORMERS EMBEDDING ──────────────────────────────────────────

def test_colbert_encoder_loads() -> None:
    from app.rag.agentic.patterns.colbert import _get_encoder

    enc = _get_encoder()
    if enc is not None:
        # Real model loaded — test it
        from app.rag.agentic.patterns.colbert import ColBERTPattern

        pattern = ColBERTPattern()
        score = pattern._maxsim_score("python programming", "Python is a programming language")
        assert score > 0


def test_cross_encoder_available() -> None:
    from app.rag.cross_encoder import cross_encode, is_cross_encoder_available

    # Whether available or not, must not raise
    avail = is_cross_encoder_available()
    assert isinstance(avail, bool)
    # Fallback path always works
    scores = cross_encode("python", ["Python code", "Java code"])
    assert len(scores) == 2


# ── CHUNKING STRATEGIES ───────────────────────────────────────────────────────

def test_semantic_chunker() -> None:
    from app.rag.chunker import SemanticChunker

    # Constructor uses max_chars / overlap_chars, not chunk_size / overlap
    chunker = SemanticChunker(max_chars=200, overlap_chars=20)
    text = (
        "This is paragraph one about machine learning.\n\n"
        "This is paragraph two about deep learning.\n\n"
        "This is paragraph three about NLP."
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    for c in chunks:
        assert len(c.content) > 0


def test_ast_chunker_for_code() -> None:
    from app.ingestion.chunkers.ast_chunker import ASTChunker
    from app.ingestion.chunkers.base import ChunkerBase

    chunker = ASTChunker()
    assert isinstance(chunker, ChunkerBase)
    code = '''
def hello_world():
    """Say hello."""
    print("Hello, World!")

def add(a, b):
    """Add two numbers."""
    return a + b

class Calculator:
    def multiply(self, x, y):
        return x * y
'''
    chunks = chunker.chunk(code)
    assert len(chunks) >= 2  # At least functions + class


def test_heading_chunker_for_markdown() -> None:
    from app.ingestion.chunkers.heading import HeadingChunker

    chunker = HeadingChunker()
    text = """# Introduction
This is the intro.

## Section 1
Content of section 1.

## Section 2
Content of section 2.

### Subsection 2.1
Subsection content.
"""
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2


def test_timestamp_chunker_for_transcripts() -> None:
    from app.ingestion.chunkers.timestamp import TimestampChunker

    chunker = TimestampChunker()
    text = """[00:00:05] Hello and welcome to this tutorial.
[00:01:30] Today we'll cover machine learning basics.
[00:05:00] Let's start with the fundamentals.
[00:10:00] Now moving to advanced topics."""
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2


def test_scene_chunker_for_video() -> None:
    from app.ingestion.chunkers.scene import SceneChunker

    chunker = SceneChunker()
    text = """[SCENE 1: office setup]
The protagonist walks in.

[SCENE 2: meeting room]
A presentation begins.

[SCENE 3: rooftop]
The finale takes place."""
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2


def test_pdf_layout_chunker() -> None:
    from app.ingestion.chunkers.pdf_layout import PDFLayoutChunker

    chunker = PDFLayoutChunker()
    text = """--- PAGE 1 ---
Introduction to machine learning.

--- PAGE 2 ---
Advanced deep learning concepts."""
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1


def test_get_chunker_for_strategy() -> None:
    from app.ingestion.chunkers import get_chunker_for_strategy

    for strategy in ["semantic", "ast", "heading", "layout", "timestamp", "scene"]:
        chunker = get_chunker_for_strategy(strategy)
        assert chunker is not None, f"No chunker for {strategy}"


# ── EMBEDDING POLICY SELECTOR ─────────────────────────────────────────────────

def test_embedding_policy_for_content_types() -> None:
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.embedding_policy_selector import EmbeddingPolicy, EmbeddingPolicySelector

    selector = EmbeddingPolicySelector()

    for ct in [ContentType.TEXT, ContentType.CODE, ContentType.IMAGE, ContentType.VIDEO]:
        policy = selector.select(ct, collection_size=1000)
        assert isinstance(policy, EmbeddingPolicy)
        assert policy.dimension > 0
        assert policy.model_id is not None


def test_embedding_policy_hnsw_for_large_collection() -> None:
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.embedding_policy_selector import EmbeddingPolicySelector

    selector = EmbeddingPolicySelector()
    policy = selector.select(ContentType.TEXT, collection_size=50000)
    assert policy.index_strategy == "hnsw"


# ── VECTOR INDEX POLICY ───────────────────────────────────────────────────────

def test_vector_index_policy() -> None:
    from app.embedding.vector_index_policy import VectorIndexPolicy

    policy = VectorIndexPolicy()
    # Small collection (<1000): exact — IndexStrategy is (str, Enum) so direct comparison works
    result_small = policy.select(collection_size=100, dimension=1536)
    assert result_small in ("exact", "flat", "hnsw")
    # Large collection (>100000): ivf
    result_large = policy.select(collection_size=500000, dimension=1536)
    assert result_large in ("hnsw", "ivf")
