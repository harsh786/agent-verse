"""Markdown parser — AST-aware section chunking."""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_FRONT_MATTER_RE = re.compile(r"^---\s*\n[\s\S]*?\n---\s*\n", re.MULTILINE)


class MarkdownParser:
    """Parse Markdown into clean text, preserving heading structure."""

    def parse(self, content: str) -> str:
        # Remove front matter (YAML/TOML)
        text = _FRONT_MATTER_RE.sub("", content)

        # Preserve code blocks as-is (valuable for code search): stash fenced
        # blocks behind placeholders before the formatting regexes below run,
        # so sample markdown/asterisks inside a fence (e.g. a snippet showing
        # **bold** syntax) isn't rewritten, then restore them verbatim.
        code_blocks: list[str] = []

        def _stash(m: re.Match[str]) -> str:
            code_blocks.append(m.group(0))
            return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

        text = _CODE_BLOCK_RE.sub(_stash, text)

        # Convert inline/block formatting to plain text
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # bold
        # Italic: require the delimiters to hug their content (not preceded/
        # followed by whitespace) so a `* ` list-item marker on its own line
        # isn't mistaken for an opening italic delimiter.
        text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)  # italic
        text = re.sub(r"`{1,2}([^`]+)`{1,2}", r"\1", text)  # inline code
        text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[Image: \1]", text)  # images
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links

        # Restore stashed code blocks verbatim
        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00CODEBLOCK{i}\x00", block)

        # Clean up whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def parse_sections(self, content: str) -> list[dict]:
        """Parse markdown into sections, each with heading + body."""
        text = self.parse(content)
        sections: list[dict] = []
        current: dict = {"heading": "", "level": 0, "body": ""}
        lines = text.splitlines(keepends=True)

        for line in lines:
            m = _HEADING_RE.match(line.rstrip())
            if m:
                if current["body"].strip() or current["heading"]:
                    sections.append(current)
                current = {
                    "heading": m.group(2),
                    "level": len(m.group(1)),
                    "body": "",
                }
            else:
                current["body"] += line

        if current["body"].strip() or current["heading"]:
            sections.append(current)

        return sections


class YAMLParser:
    """Parse YAML/HCL/TOML configuration files into readable text."""

    def parse(self, content: str, *, file_ext: str = ".yaml") -> str:
        if file_ext in (".yaml", ".yml"):
            return self._parse_yaml(content)
        if file_ext in (".toml",):
            return self._parse_toml(content)
        # HCL/Terraform: parse as best-effort
        return self._parse_generic(content)

    def _parse_yaml(self, content: str) -> str:
        try:
            import yaml  # type: ignore[import-not-found]

            obj = yaml.safe_load(content)
            if obj:
                from app.ingestion.parsers.json_parser import _flatten

                flat = _flatten(obj)
                return "\n".join(flat[:500])
        except Exception as exc:
            _log.debug("yaml_parse_error: %s", exc)
        return content[:10000]

    def _parse_toml(self, content: str) -> str:
        try:
            import tomllib  # Python 3.11+

            obj = tomllib.loads(content)
            from app.ingestion.parsers.json_parser import _flatten

            flat = _flatten(obj)
            return "\n".join(flat[:500])
        except Exception:
            return content[:10000]

    def _parse_generic(self, content: str) -> str:
        # Remove HCL/Terraform comments and return key=value lines
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("//"):
                lines.append(stripped)
        return "\n".join(lines[:1000])
