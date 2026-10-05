"""A5: non-string credential values reach built-in handlers.

_extract_credentials_from_server kept only ``str`` values, so a connector saved
with ``{"tls": true, "port": 27017}`` reached the MongoDB handler as ``{url}``
alone and a requireTLS server closed the connection.
"""

from __future__ import annotations

import pytest

from app.mcp.client import _extract_credentials_from_server
from app.mcp.registry import MCPServerConfig


def test_bool_and_numbers_pass_through_as_text() -> None:
    cfg = MCPServerConfig(
        name="orders-db",
        url="builtin://",
        builtin_type="builtin-mongodb",
        auth_config={
            "url": "mongodb://8.8.8.8:27017/shop",
            "tls": True,
            "direct_connection": False,
            "port": 27017,
            "timeout": 1.5,
            "nested": {"a": 1},
            "listed": [1, 2],
            "missing": None,
        },
    )
    creds = _extract_credentials_from_server(cfg)
    assert creds["tls"] == "true"
    assert creds["direct_connection"] == "false"
    assert creds["port"] == "27017"
    assert creds["timeout"] == "1.5"
    assert "nested" not in creds and "listed" not in creds and "missing" not in creds


async def test_tls_true_reaches_the_mongodb_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import pymongo

    from app.mcp.servers import mongodb_server

    built: list[dict[str, object]] = []

    class _Client:
        def __init__(self, dsn: str, **kwargs: object) -> None:
            built.append(kwargs)

        def __getitem__(self, name: str) -> object:
            class _DB:
                def command(self, *_a: object, **_k: object) -> dict[str, float]:
                    return {"ok": 1.0}

                def __getitem__(self, coll: str) -> object:
                    return object()

                def list_collection_names(self) -> list[str]:
                    return []

            return _DB()

        def close(self) -> None:
            pass

    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    cfg = MCPServerConfig(
        name="orders-db",
        url="builtin://",
        builtin_type="builtin-mongodb",
        auth_config={"url": "mongodb://8.8.8.8:27017/shop", "tls": True},
    )
    result = await mongodb_server.call_tool(
        "mongodb_list_collections", {}, credentials=_extract_credentials_from_server(cfg)
    )
    assert "collections" in result, result
    assert built[0]["tls"] is True
