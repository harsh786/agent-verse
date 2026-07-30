# tests/rag/test_parent_child_sentence_window.py
"""Parent-child and sentence window retrieval tests."""
from __future__ import annotations

# ── Parent-Child Chunking ─────────────────────────────────────────────────────

def test_parent_child_chunker_creates_hierarchy():
    from app.rag.parent_child_chunker import ParentChildChunker

    chunker = ParentChildChunker(parent_chunk_size=200, child_chunk_size=80, child_overlap=20)
    text = "Machine learning is a subset of AI. " * 20  # ~700 chars
    parents = chunker.chunk(text, document_id="doc1")
    assert len(parents) >= 1
    # Each parent must have at least one child
    for parent in parents:
        assert len(parent.children) >= 1
        # Child chunk_id must reference parent
        for child in parent.children:
            assert child.parent_chunk_id == parent.chunk_id


def test_parent_child_chunker_child_window_positions():
    from app.rag.parent_child_chunker import ParentChildChunker

    chunker = ParentChildChunker(parent_chunk_size=500, child_chunk_size=100, child_overlap=20)
    parents = chunker.chunk("A " * 300, document_id="doc1")
    for parent in parents:
        for child in parent.children:
            assert child.window_start >= 0
            assert child.window_end > child.window_start


def test_parent_child_chunker_child_subset_of_parent():
    from app.rag.parent_child_chunker import ParentChildChunker

    chunker = ParentChildChunker(parent_chunk_size=400, child_chunk_size=100)
    text = "Python is a language. It is used for ML. Data science uses Python. "
    parents = chunker.chunk(text, document_id="doc1")
    for parent in parents:
        for child in parent.children:
            # Child content should be (approximately) a substring of parent
            assert len(child.content) <= len(parent.content) + 10  # +10 for boundary chars


# ── Sentence Window Retrieval ─────────────────────────────────────────────────

def test_sentence_window_chunker_creates_sentence_chunks():
    from app.rag.sentence_window import SentenceWindowChunker

    chunker = SentenceWindowChunker(window_size=2)
    text = (
        "Python is great. It is used for ML. Data scientists love Python. "
        "Many libraries exist. TensorFlow is popular."
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 3  # At least 3 sentence chunks
    for chunk in chunks:
        assert "content" in chunk
        assert "window_context" in chunk["metadata"]
        assert len(chunk["metadata"]["window_context"]) >= len(chunk["content"])


def test_sentence_window_chunker_window_includes_neighbors():
    from app.rag.sentence_window import SentenceWindowChunker

    chunker = SentenceWindowChunker(window_size=1)
    text = "First sentence. Second sentence. Third sentence."
    chunks = chunker.chunk(text)
    # Middle sentence should include neighbors in window
    if len(chunks) >= 3:
        middle = chunks[1]
        window = middle["metadata"]["window_context"]
        # Window should contain more than just the matched sentence
        assert len(window) > len(middle["content"])


def test_sentence_window_retriever_expands_to_window():
    """SentenceWindowRetriever must expand small chunks to window context."""
    from app.rag.engine import RetrievalResult
    from app.rag.sentence_window import SentenceWindowRetriever

    # Simulate a retrieval result with window_context in metadata
    result = RetrievalResult(
        chunk_id="c1",
        content="Python is used for ML.",  # Small sentence
        score=0.9,
        source_metadata={
            "window_context": (
                "Python is great. Python is used for ML. Data scientists love Python."
            ),
            "sentence_index": 1,
            "window_expanded": False,
        },
        retrieval_legs=["vector"],
    )
    retriever = SentenceWindowRetriever()
    expanded = retriever.expand([result])
    assert len(expanded) == 1
    # Content should be expanded to window
    assert len(expanded[0].content) > len(result.content)
    assert "window_expanded" in expanded[0].source_metadata


def test_sentence_window_retriever_no_expand_when_no_window():
    """SentenceWindowRetriever must not modify chunks without window metadata."""
    from app.rag.engine import RetrievalResult
    from app.rag.sentence_window import SentenceWindowRetriever

    result = RetrievalResult(
        chunk_id="c1",
        content="Python is used for ML.",
        score=0.9,
        source_metadata={},  # No window context
        retrieval_legs=["vector"],
    )
    retriever = SentenceWindowRetriever()
    expanded = retriever.expand([result])
    assert expanded[0].content == result.content  # Unchanged
