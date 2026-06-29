"""Comprehensive tests for ArtifactTool — execute with/without store, to_tool_def."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.tools.artifact_tool import ArtifactTool


# ── 1. execute without store ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_no_store_returns_local_url():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(
        name="report.csv",
        content="col1,col2\n1,2",
        tenant_id="t1",
        goal_id="g1",
    )
    assert "/artifacts/t1/g1/" in result["artifact_url"]
    assert result["filename"] == "report.csv"


@pytest.mark.asyncio
async def test_execute_no_store_content_bytes():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(
        name="data.bin",
        content=b"\x00\x01\x02",
        tenant_id="t1",
        goal_id="g1",
    )
    assert result["size_bytes"] == 3
    assert result["filename"] == "data.bin"


@pytest.mark.asyncio
async def test_execute_returns_artifact_id():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(
        name="output.txt",
        content="hello",
        tenant_id="t1",
        goal_id="g1",
    )
    assert "artifact_id" in result
    assert len(result["artifact_id"]) == 32  # uuid4 hex


@pytest.mark.asyncio
async def test_execute_unique_artifact_ids():
    tool = ArtifactTool(artifact_store=None)
    r1 = await tool.execute(name="f.txt", content="a", tenant_id="t1", goal_id="g1")
    r2 = await tool.execute(name="f.txt", content="a", tenant_id="t1", goal_id="g1")
    assert r1["artifact_id"] != r2["artifact_id"]


@pytest.mark.asyncio
async def test_execute_content_type_default():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(name="f.txt", content="x", tenant_id="t1", goal_id="g1")
    assert result["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_execute_content_type_custom():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(
        name="data.json",
        content='{"key": "val"}',
        content_type="application/json",
        tenant_id="t1",
        goal_id="g1",
    )
    assert result["content_type"] == "application/json"


@pytest.mark.asyncio
async def test_execute_expires_hours():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(name="f.txt", content="x", tenant_id="t1", goal_id="g1", expires_hours=72)
    assert result["expires_hours"] == 72


@pytest.mark.asyncio
async def test_execute_default_expires_168():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(name="f.txt", content="x", tenant_id="t1", goal_id="g1")
    assert result["expires_hours"] == 168


@pytest.mark.asyncio
async def test_execute_empty_goal_id():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(name="f.txt", content="x", tenant_id="t1")
    assert "artifact_url" in result
    assert result["goal_id"] == ""


@pytest.mark.asyncio
async def test_execute_size_bytes_string_content():
    tool = ArtifactTool(artifact_store=None)
    content = "hello world"
    result = await tool.execute(name="f.txt", content=content, tenant_id="t1", goal_id="g1")
    assert result["size_bytes"] == len(content.encode("utf-8"))


@pytest.mark.asyncio
async def test_execute_created_at_iso_format():
    tool = ArtifactTool(artifact_store=None)
    result = await tool.execute(name="f.txt", content="x", tenant_id="t1", goal_id="g1")
    assert "T" in result["created_at"]  # ISO format check


# ── 2. execute with store ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_with_store_calls_write_bytes():
    mock_store = AsyncMock()
    mock_store.write_bytes = AsyncMock(return_value="https://s3.example.com/bucket/key")
    tool = ArtifactTool(artifact_store=mock_store)

    result = await tool.execute(
        name="output.csv",
        content="a,b,c",
        tenant_id="t1",
        goal_id="g1",
    )
    mock_store.write_bytes.assert_called_once()
    assert result["artifact_url"] == "https://s3.example.com/bucket/key"


@pytest.mark.asyncio
async def test_execute_with_store_correct_key_format():
    mock_store = AsyncMock()
    mock_store.write_bytes = AsyncMock(return_value="https://s3.example.com/key")
    tool = ArtifactTool(artifact_store=mock_store)

    await tool.execute(name="r.csv", content="data", tenant_id="t1", goal_id="g1")

    call_kwargs = mock_store.write_bytes.call_args[1]
    assert call_kwargs["key"].startswith("t1/g1/")
    assert call_kwargs["key"].endswith("/r.csv")
    assert call_kwargs["data"] == b"data"


@pytest.mark.asyncio
async def test_execute_store_failure_falls_back_to_local():
    mock_store = AsyncMock()
    mock_store.write_bytes = AsyncMock(side_effect=Exception("S3 unavailable"))
    tool = ArtifactTool(artifact_store=mock_store)

    result = await tool.execute(
        name="fallback.txt",
        content="test content",
        tenant_id="t1",
        goal_id="g1",
    )
    # Falls back to file://
    assert "file://" in result["artifact_url"] or "artifacts" in result["artifact_url"]
    assert result["filename"] == "fallback.txt"


@pytest.mark.asyncio
async def test_execute_with_store_passes_content_type():
    mock_store = AsyncMock()
    mock_store.write_bytes = AsyncMock(return_value="s3://bucket/key")
    tool = ArtifactTool(artifact_store=mock_store)

    await tool.execute(
        name="report.md",
        content="# Report",
        content_type="text/markdown",
        tenant_id="t1",
        goal_id="g1",
    )
    call_kwargs = mock_store.write_bytes.call_args[1]
    assert call_kwargs["content_type"] == "text/markdown"


# ── 3. to_tool_def ───────────────────────────────────────────────────────────

def test_to_tool_def_structure():
    tool = ArtifactTool()
    defn = tool.to_tool_def()
    assert defn["name"] == "save_artifact"
    assert "description" in defn
    assert "parameters" in defn


def test_to_tool_def_required_fields():
    tool = ArtifactTool()
    defn = tool.to_tool_def()
    required = defn["parameters"]["required"]
    assert "name" in required
    assert "content" in required


def test_to_tool_def_properties():
    tool = ArtifactTool()
    defn = tool.to_tool_def()
    props = defn["parameters"]["properties"]
    assert "name" in props
    assert "content" in props
    assert "content_type" in props
    assert "expires_hours" in props


# ── 4. name / description class attributes ────────────────────────────────────

def test_tool_name():
    assert ArtifactTool.name == "save_artifact"


def test_tool_description_not_empty():
    assert len(ArtifactTool.description) > 10
