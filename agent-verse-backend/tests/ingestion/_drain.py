"""Helpers for connector tests: drain a ``get_delta`` stream that may end in an error.

USR-1: a connector that cannot read part of its source raises at the end of the
stream (after yielding everything it could read) or yields failure documents —
tests assert both what was read and how the failure was reported.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.ingestion.source_config import CONNECTOR_FAILURE_KEY


async def drain(stream: AsyncIterator[Any]) -> tuple[list[Any], Exception | None]:
    """Every item ``stream`` yields, and the exception it ended with (or None)."""
    items: list[Any] = []
    try:
        async for item in stream:
            items.append(item)
    except Exception as exc:
        return items, exc
    return items, None


def _doc(item: Any) -> Any:
    return item[0] if isinstance(item, tuple) else item


def failure_docs(items: list[Any]) -> list[Any]:
    """The failure documents (connector could not read the item) among ``items``."""
    return [_doc(i) for i in items if CONNECTOR_FAILURE_KEY in (_doc(i).metadata or {})]


def content_docs(items: list[Any]) -> list[Any]:
    """The readable documents among ``items``."""
    return [_doc(i) for i in items if CONNECTOR_FAILURE_KEY not in (_doc(i).metadata or {})]
