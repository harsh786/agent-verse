from __future__ import annotations

import time
from typing import Any

from app.mcp.certification_manifest import CONNECTOR_CERTIFICATION_TARGETS


def _result(
    connector: str,
    level: str,
    status: str,
    checks: list[dict[str, str]],
    started: float,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "connector": connector,
        "level": level,
        "status": status,
        "checks": checks,
        "warnings": warnings or [],
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def _unknown_connector_result(connector: str, level: str, started: float) -> dict[str, Any]:
    return _result(
        connector,
        level,
        "failed",
        [{"name": "manifest", "status": "failed"}],
        started,
        warnings=[f"Unknown connector: {connector}"],
    )


def run_static_certification(connector: str) -> dict[str, Any]:
    started = time.monotonic()
    target = CONNECTOR_CERTIFICATION_TARGETS.get(connector)
    if target is None:
        return _unknown_connector_result(connector, "static", started)

    checks = [
        {"name": "manifest", "status": "passed" if target.get("display_name") else "failed"},
        {"name": "auth", "status": "passed" if target.get("auth_modes") else "failed"},
        {"name": "read_tool", "status": "passed" if target.get("read_tool") else "failed"},
    ]
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return _result(connector, "static", status, checks, started)


async def run_mocked_certification(
    connector: str,
    *,
    mcp_client: Any,
    server_id: str,
    tenant_ctx: Any,
) -> dict[str, Any]:
    started = time.monotonic()
    target = CONNECTOR_CERTIFICATION_TARGETS.get(connector)
    if target is None:
        return _unknown_connector_result(connector, "mocked", started)

    warnings: list[str] = []
    try:
        tools = await mcp_client.discover_tools(server_id=server_id, tenant_ctx=tenant_ctx)
    except Exception as exc:
        checks = [{"name": "tool_discovery", "status": "failed"}]
        return _result(
            connector,
            "mocked",
            "failed",
            checks,
            started,
            warnings=[f"Tool discovery failed: {exc}"],
        )

    tool_names = {str(getattr(tool, "name", "")) for tool in tools}
    checks = [
        {
            "name": "tool_discovery",
            "status": "passed" if target["read_tool"] in tool_names else "failed",
        }
    ]
    if checks[0]["status"] == "passed":
        try:
            result = await mcp_client.call_tool(
                server_id=server_id,
                tool_name=target["read_tool"],
                arguments=target["read_arguments"],
                tenant_ctx=tenant_ctx,
            )
        except Exception as exc:
            checks.append({"name": "read_call", "status": "failed"})
            warnings.append(f"Tool call failed: {exc}")
        else:
            output = getattr(result, "output", None)
            output_error = output.get("error") if isinstance(output, dict) else None
            checks.append(
                {
                    "name": "read_call",
                    "status": "failed" if output_error or not result.success else "passed",
                }
            )
            if output_error or not result.success:
                error = str(output_error or getattr(result, "error", "") or "unknown error")
                warnings.append(f"Tool call failed: {error}")
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return _result(connector, "mocked", status, checks, started, warnings=warnings)
