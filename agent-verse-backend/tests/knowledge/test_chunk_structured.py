"""Structure-aware chunking for uploads (P1a-7).

Live P1a (KB-COMPLEX-LIFECYCLE): an amendment of one clause re-keyed the
chunks of every later clause, because 512-token windows shift as soon as one
sentence changes length; windows also cut CSV rows and sentences in half.
``chunk_structured`` packs whole lines (sentences for very long lines), starts a
new chunk at a heading, carries the section heading into a continuation chunk
and overlaps with whole lines — so an edit only changes the chunks of its own
section.
"""

from __future__ import annotations

import itertools

from app.knowledge.chunker_v2 import chunk_structured, count_tokens


def _doc(sections: dict[str, str]) -> str:
    return "\n\n".join(f"## {h}\n\n{body}" for h, body in sections.items())


def _body(word: str, n: int) -> str:
    return " ".join(f"The {word} desk records consignment {i} in the ledger." for i in range(n))


def test_an_edit_only_changes_the_chunks_of_its_own_section() -> None:
    base = {"Fees": _body("fees", 40), "Termination": "Notice period: 75 days. " + _body(
        "exit", 40), "Liability": _body("liability", 40), "Data": _body("data", 40)}
    edited = dict(base, Termination="Notice period: 120 days (Amendment No. 3). " + _body(
        "exit", 40))
    before = [c for c, _ in chunk_structured(_doc(base))]
    after = [c for c, _ in chunk_structured(_doc(edited))]
    changed_before = [c for c in before if c not in after]
    changed_after = [c for c in after if c not in before]
    assert changed_before and all("Termination" in c for c in changed_before)
    assert changed_after and all("Termination" in c for c in changed_after)
    assert any("Liability" in c for c in before if c in after)


def test_chunks_respect_the_token_budget_and_never_split_a_line() -> None:
    rows = "\n".join(f"txn_id: GT-{i:07d}, gate: Gate {i % 4}, remarks: seal ok, cleared"
                     for i in range(2000))
    chunks = chunk_structured(rows, max_tokens=256, overlap_tokens=32)
    assert len(chunks) > 10
    lines = set(rows.splitlines())
    for text, _ in chunks:
        assert count_tokens(text) <= 256
        assert all(line in lines for line in text.splitlines())
    covered = {line for text, _ in chunks for line in text.splitlines()}
    assert covered == lines


def test_overlap_is_made_of_whole_lines() -> None:
    rows = "\n".join(f"row {i}: value {i * 7}" for i in range(400))
    chunks = [c for c, _ in chunk_structured(rows, max_tokens=128, overlap_tokens=24)]
    for prev, nxt in itertools.pairwise(chunks):
        assert prev.splitlines()[-1] in nxt.splitlines()


def test_a_long_section_carries_its_heading_into_continuation_chunks() -> None:
    text = "# Manual\n\n## Cold-chain handling\n\n" + _body("cold", 120)
    chunks = [c for c, _ in chunk_structured(text, max_tokens=200, overlap_tokens=20)]
    assert len(chunks) > 2
    assert all("Cold-chain handling" in c for c in chunks)
    assert all(count_tokens(c) <= 200 for c in chunks)


def test_small_sections_are_packed_together() -> None:
    text = "\n\n".join(f"## Step {i}\n\nRun command {i}." for i in range(6))
    assert len(chunk_structured(text, max_tokens=512, overlap_tokens=64)) == 1


def test_a_huge_single_line_is_split_by_sentences_then_tokens() -> None:
    para = " ".join(f"Sentence {i} explains rule {i} of the berth window policy." for i in range(300))
    blob = "x" * 6000  # one "word" with no sentence boundary at all
    chunks = [c for c, _ in chunk_structured(para + "\n" + blob, max_tokens=128,
                                             overlap_tokens=16)]
    assert all(count_tokens(c) <= 128 for c in chunks)
    joined = " ".join(chunks)
    assert "Sentence 0 explains rule 0" in joined and "Sentence 299 explains rule 299" in joined
    assert sum(c.count("x") for c in chunks) >= 6000


def test_offsets_point_at_the_chunk_text() -> None:
    text = "## A\n\nalpha line\n\n## B\n\nbravo line\n" + "\n".join(
        f"filler {i}" for i in range(300))
    for chunk, offset in chunk_structured(text, max_tokens=64, overlap_tokens=8):
        # the offset is where the chunk's own (non-carried) text starts
        assert any(text.startswith(line, offset) for line in chunk.splitlines() if line.strip())


def test_blank_text_is_empty() -> None:
    assert chunk_structured("  \n\n ") == []
