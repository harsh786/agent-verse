"""MDB-03 / TG-03: the MongoDB builtin refuses write stages and server-side JavaScript.

``mongodb_find`` / ``mongodb_count`` are read-classified, yet ``$where``,
``$function`` and ``$accumulator`` ran server-side JavaScript through them, and
``mongodb_aggregate`` wrote data with ``$out`` / cross-database ``$merge``. Every
filter, projection, update document, inserted document and pipeline is walked
recursively; a refused operator anywhere fails the call BEFORE a client exists.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.servers import mongodb_server
from app.net.mongodb_policy import MongoOperatorError, assert_safe_mongo_arguments

URI = "mongodb://u:p@8.8.8.8:27017/shop"

_JS = {"body": "function(){return true}", "args": [], "lang": "js"}
_ACC = {
    "init": "function(){return 0}",
    "accumulate": "function(s){return s+1}",
    "accumulateArgs": [],
    "merge": "function(a,b){return a+b}",
    "lang": "js",
}

CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("$out", "mongodb_aggregate", {"pipeline": [{"$limit": 2}, {"$out": "pwned"}]}),
    (
        "$merge",
        "mongodb_aggregate",
        {"pipeline": [{"$merge": {"into": {"db": "otherdb", "coll": "pwned"}}}]},
    ),
    ("$where", "mongodb_find", {"query": {"$where": "sleep(300) || true"}}),
    ("$where", "mongodb_find_one", {"query": {"a": 1, "$or": [{"$where": "true"}]}}),
    ("$where", "mongodb_count", {"query": {"$and": [{"$where": "true"}]}}),
    ("$function", "mongodb_count", {"query": {"$expr": {"$function": _JS}}}),
    ("$function", "mongodb_find", {"projection": {"x": {"$function": _JS}}}),
    (
        "$accumulator",
        "mongodb_aggregate",
        {"pipeline": [{"$group": {"_id": None, "a": {"$accumulator": _ACC}}}]},
    ),
    (
        "$function",
        "mongodb_aggregate",
        {"pipeline": [{"$match": {"$expr": {"$function": _JS}}}]},
    ),
    ("$out", "mongodb_aggregate", {"pipeline": [{"$facet": {"a": [{"$out": "pwned"}]}}]}),
    (
        "$merge",
        "mongodb_aggregate",
        {"pipeline": [{"$lookup": {"from": "x", "pipeline": [{"$merge": "y"}], "as": "j"}}]},
    ),
    ("$where", "mongodb_update_one", {"filter": {"$where": "true"}, "update": {"a": 1}}),
    ("$function", "mongodb_update_one", {"filter": {}, "update": {"a": {"$function": _JS}}}),
    ("$where", "mongodb_delete_one", {"filter": {"$where": "true"}}),
    ("$function", "mongodb_insert_one", {"document": {"a": {"$function": _JS}}}),
    ("$currentOp", "mongodb_aggregate", {"pipeline": [{"$currentOp": {"allUsers": True}}]}),
    ("$listSessions", "mongodb_aggregate", {"pipeline": [{"$listSessions": {}}]}),
]


class _ExplodingClient:
    built = 0

    def __init__(self, *_a: Any, **_k: Any) -> None:
        _ExplodingClient.built += 1
        raise AssertionError("no client may be built for a refused call")


@pytest.fixture
def no_client(monkeypatch: pytest.MonkeyPatch) -> type[_ExplodingClient]:
    import pymongo

    _ExplodingClient.built = 0
    monkeypatch.setattr(pymongo, "MongoClient", _ExplodingClient)
    return _ExplodingClient


@pytest.mark.parametrize(("operator", "tool", "args"), CASES)
def test_policy_refuses_operator(operator: str, tool: str, args: dict[str, Any]) -> None:
    with pytest.raises(MongoOperatorError, match=rf"'\{operator}'"):
        assert_safe_mongo_arguments({"collection": "items", **args})


@pytest.mark.asyncio
@pytest.mark.parametrize(("operator", "tool", "args"), CASES)
async def test_handler_refuses_operator_before_connecting(
    operator: str, tool: str, args: dict[str, Any], no_client: type[_ExplodingClient]
) -> None:
    result = await mongodb_server.call_tool(
        tool, {"collection": "items", **args}, credentials={"url": URI}
    )

    assert result["status"] == "operator_refused"
    assert operator in result["error"]
    assert "not allowed" in result["error"]
    assert no_client.built == 0


def test_server_side_javascript_code_values_are_refused() -> None:
    from bson.code import Code

    with pytest.raises(MongoOperatorError, match="JavaScript"):
        assert_safe_mongo_arguments({"query": {"a": Code("function(){return 1}")}})


def test_pathologically_deep_documents_are_refused() -> None:
    doc: dict[str, Any] = {}
    cursor = doc
    for _ in range(200):
        cursor["a"] = {}
        cursor = cursor["a"]
    with pytest.raises(MongoOperatorError, match="nested"):
        assert_safe_mongo_arguments({"query": doc})


@pytest.mark.parametrize(
    "args",
    [
        {"query": {"status": "open", "n": {"$gt": 3}, "tags": {"$in": ["a", "b"]}}},
        {"query": {"$expr": {"$gt": ["$a", "$b"]}}},
        {"pipeline": [{"$match": {"a": 1}}, {"$group": {"_id": "$a", "n": {"$sum": 1}}}]},
        {
            "pipeline": [
                {"$lookup": {"from": "x", "localField": "a", "foreignField": "b", "as": "j"}}
            ]
        },
        {"filter": {"_id": 1}, "update": {"name": "x"}},
        # A field whose VALUE is the string "$out" is data, not an operator.
        {"document": {"note": "$out", "where": "$where"}},
    ],
)
def test_ordinary_queries_are_allowed(args: dict[str, Any]) -> None:
    assert_safe_mongo_arguments({"collection": "items", **args})
