"""MongoDB MCP server — interact with a MongoDB database via Motor async driver.

Environment:
  MONGODB_MCP_URL: MongoDB connection URI (e.g. mongodb+srv://user:pass@cluster/db)

The database name is inferred from the URI path or the 'database' argument.
"""
from __future__ import annotations

import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "mongodb_find",
        "description": "Find documents in a MongoDB collection with optional query and projection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Collection name"},
                "database": {"type": "string", "description": "Database name (overrides URI default)"},
                "query": {"type": "object", "description": "MongoDB query filter", "default": {}},
                "projection": {"type": "object", "description": "Fields to include/exclude"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["collection"],
        },
    },
    {
        "name": "mongodb_find_one",
        "description": "Find a single document in a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "query": {"type": "object", "default": {}},
                "projection": {"type": "object"},
            },
            "required": ["collection"],
        },
    },
    {
        "name": "mongodb_insert_one",
        "description": "Insert a single document into a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "document": {"type": "object", "description": "Document to insert"},
            },
            "required": ["collection", "document"],
        },
    },
    {
        "name": "mongodb_update_one",
        "description": "Update a single document in a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "filter": {"type": "object", "description": "Filter to match the document"},
                "update": {"type": "object", "description": "Fields to set (passed as $set)"},
                "upsert": {"type": "boolean", "default": False},
            },
            "required": ["collection", "filter", "update"],
        },
    },
    {
        "name": "mongodb_delete_one",
        "description": "Delete a single document from a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "filter": {"type": "object", "description": "Filter to match the document"},
            },
            "required": ["collection", "filter"],
        },
    },
    {
        "name": "mongodb_aggregate",
        "description": "Run an aggregation pipeline on a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "pipeline": {
                    "type": "array",
                    "description": "Aggregation pipeline stages",
                    "items": {"type": "object"},
                },
            },
            "required": ["collection", "pipeline"],
        },
    },
    {
        "name": "mongodb_list_collections",
        "description": "List all collections in a MongoDB database",
        "parameters": {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
            },
        },
    },
    {
        "name": "mongodb_count",
        "description": "Count documents in a MongoDB collection matching a filter",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "query": {"type": "object", "default": {}},
            },
            "required": ["collection"],
        },
    },
]


def _db_name(url: str, arguments: dict[str, Any]) -> str:
    """Resolve the database name from arguments or the URI path."""
    if db := arguments.get("database"):
        return db
    path = url.split("/")[-1].split("?")[0]
    return path if path else "test"


def get_tools() -> list[dict[str, Any]]:
    try:
        import motor  # noqa: F401  # type: ignore[import]
    except ImportError:
        return [
            {
                "name": "unavailable",
                "description": "motor not installed. Run: pip install motor",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
    return TOOL_DEFINITIONS


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("MONGODB_MCP_URL", "")
    if not url:
        return {"error": "MONGODB_MCP_URL not configured"}

    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore[import]

        client: Any = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
        try:
            db_name = _db_name(url, arguments)
            db = client[db_name]
            coll_name = arguments.get("collection", "documents")
            coll = db[coll_name]

            def _serialize(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
                for d in docs:
                    if "_id" in d:
                        d["_id"] = str(d["_id"])
                return docs

            if tool_name == "mongodb_find":
                query = arguments.get("query") or {}
                projection = arguments.get("projection")
                limit = arguments.get("limit", 100)
                cursor = coll.find(query, projection).limit(limit)
                docs = await cursor.to_list(length=limit)
                return {"documents": _serialize(docs), "count": len(docs)}

            elif tool_name == "mongodb_find_one":
                query = arguments.get("query") or {}
                projection = arguments.get("projection")
                doc = await coll.find_one(query, projection)
                if doc:
                    doc["_id"] = str(doc["_id"])
                return {"document": doc}

            elif tool_name == "mongodb_insert_one":
                result = await coll.insert_one(arguments["document"])
                return {"inserted_id": str(result.inserted_id), "success": True}

            elif tool_name == "mongodb_update_one":
                update_doc = {"$set": arguments["update"]}
                result = await coll.update_one(
                    arguments["filter"],
                    update_doc,
                    upsert=arguments.get("upsert", False),
                )
                return {
                    "matched": result.matched_count,
                    "modified": result.modified_count,
                    "upserted_id": str(result.upserted_id) if result.upserted_id else None,
                }

            elif tool_name == "mongodb_delete_one":
                result = await coll.delete_one(arguments["filter"])
                return {"deleted": result.deleted_count}

            elif tool_name == "mongodb_aggregate":
                cursor = coll.aggregate(arguments["pipeline"])
                docs = await cursor.to_list(length=1000)
                return {"results": _serialize(docs), "count": len(docs)}

            elif tool_name == "mongodb_list_collections":
                names = await db.list_collection_names()
                return {"collections": names, "database": db_name}

            elif tool_name == "mongodb_count":
                query = arguments.get("query") or {}
                count = await coll.count_documents(query)
                return {"count": count, "collection": coll_name}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        finally:
            client.close()

    except ImportError:
        return {
            "error": "motor not installed. Run: pip install motor",
            "tool": tool_name,
            "status": "dependency_missing",
        }
    except Exception as exc:
        logger.exception("mongodb_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
