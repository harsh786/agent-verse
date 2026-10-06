"""a04-F068-02 / a04-F071-02: 'paragraph' and 'dom' are real chunkers.

Both names were listed in SUPPORTED_CHUNKING_STRATEGIES (and are what ``auto``
picks for DOCX and HTML / web pages) but aliased ``SemanticChunker``, which
splits on blank lines only. The DOCX and HTML extractors emit one block per
line, so a whole document became one "paragraph" and was cut at sentence
boundaries regardless of its paragraphs, headings or sections.
"""

from __future__ import annotations

from app.agent.tokenizer import count_tokens
from app.ingestion.chunkers import (
    SUPPORTED_CHUNKING_STRATEGIES,
    DomChunker,
    ParagraphChunker,
    get_chunker_for_strategy,
)
from app.ingestion.chunkers.semantic import SemanticChunker
from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.content_classifier import ContentType


def _para(topic: str, sentences: int = 6) -> str:
    return " ".join(
        f"The {topic} clause number {i} sets out obligation {i} for the {topic} programme."
        for i in range(sentences)
    )


def test_strategies_map_to_their_own_chunkers() -> None:
    assert {"paragraph", "dom"} <= SUPPORTED_CHUNKING_STRATEGIES
    paragraph = get_chunker_for_strategy("paragraph")
    dom = get_chunker_for_strategy("dom")
    assert isinstance(paragraph, ParagraphChunker)
    assert isinstance(dom, DomChunker)
    assert not isinstance(paragraph, SemanticChunker)
    assert not isinstance(dom, SemanticChunker)


# ── paragraph ────────────────────────────────────────────────────────────────


def test_paragraph_chunker_keeps_docx_paragraphs_whole() -> None:
    # The DOCX extractor's output: one paragraph per line, no blank lines.
    paragraphs = [_para(t) for t in ("billing", "privacy", "security", "retention", "audit")]
    text = "\n".join(paragraphs)
    chunker = ParagraphChunker(max_tokens=200)

    chunks = chunker.chunk(text)

    assert len(chunks) > 1
    rebuilt = [p for c in chunks for p in c.content.split("\n\n")]
    assert rebuilt == paragraphs  # every paragraph intact, in order, none split
    assert all(count_tokens(c.content) <= 200 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.metadata["strategy"] == "paragraph" for c in chunks)


def test_semantic_chunker_did_not_respect_those_paragraphs() -> None:
    # Why the alias was wrong: on the same text SemanticChunker sees ONE
    # paragraph and cuts it mid-paragraph.
    paragraphs = [_para(t) for t in ("billing", "privacy", "security", "retention", "audit")]
    chunks = SemanticChunker(max_chunk_tokens=200).chunk("\n".join(paragraphs))
    assert any(c.content not in paragraphs and "\n" not in c.content for c in chunks)


def test_paragraph_chunker_never_ends_a_chunk_on_a_heading() -> None:
    text = "\n".join(
        [_para("intro", 7), "# Payment terms", _para("payment", 7), "# Termination", _para("exit")]
    )
    chunks = ParagraphChunker(max_tokens=160).chunk(text)
    for chunk in chunks:
        assert not chunk.content.rstrip().split("\n")[-1].startswith("#")
    starts = [c.content.split("\n")[0] for c in chunks]
    assert "# Payment terms" in starts and "# Termination" in starts


def test_paragraph_chunker_splits_only_an_oversized_paragraph_at_sentences() -> None:
    huge = _para("huge", 60)
    text = f"{_para('small', 2)}\n\n{huge}\n\n{_para('tail', 2)}"
    chunks = ParagraphChunker(max_tokens=120).chunk(text)
    assert chunks[0].content == _para("small", 2)
    assert chunks[-1].content == _para("tail", 2)
    middle = chunks[1:-1]
    assert len(middle) > 1
    assert " ".join(c.content for c in middle) == huge
    assert all(count_tokens(c.content) <= 120 for c in chunks)


def test_paragraph_chunker_blank_line_separated_text() -> None:
    first = "First paragraph here.\nSame paragraph, wrapped onto a second line."
    second = "Second paragraph, which is long enough not to share a chunk with it."
    chunks = ParagraphChunker(max_tokens=20).chunk(f"{first}\n\n{second}")
    assert [c.content for c in chunks] == [first, second]


def test_paragraph_chunker_empty_input() -> None:
    assert ParagraphChunker().chunk("   \n  ") == []


# ── dom ──────────────────────────────────────────────────────────────────────

_PAGE = """<!doctype html><html><head><title>Acme Handbook</title></head><body>
<nav class="site-nav"><a href="/">Home</a> <a href="/pricing">Pricing</a></nav>
<main>
  <h1>Acme Handbook</h1>
  <p>Welcome to the handbook for all Acme staff.</p>
  <h2>Expenses</h2>
  <p>Submit expenses within 30 days of purchase.</p>
  <ul><li>Meals up to 40 EUR per day</li><li>Hotels up to 150 EUR per night</li></ul>
  <h3>Approvals</h3>
  <p>Expenses above 500 EUR need director approval.</p>
  <h2>Leave</h2>
  <p>Staff get 28 days of paid leave per year.</p>
  <table><tr><th>Type</th><th>Days</th></tr><tr><td>Sick</td><td>10</td></tr></table>
</main>
<footer class="footer">Copyright Acme. Cookie settings.</footer>
</body></html>"""


def test_dom_chunker_sections_raw_html_by_heading() -> None:
    chunks = DomChunker().chunk(_PAGE)
    by_section = {c.metadata["section_path"]: c for c in chunks}

    assert set(by_section) == {
        "Acme Handbook",
        "Acme Handbook > Expenses",
        "Acme Handbook > Expenses > Approvals",
        "Acme Handbook > Leave",
    }
    expenses = by_section["Acme Handbook > Expenses"].content
    assert expenses.startswith("## Expenses")
    assert "Submit expenses within 30 days" in expenses
    assert "Meals up to 40 EUR per day" in expenses
    assert "director approval" not in expenses  # its own (sub)section
    approvals = by_section["Acme Handbook > Expenses > Approvals"]
    assert approvals.metadata["heading"] == "Approvals"
    assert approvals.metadata["level"] == 3
    assert "Days: 10" in by_section["Acme Handbook > Leave"].content
    joined = "\n".join(c.content for c in chunks)
    assert "<" not in joined  # never markup
    assert "Cookie settings" not in joined and "Home" not in joined  # page chrome dropped
    assert all(c.metadata["strategy"] == "dom" for c in chunks)


def test_dom_chunker_long_section_continuation_keeps_its_heading() -> None:
    body = "\n".join(_para(f"rule{i}", 3) for i in range(12))
    text = f"# Policy\n## Travel\n{body}\n## Security\nLock your screen."
    chunks = DomChunker(max_tokens=150).chunk(text)
    travel = [c for c in chunks if c.metadata["section_path"] == "Policy > Travel"]
    assert len(travel) > 1
    assert all(c.content.startswith("## Travel\n") for c in travel)
    assert all(count_tokens(c.content) <= 150 for c in chunks)
    assert chunks[-1].metadata["section_path"] == "Policy > Security"
    assert chunks[-1].content == "## Security\nLock your screen."


def test_dom_chunker_text_without_headings_is_paragraph_packed() -> None:
    text = "\n".join(_para(t) for t in ("alpha", "beta", "gamma"))
    chunks = DomChunker(max_tokens=200).chunk(text)
    assert chunks and all(c.metadata["section_path"] == "" for c in chunks)
    assert "\n".join(c.content for c in chunks).count("clause number 0") == 3


def test_auto_strategy_chunks_html_by_section_through_the_selector() -> None:
    from app.ingestion.parsers.html_parser import html_to_text

    selector = ChunkingStrategySelector()
    chunks = selector.select_and_chunk(html_to_text(_PAGE), ContentType.HTML)
    assert selector.last_strategy == "dom"
    assert any(c.startswith("## Leave") for c in chunks)
    assert not any("Expenses" in c and "Leave" in c for c in chunks)
