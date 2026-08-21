"""
Universal Tool Intelligence Layer
==================================
Makes the agent system truly autonomous — no manual per-tool coding needed.

Three components:

1. UniversalArgumentResolver
   Normalises LLM-generated argument dicts to match ANY tool's JSON Schema
   using semantic similarity, common aliases, and fuzzy matching.
   No hardcoded tool-specific logic.

2. SelfHealingToolCaller
   When a tool call fails with an argument error, uses a fast LLM call to
   fix the arguments and retries automatically.  Works for any tool, any
   provider, forever — no manual patches.

3. SchemaAwarePromptInjector
   Injects the tool's full JSON Schema into the executor prompt so the LLM
   generates correct argument names in the first place.

Usage (in MCPClient.call_tool):
    resolver = UniversalArgumentResolver()
    normalised = resolver.resolve(tool_schema, raw_arguments)
    result = await self._call_tool_impl(cfg, server_id, tool_name, normalised, ctx)
    if not result.success and SelfHealingToolCaller.is_argument_error(result.error):
        result = await healer.heal(tool_name, tool_schema, raw_arguments, result, ...)
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# ── 1. Universal Argument Resolver ─────────────────────────────────────────────

# Semantic aliases map canonical schema param names → common LLM synonyms.
# This table is intentionally broad and domain-spanning: if a tool schema
# declares "jql" but the LLM sends "query", this resolves it automatically.
_SEMANTIC_ALIASES: dict[str, list[str]] = {
    # Jira
    "jql": ["query", "jql_query", "search_query", "jql_string", "filter", "search"],
    "issue_id_or_key": ["issue_key", "issue_id", "key", "ticket", "ticket_id", "issue"],
    "project_key": ["project", "project_id", "project_name"],
    "summary": ["title", "name", "subject", "issue_title", "description_short"],
    "description": ["body", "content", "details", "issue_description", "long_description"],
    "assignee_account_id": ["assignee", "assignee_id", "user_id", "user"],
    "transition_id": ["transition", "status_id", "workflow_transition"],
    # Confluence
    "space_key": ["space", "space_id", "space_name", "confluence_space"],
    "body": [
        "content",
        "text",
        "page_content",
        "html",
        "wiki_content",
        "markup",
        "page_body",
        "page_text",
        "storage",
    ],
    "cql": ["query", "confluence_query", "search_query", "search"],
    "page_id": ["id", "confluence_id", "page", "content_id"],
    "parent_page_id": ["parent", "parent_id", "parent_page"],
    # Slack
    "channel": ["channel_id", "channel_name", "slack_channel", "room"],
    "message": ["text", "content", "msg", "body", "slack_message"],
    "thread_ts": ["thread", "thread_id", "reply_to", "parent_ts"],
    # GitHub / GitLab
    "repo": ["repository", "repo_name", "github_repo", "project"],
    "owner": ["org", "organisation", "organization", "user", "github_owner"],
    "pr_number": ["pull_request", "pr_id", "pull_request_number", "pr"],
    "branch": ["ref", "branch_name", "git_branch"],
    # Email / messaging
    "to": ["recipient", "email", "recipients", "address", "to_email"],
    "subject": ["title", "email_subject", "topic"],
    # Generic
    "id": ["identifier", "resource_id", "object_id"],
    "name": ["title", "label", "display_name"],
    "url": ["link", "href", "endpoint", "uri"],
    "token": ["api_key", "access_token", "auth_token", "key"],
    "limit": ["max", "max_results", "count", "page_size", "size"],
    "max_results": ["limit", "count", "max", "page_size", "size", "n", "num"],
    "offset": ["start", "start_at", "skip", "from"],
}

# Build reverse map: alias → canonical names (for fast lookup)
_ALIAS_TO_CANONICAL: dict[str, list[str]] = {}
for _canonical, _aliases in _SEMANTIC_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL.setdefault(_alias, []).append(_canonical)


def _normalise_key(key: str) -> str:
    """Lowercase, camelCase→snake, remove all non-alphanumeric for comparison."""
    # Convert camelCase / PascalCase to lower (spaceKey → spacekey, SpaceKey → spacekey)
    key = re.sub(r"([A-Z])", lambda m: "_" + m.group(1), key)
    key = key.lower()
    # Strip everything non-alphanumeric for comparison only
    key = re.sub(r"[^a-z0-9]", "", key)
    return key


class UniversalArgumentResolver:
    """
    Resolves LLM-generated argument dicts to match ANY tool's JSON Schema.

    Algorithm (in order, stops when a match is found):
    1. Exact key match               → already correct
    2. Semantic alias lookup         → "query" → "jql"
    3. Normalised key match          → "PageContent" → "page_content"
    4. Fuzzy substring match         → "jqlQuery" contains "jql"
    5. Type-guided value promotion   → {"jql": {...}} with jql.query = the string

    Works for ANY tool without any per-tool code.
    """

    def resolve(
        self,
        tool_schema: dict[str, Any] | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a new arguments dict normalised to match tool_schema."""
        if not tool_schema or not arguments:
            return arguments

        properties: dict[str, Any] = tool_schema.get("properties") or {}
        required: list[str] = tool_schema.get("required") or []

        if not properties:
            return arguments  # No schema to resolve against

        resolved = dict(arguments)

        # For every schema property that's missing, try to find a match
        for param_name, param_def in properties.items():
            if param_name in resolved:
                continue  # Already present

            value = (
                self._by_semantic_alias(param_name, resolved)
                or self._by_normalised_key(param_name, resolved)
                or self._by_fuzzy_match(param_name, resolved)
                or self._by_type_guided(param_name, param_def, resolved)
            )
            if value is not None:
                resolved[param_name] = value
                logger.debug(
                    "argument_resolved",
                    param=param_name,
                    resolved_from=str(value)[:60],
                )

        # Remove keys that are clearly wrong (not in schema) to avoid noise
        # Only when the schema is exhaustive (additionalProperties: false)
        if tool_schema.get("additionalProperties") is False:
            resolved = {k: v for k, v in resolved.items() if k in properties}

        return resolved

    # ── Resolution strategies ──────────────────────────────────────────────

    def _by_semantic_alias(self, param: str, args: dict) -> Any:
        """Check our semantic alias table."""
        aliases = _SEMANTIC_ALIASES.get(param, [])
        for alias in aliases:
            if alias in args:
                return args[alias]
        # Also reverse: if param itself is an alias for something in args
        for alias, canonicals in _ALIAS_TO_CANONICAL.items():
            if alias in args and param in canonicals:
                return args[alias]
        return None

    def _by_normalised_key(self, param: str, args: dict) -> Any:
        """Case/punctuation insensitive match."""
        norm_param = _normalise_key(param)
        for key, val in args.items():
            if _normalise_key(key) == norm_param:
                return val
        return None

    def _by_fuzzy_match(self, param: str, args: dict) -> Any:
        """Substring match — 'jqlQuery' contains 'jql'."""
        norm_param = _normalise_key(param)
        # Score each key by longest common substring length
        best_score = 0
        best_val: Any = None
        for key, val in args.items():
            norm_key = _normalise_key(key)
            # Both directions
            if norm_param in norm_key or norm_key in norm_param:
                score = min(len(norm_param), len(norm_key))
                if score > best_score:
                    best_score = score
                    best_val = val
        return best_val if best_score >= 3 else None  # Minimum 3 chars to avoid noise

    def _by_type_guided(self, param: str, param_def: dict, args: dict) -> Any:
        """
        Type-guided extraction: if schema says param is a string and one of
        the existing args has a nested key matching param, unwrap it.
        E.g.: args = {"jql_info": {"jql": "project = X"}} → extracts "project = X"
        """
        expected_type = param_def.get("type")
        if expected_type == "string":
            for key, val in args.items():
                if isinstance(val, dict) and param in val:
                    return val[param]
                if isinstance(val, dict):
                    norm_param = _normalise_key(param)
                    for nested_key, nested_val in val.items():
                        if _normalise_key(nested_key) == norm_param and isinstance(nested_val, str):
                            return nested_val
        return None

    def report_missing(
        self,
        tool_name: str,
        tool_schema: dict,
        resolved: dict,
    ) -> list[str]:
        """Return names of required params still missing after resolution."""
        required = tool_schema.get("required") or []
        return [p for p in required if not resolved.get(p)]


# ── 2. Self-Healing Tool Caller ─────────────────────────────────────────────────

_ARGUMENT_ERROR_PATTERNS = [
    r"KeyError:\s*['\"](\w+)['\"]",
    r"missing.*required.*argument",
    r"'(\w+)' is required",
    r"Missing required parameter",
    r"argument.*not.*found",
    r"TypeError.*argument",
    r"required.*missing",
    # Python str(KeyError('body')) produces "'body'" — detect bare quoted word
    r"^['\"][\w]+['\"]$",
]

_ARGUMENT_ERROR_RE = re.compile("|".join(_ARGUMENT_ERROR_PATTERNS), re.IGNORECASE)


class SelfHealingToolCaller:
    """
    When a tool call fails with an argument-related error, uses a fast LLM
    to fix the arguments and retries automatically.

    This makes the agent system truly autonomous:
    - No per-tool manual coding needed
    - Works for any new tool added to the system
    - Learns what the right argument names are and retries
    """

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider  # LLMProvider instance (optional)
        self._max_heal_attempts = 2

    @staticmethod
    def is_argument_error(error: str | None) -> bool:
        """Return True if the error looks like a missing/wrong argument."""
        if not error:
            return False
        return bool(_ARGUMENT_ERROR_RE.search(str(error)))

    async def heal(
        self,
        *,
        tool_name: str,
        tool_schema: dict[str, Any] | None,
        original_arguments: dict[str, Any],
        failed_result: Any,
        resolver: UniversalArgumentResolver | None = None,
    ) -> dict[str, Any]:
        """
        Given a failed tool call, attempt to produce fixed arguments.

        Returns corrected argument dict (may be same as original if healing fails).
        """
        error_msg = str(getattr(failed_result, "error", ""))

        # Step 1: Try the UniversalArgumentResolver first (zero-cost, no LLM needed)
        if resolver and tool_schema:
            resolved = resolver.resolve(tool_schema, original_arguments)
            still_missing = resolver.report_missing(tool_name, tool_schema, resolved)
            if not still_missing:
                logger.info(
                    "self_heal_resolver_fixed",
                    tool=tool_name,
                    original_keys=list(original_arguments.keys()),
                    resolved_keys=list(resolved.keys()),
                )
                return resolved

        # Step 2: Use LLM to fix (only if provider is available)
        if self._provider and tool_schema:
            try:
                fixed = await self._llm_fix_arguments(
                    tool_name=tool_name,
                    tool_schema=tool_schema,
                    original_arguments=original_arguments,
                    error=error_msg,
                )
                if fixed and isinstance(fixed, dict):
                    logger.info(
                        "self_heal_llm_fixed",
                        tool=tool_name,
                        error=error_msg[:100],
                    )
                    return fixed
            except Exception as exc:
                logger.warning("self_heal_llm_failed", tool=tool_name, error=str(exc))

        # Step 3: Extract parameter name from KeyError and use schema default
        if tool_schema:
            healed = self._extract_and_inject(tool_name, tool_schema, original_arguments, error_msg)
            if healed != original_arguments:
                return healed

        return original_arguments  # Couldn't heal; return original

    async def _llm_fix_arguments(
        self,
        tool_name: str,
        tool_schema: dict,
        original_arguments: dict,
        error: str,
    ) -> dict | None:
        """Ask the LLM to produce correct arguments given the schema + error."""
        from app.providers.base import CompletionRequest, Message

        schema_str = json.dumps(tool_schema, indent=2)
        args_str = json.dumps(original_arguments, indent=2)

        prompt = f"""\
You are fixing a failed tool call. The tool call failed because the arguments \
didn't match the expected schema.

Tool name: {tool_name}
Tool JSON Schema:
{schema_str}

Arguments that were passed (which caused the error):
{args_str}

Error: {error}

Produce a corrected JSON object with the right parameter names and values.
- Map the values from the original arguments to the correct schema parameter names
- Keep all values intact, just rename/restructure keys as needed
- For any required parameter still missing, use a reasonable default or empty string
- Return ONLY a valid JSON object, nothing else"""

        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            # Use fastest/cheapest model for self-healing
            model="claude-haiku-3-5",
            max_tokens=500,
        )
        resp = await self._provider.complete(req)
        content = resp.content.strip()
        # Extract JSON from the response
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(content)

    def _extract_and_inject(
        self,
        tool_name: str,
        tool_schema: dict,
        arguments: dict,
        error: str,
    ) -> dict:
        """
        If error is KeyError: 'param_name', check if we have a value in
        arguments that could fill it (by type matching).
        """
        # Extract missing param name from error like KeyError: 'jql'
        match = re.search(r"KeyError:\s*['\"](\w+)['\"]", error)
        if not match:
            return arguments

        missing_param = match.group(1)
        param_schema = (tool_schema.get("properties") or {}).get(missing_param, {})
        expected_type = param_schema.get("type", "string")

        healed = dict(arguments)

        # Try to find a value in existing arguments with the right type
        for key, val in arguments.items():
            if key == missing_param:
                continue
            if expected_type == "string" and isinstance(val, str) and val:
                healed[missing_param] = val
                logger.info(
                    "self_heal_type_inject",
                    tool=tool_name,
                    missing=missing_param,
                    source_key=key,
                )
                return healed
            if expected_type == "integer" and isinstance(val, int):
                healed[missing_param] = val
                return healed
            if expected_type == "array" and isinstance(val, list):
                healed[missing_param] = val
                return healed

        # Last resort: inject empty/default value
        defaults = {"string": "", "integer": 0, "boolean": False, "array": [], "object": {}}
        healed[missing_param] = defaults.get(expected_type, "")
        return healed


# ── 3. Schema-Aware Prompt Injector ─────────────────────────────────────────────


class SchemaAwarePromptInjector:
    """
    Injects tool JSON schemas into the executor's system prompt so the LLM
    generates correct argument names in the first place — preventing errors
    upstream rather than fixing them downstream.

    This is the cleanest solution: the LLM already knows exactly what the
    tool expects before it generates a tool call.
    """

    @staticmethod
    def build_tool_schema_block(tools: list[Any]) -> str:
        """
        Build a [Tool Schemas] block for injection into the system prompt.

        Tools is a list of ToolDefinition / ToolRef objects.
        """
        if not tools:
            return ""

        lines = ["[Tool Schemas — use EXACTLY these parameter names]"]
        for t in tools:
            name = getattr(t, "name", "") or ""
            desc = (getattr(t, "description", "") or "")[:80]
            schema = getattr(t, "input_schema", None) or {}
            server = getattr(t, "server_id", "") or getattr(t, "server_name", "") or ""

            props = schema.get("properties", {})
            required = schema.get("required", [])

            lines.append(f"\n• {name} [{server}] — {desc}")
            if props:
                lines.append("  Parameters:")
                for pname, pdef in list(props.items())[:8]:  # Cap at 8 to save tokens
                    req_marker = "*" if pname in required else ""
                    ptype = pdef.get("type", "any")
                    pdesc = pdef.get("description", "")[:50]
                    lines.append(f"    {pname}{req_marker}: {ptype} — {pdesc}")
                if len(props) > 8:
                    lines.append(f"    ... and {len(props) - 8} more parameters")
        return "\n".join(lines)

    @staticmethod
    def build_tool_call_format_reminder() -> str:
        """A short reminder for the executor about argument naming."""
        return (
            "\n\n[IMPORTANT — Tool Call Rules]\n"
            "• Use the EXACT parameter names from the [Tool Schemas] section above\n"
            "• Never invent parameter names — only use what the schema defines\n"
            "• Required parameters (marked *) MUST always be provided\n"
            "• jira_search_issues requires 'jql' (not 'query', not 'search')\n"
            "• confluence_create_page requires 'body' (not 'content', not 'text')\n"
        )


# ── Module-level convenience instances ──────────────────────────────────────────

_default_resolver = UniversalArgumentResolver()
_default_healer = SelfHealingToolCaller()  # No LLM provider — resolver-only healing


def get_resolver() -> UniversalArgumentResolver:
    return _default_resolver


def get_healer(provider: Any | None = None) -> SelfHealingToolCaller:
    if provider is not None:
        return SelfHealingToolCaller(provider=provider)
    return _default_healer
