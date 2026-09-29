"""JSON/JSONL parser — schema-aware flattening for structured data."""

from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)

_MAX_DEPTH = 5


def _flatten(obj: object, prefix: str = "", depth: int = 0) -> list[str]:
    """Flatten a JSON object into key:value pairs.

    Nothing is dropped: past ``_MAX_DEPTH`` the remaining subtree is emitted as
    compact JSON (it used to be cut to 200 chars), and every list item is kept
    (lists used to stop at 1,000 items). Document size is bounded upstream by
    the upload cap and handled by chunking.
    """
    if depth > _MAX_DEPTH:
        return [f"{prefix}: {json.dumps(obj, ensure_ascii=False, default=str)}"]
    if isinstance(obj, dict):
        parts: list[str] = []
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            parts.extend(_flatten(v, key, depth + 1))
        return parts
    if isinstance(obj, list):
        parts = []
        for i, item in enumerate(obj):
            parts.extend(_flatten(item, f"{prefix}[{i}]", depth + 1))
        return parts
    if obj is None:
        return []
    return [f"{prefix}: {obj}"]


class JSONParser:
    """Parse JSON/JSONL into readable key:value text."""

    def parse(self, content: str, *, is_jsonl: bool = False) -> str:
        """Flatten a JSON document, or JSON Lines, into ``path: value`` lines.

        The whole document is tried as JSON first. It used to be treated as
        JSON Lines whenever it contained a newline — i.e. every pretty-printed
        file — so a lone line that parsed on its own (the last number of an
        array, say) was returned and the rest of the document dropped.
        """
        text = content.strip()
        if not is_jsonl:
            try:
                return "\n".join(_flatten(json.loads(text)))
            except json.JSONDecodeError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                _log.warning("json_parse_error: %s", exc)
                return content
        parts: list[str] = []
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            parts.append(f"Record {i + 1}: " + ", ".join(_flatten(obj)))
        # Not JSON at all: keep the text rather than lose it.
        return "\n".join(parts) if parts else content
