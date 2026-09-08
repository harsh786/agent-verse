"""Filesystem path helpers for the test suite.

Previously a number of tests embedded an absolute path pointing at one
developer's home directory (``/Users/<name>/.../Agent-Verse/...``). That made
the tests fail with ``FileNotFoundError`` on every other checkout. This module
computes the locations it needs relative to its own file, so the suite runs on
any clone regardless of where it lives on disk.

Sibling projects in the monorepo (frontend, SDKs) are not always checked out
next to the backend. Access those through the ``require_*`` helpers, which
``pytest.skip`` with a clear reason when the sibling repo is absent rather than
erroring.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# tests/_paths.py -> tests/ -> agent-verse-backend/ -> <monorepo root>
BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

# Backend-internal locations (always present alongside this file).
MIGRATIONS_DIR = BACKEND_ROOT / "app" / "db" / "migrations" / "versions"
INFRA_DIR = BACKEND_ROOT / "infra"

# Sibling monorepo projects — may or may not be checked out beside the backend.
FRONTEND_ROOT = REPO_ROOT / "agent-verse-frontend"


def _first_existing(*candidates: Path) -> Path | None:
    """Return the first candidate path that exists, or ``None``."""
    return next((c for c in candidates if c.exists()), None)


# The SDKs have historically lived either at the repo root or under Archived/.
SDK_PYTHON_ROOT = _first_existing(
    REPO_ROOT / "agent-verse-sdk-python",
    REPO_ROOT / "Archived" / "agent-verse-sdk-python",
)
SDK_TS_ROOT = _first_existing(
    REPO_ROOT / "agent-verse-sdk-typescript",
    REPO_ROOT / "Archived" / "agent-verse-sdk-typescript",
)


def require_frontend() -> Path:
    """Return the frontend repo root, skipping the test if it is absent."""
    if not FRONTEND_ROOT.exists():
        pytest.skip(f"agent-verse-frontend not present in this checkout ({FRONTEND_ROOT})")
    return FRONTEND_ROOT


def require_sdk_python() -> Path:
    """Return the Python SDK root, skipping the test if it is absent."""
    if SDK_PYTHON_ROOT is None:
        pytest.skip("agent-verse-sdk-python not present in this checkout")
    return SDK_PYTHON_ROOT


def require_sdk_typescript() -> Path:
    """Return the TypeScript SDK root, skipping the test if it is absent."""
    if SDK_TS_ROOT is None:
        pytest.skip("agent-verse-sdk-typescript not present in this checkout")
    return SDK_TS_ROOT
