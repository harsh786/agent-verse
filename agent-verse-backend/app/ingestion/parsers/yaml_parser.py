"""YAML / TOML / HCL parser — config-file-aware extraction."""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def _flatten(obj: object, prefix: str = "", max_depth: int = 8) -> list[str]:
    """Recursively flatten nested dicts/lists into key=value strings."""
    if max_depth == 0:
        return [f"{prefix}=<truncated>"]
    parts: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            child_key = f"{prefix}.{k}" if prefix else k
            parts.extend(_flatten(v, child_key, max_depth - 1))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj[:50]):  # cap list expansion
            child_key = f"{prefix}[{i}]"
            parts.extend(_flatten(v, child_key, max_depth - 1))
    else:
        parts.append(f"{prefix}={obj}")
    return parts


class YAMLParser:
    """Parse YAML configuration files into key=value text."""

    def parse(self, content: str | bytes, *, filename: str = "") -> str:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        try:
            import yaml  # type: ignore[import-not-found]

            data = yaml.safe_load(content)
        except Exception as exc:
            _log.warning("YAML parse failed for '%s': %s — returning raw", filename, exc)
            return content[:8000]

        if data is None:
            return ""
        lines = _flatten(data)
        header = f"Config: {filename}\n" if filename else ""
        return header + "\n".join(lines[:2000])


class TOMLParser:
    """Parse TOML configuration files into key=value text."""

    def parse(self, content: str | bytes, *, filename: str = "") -> str:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        try:
            import tomllib  # Python 3.11+

            data = tomllib.loads(content)
        except ImportError:
            try:
                import tomli  # type: ignore[import-not-found]

                data = tomli.loads(content)
            except ImportError:
                _log.warning("tomllib/tomli not available — returning raw TOML")
                return content[:8000]
        except Exception as exc:
            _log.warning("TOML parse failed for '%s': %s", filename, exc)
            return content[:8000]

        lines = _flatten(data)
        header = f"Config: {filename}\n" if filename else ""
        return header + "\n".join(lines[:2000])
