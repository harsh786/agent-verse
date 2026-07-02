"""Tenant-scoped file operations.

All operations are restricted to /tmp/agentverse-workspace/{tenant_id}/.
Path traversal attempts raise PermissionError.
"""
from __future__ import annotations

import os
import pathlib
from typing import Any

_BASE_WORKSPACE = "/tmp/agentverse-workspace"


class FileOps:
    """File operations scoped to a tenant's isolated workspace directory."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._workspace = pathlib.Path(_BASE_WORKSPACE) / tenant_id
        self._workspace.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: str) -> pathlib.Path:
        """Resolve path and verify it stays within the tenant workspace.

        Raises PermissionError if the resolved path would escape the workspace.
        """
        # Resolve relative to workspace
        resolved = (self._workspace / path).resolve()
        workspace_resolved = self._workspace.resolve()
        try:
            resolved.relative_to(workspace_resolved)
        except ValueError:
            raise PermissionError(
                f"Path {path!r} resolves outside workspace for tenant {self._tenant_id}"
            )
        return resolved

    async def read(self, path: str) -> str:
        """Read text content from a file in the tenant workspace."""
        safe = self._safe_path(path)
        if not safe.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        try:
            import aiofiles
            async with aiofiles.open(safe, "r", encoding="utf-8") as f:
                return await f.read()
        except ImportError:
            return safe.read_text(encoding="utf-8")

    async def write(self, path: str, content: str) -> int:
        """Write text content to a file in the tenant workspace.

        Creates parent directories as needed. Returns bytes written.
        """
        safe = self._safe_path(path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        try:
            import aiofiles
            async with aiofiles.open(safe, "w", encoding="utf-8") as f:
                await f.write(content)
        except ImportError:
            safe.write_text(content, encoding="utf-8")
        return len(content.encode("utf-8"))

    async def list(self, directory: str = ".") -> list[dict[str, Any]]:
        """List files and directories in the tenant workspace path."""
        safe = self._safe_path(directory)
        if not safe.exists():
            return []
        if not safe.is_dir():
            raise NotADirectoryError(f"{directory!r} is not a directory")

        entries = []
        for entry in safe.iterdir():
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "path": str(entry.relative_to(self._workspace)),  # relative path for open/delete
                "type": "directory" if entry.is_dir() else "file",
                "is_dir": entry.is_dir(),
                "size_bytes": stat.st_size if entry.is_file() else 0,
                "modified_at": stat.st_mtime,
            })
        return sorted(entries, key=lambda e: e["name"])

    async def delete(self, path: str) -> bool:
        """Delete a file from the tenant workspace.

        Returns True if deleted, False if not found.
        """
        safe = self._safe_path(path)
        if not safe.exists():
            return False
        if safe.is_dir():
            import shutil
            shutil.rmtree(safe)
        else:
            safe.unlink()
        return True

    async def exists(self, path: str) -> bool:
        """Check if a path exists in the tenant workspace."""
        try:
            safe = self._safe_path(path)
            return safe.exists()
        except PermissionError:
            return False

    # ── alias for compatibility with callers expecting list_dir ───────────────

    async def list_dir(self, directory: str = ".") -> list[dict[str, Any]]:
        """Alias for list() — for callers that prefer the explicit name."""
        return await self.list(directory)


# ── Module-level convenience wrappers returning dicts with success/error ───────


async def file_read(path: str, *, tenant_id: str) -> dict[str, Any]:
    """Read a file and return ``{"success": True, "content": ...}`` or error dict."""
    try:
        ops = FileOps(tenant_id)
        content = await ops.read(path)
        return {"success": True, "path": path, "content": content}
    except (FileNotFoundError, PermissionError, Exception) as exc:
        return {"success": False, "error": str(exc)}


async def file_write(path: str, content: str, *, tenant_id: str) -> dict[str, Any]:
    """Write a file and return ``{"success": True, "bytes_written": ...}`` or error dict."""
    try:
        ops = FileOps(tenant_id)
        bytes_written = await ops.write(path, content)
        return {"success": True, "path": path, "bytes_written": bytes_written}
    except (PermissionError, Exception) as exc:
        return {"success": False, "error": str(exc)}


async def file_list(directory: str = ".", *, tenant_id: str) -> dict[str, Any]:
    """List a directory and return ``{"success": True, "entries": [...]}`` or error dict."""
    try:
        ops = FileOps(tenant_id)
        entries = await ops.list(directory)
        return {"success": True, "directory": directory, "entries": entries}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def file_delete(path: str, *, tenant_id: str) -> dict[str, Any]:
    """Delete a file and return ``{"success": True}`` or error dict."""
    try:
        ops = FileOps(tenant_id)
        deleted = await ops.delete(path)
        if not deleted:
            return {"success": False, "error": f"File not found: {path!r}"}
        return {"success": True, "path": path}
    except (PermissionError, Exception) as exc:
        return {"success": False, "error": str(exc)}
