"""Paragraph- and DOM-structure chunkers (a04-F068-02 / a04-F071-02).

``paragraph`` (what ``auto`` picks for DOCX) and ``dom`` (HTML / web pages) used
to alias :class:`SemanticChunker`, which splits on blank lines only. The DOCX
and HTML extractors emit ONE block per line (headings as ``#`` lines), so a
whole document was one "paragraph", cut at sentence boundaries wherever the
token budget ran out.

* :class:`ParagraphChunker` packs whole paragraphs (blank-line blocks, or lines
  when the text has no blank lines) up to the token budget. A paragraph is split
  (at sentences) only when it alone exceeds the budget, and a heading is never
  left as the last line of a chunk — it opens the next one.
* :class:`DomChunker` follows the document's structure: raw HTML is first read
  with the shared extractor (page chrome dropped, headings / lists / table rows
  kept), then every heading opens a section; each section is packed on its own
  (blocks never cross a section boundary) and every chunk of a section starts
  with the section's heading line, so a continuation chunk keeps its context.
  Chunk metadata carries ``heading``, ``level`` and ``section_path``
  ("Handbook > Expenses > Approvals").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent.tokenizer import count_tokens
from app.ingestion.chunkers.base import Chunk, ChunkerBase
from app.ingestion.chunkers.semantic import SemanticChunker

_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LOOKS_LIKE_HTML = re.compile(
    r"<\s*(?:!doctype\s+html|html|head|body|main|article|section|div|p|h[1-6]|ul|ol|li|"
    r"table|tr|td|span|a)\b[^>]*>",
    re.IGNORECASE,
)


def _is_heading(block: str) -> bool:
    return "\n" not in block and bool(_HEADING_LINE.match(block))


def _paragraphs(text: str) -> list[str]:
    """Blank-line blocks; one block per line when the text has no blank line."""
    stripped = text.strip()
    if not stripped:
        return []
    if re.search(r"\n[ \t]*\n", stripped):
        blocks = [b.strip() for b in re.split(r"\n[ \t]*\n", stripped)]
    else:
        blocks = [line.strip() for line in stripped.split("\n")]
    return [b for b in blocks if b]


def _pack(
    blocks: list[str],
    max_tokens: int,
    *,
    prefix: str = "",
    joiner: str = "\n\n",
) -> list[str]:
    """Greedy-pack whole blocks into texts of at most ``max_tokens`` tokens.

    ``prefix`` (a section heading) starts every text and counts toward the
    budget. A block that cannot fit even alone is split at sentences (then
    words) into texts of its own — its pieces never share a text with another
    block. A heading block never ends a text while more blocks follow.
    """
    budget = max(8, max_tokens - (count_tokens(prefix) + 1 if prefix else 0))
    splitter = SemanticChunker(max_chunk_tokens=budget)
    out: list[list[str]] = []
    current: list[str] = []

    def take_headings() -> list[str]:
        carry: list[str] = []
        while current and _is_heading(current[-1]):
            carry.insert(0, current.pop())
        return carry

    for block in blocks:
        if count_tokens(block) > budget:
            carry = take_headings()  # a heading belongs with what it introduces
            if current:
                out.append(current)
            current = []
            pieces = splitter._split_sentences(block)
            if carry and count_tokens(joiner.join([*carry, pieces[0]])) <= budget:
                pieces[0] = joiner.join([*carry, pieces[0]])
            elif carry:
                out.append(carry)
            out.extend([piece] for piece in pieces)
            continue
        if current and count_tokens(joiner.join([*current, block])) > budget:
            carry = take_headings()
            if current:
                out.append(current)
            current = carry
            if current and count_tokens(joiner.join([*current, block])) > budget:
                out.append(current)  # the heading(s) and this block cannot share
                current = []
        current.append(block)
    if current:
        out.append(current)
    texts = [joiner.join(group) for group in out]
    return [f"{prefix}\n{t}" if prefix else t for t in texts]


class ParagraphChunker(ChunkerBase):
    """Whole paragraphs packed up to ``max_tokens`` (see module docstring)."""

    def __init__(self, max_tokens: int = 512) -> None:
        self._max_tokens = max_tokens

    def chunk(self, content: str) -> list[Chunk]:
        blocks = _paragraphs(content)
        texts = _pack(blocks, self._max_tokens)
        return [
            Chunk(
                content=text,
                chunk_index=index,
                metadata={"strategy": "paragraph", "paragraphs": text.count("\n\n") + 1},
            )
            for index, text in enumerate(texts)
        ]


@dataclass
class _Section:
    path: list[str]
    heading_line: str = ""
    level: int = 0
    blocks: list[str] = field(default_factory=list)


def _html_to_structured_text(content: str) -> str:
    try:
        from app.ingestion.parsers.html_parser import extract_html_text

        text = extract_html_text(content)
    except Exception:
        text = None
    if text:
        return text
    from app.ingestion.parsers.html_parser import html_to_text

    return html_to_text(content)


class DomChunker(ChunkerBase):
    """Section-aware chunks of an HTML page / structured text (see module docstring)."""

    def __init__(self, max_tokens: int = 512) -> None:
        self._max_tokens = max_tokens

    def _sections(self, text: str) -> list[_Section]:
        stack: list[tuple[int, str]] = []
        sections = [_Section(path=[])]
        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            if not line.strip():
                continue
            match = _HEADING_LINE.match(line.strip())
            if match:
                level, title = len(match.group(1)), match.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                sections.append(
                    _Section(
                        path=[t for _, t in stack],
                        heading_line=f"{'#' * level} {title}",
                        level=level,
                    )
                )
            else:
                sections[-1].blocks.append(line.strip())
        return [s for s in sections if s.blocks]

    def chunk(self, content: str) -> list[Chunk]:
        text = content
        if _LOOKS_LIKE_HTML.search(content):
            text = _html_to_structured_text(content)
        chunks: list[Chunk] = []
        for section in self._sections(text):
            for piece in _pack(
                section.blocks, self._max_tokens, prefix=section.heading_line, joiner="\n"
            ):
                chunks.append(
                    Chunk(
                        content=piece,
                        chunk_index=len(chunks),
                        metadata={
                            "strategy": "dom",
                            "heading": section.path[-1] if section.path else "",
                            "level": section.level,
                            "section_path": " > ".join(section.path),
                            "section": section.path[-1] if section.path else "",
                        },
                    )
                )
        return chunks


__all__ = ["DomChunker", "ParagraphChunker"]
