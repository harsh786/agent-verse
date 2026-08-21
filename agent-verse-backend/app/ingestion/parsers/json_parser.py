"""JSON/JSONL parser — schema-aware flattening for structured data."""

from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)

_MAX_DEPTH = 5
_MAX_ITEMS = 1000


def _flatten(obj: object, prefix: str = "", depth: int = 0) -> list[str]:
    """Flatten a JSON object into key:value pairs."""
    if depth > _MAX_DEPTH:
        return [f"{prefix}: {str(obj)[:200]}"]
    if isinstance(obj, dict):
        parts: list[str] = []
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            parts.extend(_flatten(v, key, depth + 1))
        return parts
    if isinstance(obj, list):
        parts = []
        for i, item in enumerate(obj[:_MAX_ITEMS]):
            parts.extend(_flatten(item, f"{prefix}[{i}]", depth + 1))
        if len(obj) > _MAX_ITEMS:
            parts.append(f"{prefix}[...{len(obj) - _MAX_ITEMS} more items]")
        return parts
    if obj is None:
        return []
    return [f"{prefix}: {obj}"]


class JSONParser:
    """Parse JSON/JSONL into readable key:value text."""

    def parse(self, content: str, *, is_jsonl: bool = False) -> str:
        try:
            if is_jsonl or "\n" in content.strip():
                # Try JSONL first
                lines = content.strip().splitlines()
                parts: list[str] = []
                for i, line in enumerate(lines[:_MAX_ITEMS]):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        flat = _flatten(obj)
                        parts.append(f"Record {i + 1}: " + ", ".join(flat[:20]))
                    except json.JSONDecodeError:
                        pass
                if parts:
                    return "\n".join(parts)

            # Single JSON object
            obj = json.loads(content)
            flat = _flatten(obj)
            return "\n".join(flat[:500])

        except json.JSONDecodeError:
            # Not valid JSON — return as-is
            return content[:10000]
        except Exception as exc:
            _log.warning("json_parse_error: %s", exc)
            return content[:5000]
