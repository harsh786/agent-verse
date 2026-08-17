"""LaTeX parser — section-aware extraction from .tex source files."""
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# LaTeX sectioning commands in hierarchy order
_SECTION_COMMANDS = [
    "chapter", "section", "subsection", "subsubsection",
    "paragraph", "subparagraph",
]
_SECTION_RE = re.compile(
    r"\\(" + "|".join(_SECTION_COMMANDS) + r")\*?\{([^}]+)\}"
)
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})*")
_MATH_ENV_RE = re.compile(
    r"\\begin\{(equation|align|math|displaymath)[*]?\}.*?\\end\{\1[*]?\}",
    re.DOTALL,
)


def _clean_latex(text: str) -> str:
    """Strip comments, math environments, and most LaTeX commands."""
    text = _COMMENT_RE.sub("", text)
    text = _MATH_ENV_RE.sub(" [formula] ", text)
    text = _COMMAND_RE.sub("", text)
    text = text.replace("{", "").replace("}", "")
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


class LaTeXParser:
    """Parse LaTeX source into section-segmented plain text.

    Each section heading becomes:
      ## Section Title
      <cleaned text>
    """

    MAX_CHARS = 100_000

    def parse(self, content: str | bytes, *, filename: str = "") -> str:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")

        if len(content) > self.MAX_CHARS:
            content = content[: self.MAX_CHARS] + "\n[truncated]"

        # Split on section commands
        parts: list[str] = []
        current_section = ""
        current_body: list[str] = []
        pos = 0

        for m in _SECTION_RE.finditer(content):
            # Body text before this section
            body_chunk = _clean_latex(content[pos : m.start()])
            if body_chunk.strip():
                current_body.append(body_chunk)

            # Flush current section
            if current_section or current_body:
                body_text = "\n".join(current_body).strip()
                if body_text:
                    header = f"## {current_section}\n" if current_section else ""
                    parts.append(header + body_text)

            current_section = m.group(2).strip()
            current_body = []
            pos = m.end()

        # Flush last section
        remaining = _clean_latex(content[pos:])
        if remaining.strip():
            current_body.append(remaining)
        if current_section or current_body:
            body_text = "\n".join(current_body).strip()
            if body_text:
                header = f"## {current_section}\n" if current_section else ""
                parts.append(header + body_text)

        if not parts:
            # No sections found — return cleaned full text
            return _clean_latex(content)

        file_header = f"File: {filename}\n\n" if filename else ""
        return file_header + "\n\n".join(parts)
