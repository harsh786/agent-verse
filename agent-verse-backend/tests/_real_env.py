"""The developer's real ``.env`` for the opt-in real-provider suites — without leaking it.

pytest imports every test module during collection, including deselected opt-in
suites. ``tests/real_e2e`` used to call ``load_dotenv(BACKEND_ROOT / ".env")`` at
module level, which put the real ``.env`` (provider keys, model ids, egress
allowlists) into the environment of the whole unit session. Read it with
:func:`real_env` for skip conditions, and apply it only while those suites'
tests run with :func:`applied_dotenv`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from tests._paths import BACKEND_ROOT


@lru_cache(maxsize=1)
def dotenv() -> dict[str, str]:
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - python-dotenv is a dev dependency
        return {}
    return {k: v for k, v in dotenv_values(BACKEND_ROOT / ".env").items() if v is not None}


def real_env(name: str, default: str = "") -> str:
    """``os.environ[name]``, else the ``.env`` value, else *default* (no mutation)."""
    return os.getenv(name) or dotenv().get(name, default)


@contextmanager
def applied_dotenv(**extra: str) -> Iterator[None]:
    """``.env`` (and *extra*) in ``os.environ`` for the duration; restored afterwards."""
    saved = dict(os.environ)
    for key, value in {**dotenv(), **extra}.items():
        os.environ.setdefault(key, value)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
