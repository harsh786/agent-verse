"""Shared utilities for MCP server implementations."""

from __future__ import annotations


def safe_tool_result(tool_name: str, error: Exception) -> dict:
    """Return a structured error result instead of silently passing.

    Use this in MCP server ``call_tool`` handlers to replace bare
    ``except Exception: pass`` patterns with structured error responses
    that callers can act on.

    Example::

        except Exception as exc:
            return safe_tool_result(tool_name, exc)
    """
    return {
        "error": str(error),
        "tool": tool_name,
        "success": False,
        "error_type": type(error).__name__,
    }
