"""Tests for standalone file_ops convenience functions."""
from __future__ import annotations

import pytest
from app.tools.file_ops import file_read, file_write, file_list, file_delete


@pytest.mark.asyncio
async def test_write_and_read():
    r = await file_write("test_fw.txt", "hello", tenant_id="t-test")
    assert r["success"] is True
    r2 = await file_read("test_fw.txt", tenant_id="t-test")
    assert r2["success"] is True
    assert "hello" in r2["content"]
    # cleanup
    await file_delete("test_fw.txt", tenant_id="t-test")


@pytest.mark.asyncio
async def test_list_files():
    await file_write("list_me.txt", "x", tenant_id="t-list")
    r = await file_list(".", tenant_id="t-list")
    assert r["success"] is True
    assert "entries" in r
    names = [e["name"] for e in r["entries"]]
    assert "list_me.txt" in names
    await file_delete("list_me.txt", tenant_id="t-list")


@pytest.mark.asyncio
async def test_delete_file():
    await file_write("del.txt", "x", tenant_id="t-del")
    r = await file_delete("del.txt", tenant_id="t-del")
    assert r["success"] is True


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_error():
    r = await file_delete("no_such_file_xyz.txt", tenant_id="t-del2")
    assert r["success"] is False


@pytest.mark.asyncio
async def test_path_traversal_blocked():
    r = await file_read("../../etc/passwd", tenant_id="t1")
    assert r["success"] is False
    assert "error" in r
