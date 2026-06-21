"""Secret resolution that works identically in dev (env vars) and prod (mounted files).

Precedence for a logical secret ``NAME``:
  1. ``NAME_FILE`` env var → read & strip the file it points to (Docker/K8s secrets).
  2. ``NAME`` env var → use its value directly (convenient for local dev).
  3. ``default`` if provided; otherwise raise :class:`SecretNotFoundError`.
"""

from __future__ import annotations

import os

_MISSING = object()


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret cannot be resolved from file or env."""


def read_secret(name: str, default: str | object = _MISSING) -> str:
    """Resolve a secret by name, preferring a mounted ``*_FILE`` over a plain env var."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        try:
            with open(file_path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError as exc:
            raise SecretNotFoundError(
                f"{name}_FILE points to '{file_path}' which could not be read"
            ) from exc

    value = os.environ.get(name)
    if value is not None:
        return value

    if default is not _MISSING:
        return default  # type: ignore[return-value]

    raise SecretNotFoundError(
        f"Secret '{name}' not found (set {name}_FILE, {name}, or provide a default)"
    )
