"""Tenant-scoped credential lookup for built-in MCP server handlers (TOOL-01).

Built-in handlers historically read their vendor credentials straight from the
process environment (``os.getenv("HUBSPOT_API_KEY")``). On a multi-tenant
platform that is a confused deputy: a tenant's tool call ran with the
PLATFORM's token, database URL or cloud keys, and the credentials the tenant
configured on its own connector were ignored by all but a handful of handlers.

Every handler now reads configuration through :func:`tenant_getenv`, and every
built-in is dispatched through :func:`with_tenant_credentials`, which binds the
calling connector's credentials for the duration of the call:

* Inside a tenant call, ``tenant_getenv("HUBSPOT_API_KEY")`` answers from the
  connector's credentials only — the exact key, its lower-case form, the key
  with the vendor prefix dropped (``api_key``) and a few generic aliases
  (``token`` / ``url`` ...). The process environment is NEVER consulted, so a
  missing tenant credential stays missing (the handler reports it) instead of
  silently becoming the platform's.
* Endpoint values a tenant supplies (anything URL/DSN-shaped, or a ``*_URL`` /
  ``*_HOST`` setting) must pass the connector egress policy, whatever key they
  arrive under — a tenant cannot point a handler at an internal address.
* Settings naming a local file (``*_PATH``, ``*_FILE``,
  ``GOOGLE_APPLICATION_CREDENTIALS``, ``KUBECONFIG``) are never answered inside
  a tenant call: a file on the platform host is never a tenant's credential.

Outside a tenant call (platform code, CLI, unit tests calling a handler module
directly) :func:`tenant_getenv` is ``os.environ.get``.
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "TenantCredentialError",
    "accepts_credentials",
    "aws_credentials",
    "in_tenant_scope",
    "platform_scoped",
    "tenant_getenv",
    "tenant_scope",
    "with_tenant_credentials",
]


class TenantCredentialError(ValueError):
    """A tenant-supplied credential is not usable (e.g. blocked endpoint)."""


class _Scope:
    """The calling connector's credentials, looked up by env-var name."""

    def __init__(self, credentials: Mapping[str, Any]) -> None:
        self._values: dict[str, str] = {}
        for key, value in credentials.items():
            if isinstance(value, str | int | float | bool) and str(value).strip():
                self._values.setdefault(str(key).strip().lower(), str(value).strip())
        self._checked: set[str] = set()

    def lookup(self, name: str) -> str | None:
        key = name.strip().lower()
        if _names_local_file(key):
            return None
        value = self._values.get(key)
        if value is None:
            tokens = key.split("_")
            # Vendor prefix dropped: HUBSPOT_API_KEY -> api_key -> key.
            for i in range(1, len(tokens)):
                value = self._values.get("_".join(tokens[i:]))
                if value is not None:
                    break
        if value is None:
            for alias in _generic_aliases(key):
                value = self._values.get(alias)
                if value is not None:
                    break
        if value is not None and _is_endpoint(key, value):
            self._check_endpoint(key, value)
        return value

    def _check_endpoint(self, key: str, value: str) -> None:
        if value in self._checked:
            return
        _assert_tenant_endpoint(key, value)
        self._checked.add(value)


_SCOPE: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar(
    "mcp_builtin_tenant_credentials", default=None
)

_URL_SUFFIXES = ("_url", "_uri", "_endpoint", "_host", "_server", "_dsn", "_base")
_URL_ALIASES = (
    "url",
    "base_url",
    "instance_url",
    "endpoint",
    "server_url",
    "uri",
    "connection_string",
    "dsn",
    "host",
)
_SECRET_SUFFIXES = ("_token", "_key", "_pat", "_secret_key")
_SECRET_ALIASES = ("token", "api_key", "access_token", "api_token", "key", "bearer_token")
_USER_ALIASES = ("username", "user", "email")
_FILE_SUFFIXES = ("_path", "_file", "_filename", "_dir")
_FILE_NAMES = frozenset({"google_application_credentials", "kubeconfig"})
_DSN_SCHEMES = ("mongodb", "mongodb+srv", "postgres", "postgresql", "mysql", "redis", "rediss")
_HTTP_SCHEMES = ("http", "https", "ws", "wss")


def _names_local_file(key: str) -> bool:
    return key in _FILE_NAMES or key.endswith(_FILE_SUFFIXES)


def _generic_aliases(key: str) -> tuple[str, ...]:
    if key.endswith(_URL_SUFFIXES):
        return _URL_ALIASES
    if key.endswith("_password"):
        return ("password",)
    if key.endswith(("_user", "_username", "_email")):
        return _USER_ALIASES
    if key.endswith(_SECRET_SUFFIXES):
        return _SECRET_ALIASES
    return ()


def _is_endpoint(key: str, value: str) -> bool:
    return "://" in value or key.endswith(_URL_SUFFIXES)


def _assert_tenant_endpoint(key: str, value: str) -> None:
    """Refuse a tenant endpoint the connector egress policy does not allow."""
    from app.ingestion.connector_egress import assert_source_dsn, assert_source_url
    from app.net.ssrf_guard import SSRFError

    context = f"MCP built-in credential {key}"
    candidate = value if "://" in value else f"https://{value}"
    scheme = urlsplit(candidate).scheme.lower()
    try:
        if scheme in _DSN_SCHEMES:
            assert_source_dsn(candidate, context=context)
        elif scheme in _HTTP_SCHEMES:
            http = candidate.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
            assert_source_url(http, context=context)
        else:
            raise TenantCredentialError(
                f"Connector credential '{key}' uses scheme '{scheme}', which is not allowed"
            )
    except (SSRFError, ValueError) as exc:
        if isinstance(exc, TenantCredentialError):
            raise
        raise TenantCredentialError(
            f"Connector credential '{key}' points at an address the egress policy blocks"
        ) from exc


def in_tenant_scope() -> bool:
    """True while a built-in handler is serving a tenant's tool call."""
    return _SCOPE.get() is not None


def tenant_getenv(name: str, default: Any = None) -> Any:
    """``os.getenv`` for built-in handlers — tenant credentials only in a tenant call."""
    scope = _SCOPE.get()
    if scope is None:
        return os.environ.get(name, default)
    value = scope.lookup(name)
    return default if value is None else value


def aws_credentials() -> dict[str, str]:
    """Explicit AWS keys for a boto3 client — never boto3's default chain.

    With ``aws_access_key_id=None`` boto3 falls back to the process env, the
    shared config files and the instance-metadata role: the PLATFORM's AWS
    identity. A connector without both keys is refused instead.
    """
    key = tenant_getenv("AWS_ACCESS_KEY_ID")
    secret = tenant_getenv("AWS_SECRET_ACCESS_KEY")
    if not key or not secret:
        raise TenantCredentialError(
            "AWS credentials are not configured on the connector: configure credentials "
            "(aws_access_key_id and aws_secret_access_key); the platform's AWS identity "
            "is never used."
        )
    creds = {"aws_access_key_id": str(key), "aws_secret_access_key": str(secret)}
    session_token = tenant_getenv("AWS_SESSION_TOKEN")
    if session_token:
        creds["aws_session_token"] = str(session_token)
    return creds


class tenant_scope:  # noqa: N801 - used like a function: ``with tenant_scope(creds):``
    """Bind ``credentials`` as the current tenant call's credentials."""

    def __init__(self, credentials: Mapping[str, Any] | None) -> None:
        self._scope = _Scope(credentials or {})
        self._token: contextvars.Token[_Scope | None] | None = None

    def __enter__(self) -> tenant_scope:
        from app.mcp.servers.egress import install_http_pinning

        install_http_pinning()  # connect-time pinning of every HTTP connection
        self._token = _SCOPE.set(self._scope)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _SCOPE.reset(self._token)


def accepts_credentials(handler: Callable[..., Any]) -> bool:
    try:
        return "credentials" in inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return False


Handler = Callable[..., Awaitable[dict[str, Any]]]


def _copy_identity(wrapper: Any, handler: Any) -> None:
    # Deliberately NOT functools.wraps: ``__wrapped__`` would make
    # inspect.signature() report the inner signature (no ``credentials``), and
    # MCPClient would then stop passing the tenant's credentials at all.
    for attr in ("__module__", "__name__", "__qualname__", "__doc__"):
        with contextlib.suppress(AttributeError, TypeError):
            setattr(wrapper, attr, getattr(handler, attr))
    wrapper._builtin_inner_handler = handler


def with_tenant_credentials(handler: Handler) -> Handler:
    """Dispatch ``handler`` with the calling connector's credentials bound.

    The returned callable always accepts ``credentials``; the handler sees them
    both as its own ``credentials`` argument (when it declares one) and through
    :func:`tenant_getenv`. The platform environment is never read during the call.
    """
    if getattr(handler, "_tenant_scoped", False):
        return handler
    pass_through = accepts_credentials(handler)

    async def _call(
        tool_name: str,
        arguments: dict[str, Any],
        credentials: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        creds = dict(credentials or {})
        try:
            with tenant_scope(creds):
                if pass_through:
                    return await handler(tool_name, arguments, credentials=creds)
                return await handler(tool_name, arguments)
        except TenantCredentialError as exc:
            return {"error": str(exc), "status": "credentials_rejected"}

    _copy_identity(_call, handler)
    _call._tenant_scoped = True  # type: ignore[attr-defined]
    return _call


def platform_scoped(handler: Handler) -> Handler:
    """Adapter for a platform-owned, credential-free built-in (web search, OCR,
    HTTP fetch): it accepts ``credentials`` for a uniform dispatch contract but
    runs on platform configuration, since no tenant secret is involved."""
    if getattr(handler, "_platform_scoped", False) or getattr(handler, "_tenant_scoped", False):
        return handler
    pass_through = accepts_credentials(handler)

    async def _call(
        tool_name: str,
        arguments: dict[str, Any],
        credentials: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if pass_through:
            return await handler(tool_name, arguments, credentials=dict(credentials or {}))
        return await handler(tool_name, arguments)

    _copy_identity(_call, handler)
    _call._platform_scoped = True  # type: ignore[attr-defined]
    return _call
