"""ContextResolver — resolves {{...}} template expressions in workflow steps.

Supported variables:
  {{inputs.FIELD}}                  → workflow input value
  {{steps.STEP_ID.output.FIELD}}    → step output field
  {{steps.STEP_ID.output}}          → full step output dict (as JSON)
  {{vars.VAR_NAME}}                 → mutable workflow variable
  {{vault://SECRET_NAME}}           → VaultClient secret (redacted in DB)
  {{env.VAR_NAME}}                  → workflow env variable (from DSL)
  {{foreach.VAR}}                   → current foreach loop variable
  {{foreach.index}}                 → zero-based iteration index
  {{foreach.total}}                 → total items in foreach
  {{trigger.FIELD}}                 → raw trigger payload field
  {{workflow.run_id}}               → current run UUID
  {{workflow.tenant_id}}            → tenant UUID
  {{workflow.now_iso}}              → UTC ISO-8601 timestamp
  {{workflow.now_unix}}             → UTC Unix epoch (int)
  {{workflow.completed_branch}}     → last conditional branch taken
  {{workflow.tenant_admin_email}}   → resolved from tenant record
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

# Template expression: {{ ... }}
_EXPR_RE = re.compile(r"\{\{([^}]+)\}\}")


class ContextResolverError(ValueError):
    pass


class ContextResolver:
    """Resolves {{...}} expressions against the current WorkflowState."""

    def __init__(self, vault_client: Any | None = None) -> None:
        self._vault = vault_client
        # Track vault keys resolved this invocation (for SecretMasker)
        self._vault_keys_used: set[str] = set()

    @property
    def vault_keys_used(self) -> set[str]:
        return set(self._vault_keys_used)

    def reset_vault_tracking(self) -> None:
        self._vault_keys_used.clear()

    # ── Public API ────────────────────────────────────────────────────────

    def resolve(self, template: Any, state: Mapping[str, Any]) -> Any:
        """Resolve a single value. If not a string, return as-is."""
        if not isinstance(template, str):
            return template
        return self._resolve_string(template, state)

    def resolve_dict(self, d: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        """Recursively resolve all string values in a dict."""
        return {k: self._resolve_any(v, state) for k, v in d.items()}

    def resolve_all(self, obj: Any, state: Mapping[str, Any]) -> Any:
        """Resolve any nested structure (dict, list, string)."""
        return self._resolve_any(obj, state)

    # ── Private ───────────────────────────────────────────────────────────

    def _resolve_any(self, obj: Any, state: Mapping[str, Any]) -> Any:
        if isinstance(obj, str):
            return self._resolve_string(obj, state)
        if isinstance(obj, dict):
            return {k: self._resolve_any(v, state) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_any(item, state) for item in obj]
        return obj

    def _resolve_string(self, template: str, state: Mapping[str, Any]) -> Any:
        """Resolve a template string.

        If the entire string is a single {{...}} expression and the resolved
        value is not a string, returns the raw value (e.g. dict, int).
        """
        # Check if entire string is one expression
        stripped = template.strip()
        full_match = re.fullmatch(r"\{\{([^}]+)\}\}", stripped)
        if full_match:
            return self._resolve_expr(full_match.group(1).strip(), state)

        # Multiple expressions or mixed text — always returns string
        def replacer(m: re.Match[str]) -> str:
            val = self._resolve_expr(m.group(1).strip(), state)
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            return str(val) if val is not None else ""

        return _EXPR_RE.sub(replacer, template)

    def _resolve_expr(self, expr: str, state: Mapping[str, Any]) -> Any:
        """Resolve a single expression like 'steps.foo.output.bar'."""
        # vault://SECRET_NAME
        if expr.startswith("vault://"):
            key = expr[8:]
            self._vault_keys_used.add(key)
            if self._vault is not None:
                try:
                    return self._vault.get(key)
                except Exception:
                    _log.warning("vault_key_not_found", key=key)
            return f"[vault:{key}]"

        parts = expr.split(".")

        # inputs.FIELD
        if parts[0] == "inputs":
            return self._get_nested(state.get("inputs", {}), parts[1:])

        # steps.STEP_ID.output[.FIELD...]
        if parts[0] == "steps" and len(parts) >= 3:
            step_id = parts[1]
            if parts[2] != "output":
                return None
            step_out = (state.get("step_outputs") or {}).get(step_id, {})
            if len(parts) == 3:
                return step_out
            return self._get_nested(step_out, parts[3:])

        # vars.VAR_NAME
        if parts[0] == "vars":
            return self._get_nested(state.get("vars", {}), parts[1:])

        # env.VAR_NAME
        if parts[0] == "env":
            env = state.get("_env", {})
            return self._get_nested(env, parts[1:])

        # foreach.*
        if parts[0] == "foreach":
            foreach_ctx = state.get("_foreach_ctx", {})
            return self._get_nested(foreach_ctx, parts[1:])

        # trigger.FIELD
        if parts[0] == "trigger":
            return self._get_nested(state.get("raw_trigger", {}), parts[1:])

        # workflow.*
        if parts[0] == "workflow":
            return self._resolve_workflow_meta(parts[1] if len(parts) > 1 else "", state)

        _log.debug("unresolved_expr", expr=expr)
        return None

    def _resolve_workflow_meta(self, key: str, state: Mapping[str, Any]) -> Any:
        if key == "run_id":
            return state.get("run_id", "")
        if key == "tenant_id":
            return state.get("tenant_id", "")
        if key == "now_iso":
            return datetime.now(UTC).isoformat()
        if key == "now_unix":
            return int(time.time())
        if key == "completed_branch":
            return state.get("completed_branch", "")
        if key == "tenant_admin_email":
            return state.get("_tenant_admin_email", "")
        return None

    @staticmethod
    def _get_nested(obj: Any, parts: list[str]) -> Any:
        """Traverse nested dict/list by dot-separated parts."""
        cur = obj
        for part in parts:
            if cur is None:
                return None
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return cur
