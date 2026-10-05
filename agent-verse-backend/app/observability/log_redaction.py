"""Log redaction for every process (API, Celery workers, beat) — OI-3.

Raw exception text reached the worker log unredacted (a ``PermissionError`` whose
message carried a bearer token was logged by ``run_goal``). Redaction used to be
applied only at specific call sites (goal rows, events); logs had none.

``install_log_redaction()`` covers both logging pipelines, idempotently:

* **stdlib logging** — a ``LogRecord`` factory redacts the formatted message, the
  exception text / traceback and the stack info when the record is created, so it
  applies to every logger and every handler (including ones Celery or a library
  attaches later) without depending on handler order.
* **structlog** — :func:`redact_event_dict` is inserted before the renderer. It
  renders ``exc_info`` into a redacted ``exception`` string and redacts every
  string value (nested dicts / lists included).

Masks: bearer tokens, Authorization / API-key headers, ``key=value`` secrets,
API-key query parameters, URI userinfo passwords and provider-key-shaped strings
(the shared ``BARE_SECRET_PATTERNS`` of :mod:`app.agent.sanitization`).
"""

from __future__ import annotations

import logging
import re
import threading
import traceback
from typing import Any

import structlog

from app.observability.secret_patterns import redact_sensitive_text

_REDACTED = "[REDACTED]"

# "Bearer <token>" anywhere (not only after "Authorization:").
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{6,}")
# "?apikey=..." / "&access_token=..." etc. in URLs.
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|apikey|key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"token|auth|client[_-]?secret|secret|password|passwd|sig|signature|code)=)[^&#\s'\"]+"
)
# "scheme://user:password@host" -> "scheme://user:[REDACTED]@host" (user may be empty).
_URI_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@'\"]*:)[^@\s/'\"]+@")
# "X-Api-Key: value", "api-key: value" header shapes (incl. quoted dict repr).
_API_KEY_HEADER_RE = re.compile(r"(?i)(['\"]?\b(?:x-)?api-?key['\"]?\s*[:=]\s*['\"]?)[^\s,;}&'\"]+")


def redact_log_text(value: object) -> str:
    """Return *value* as text with credentials masked (never raises)."""
    try:
        text = "" if value is None else str(value)
    except Exception:
        return "<unprintable>"
    text = _URI_USERINFO_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}@", text)
    text = _QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)
    text = _API_KEY_HEADER_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)
    text = redact_sensitive_text(text)
    return _BEARER_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)


def _redact_value(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return redact_log_text(value)
    if depth > 6 or value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, dict):
        return {k: _redact_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_redact_value(v, depth + 1) for v in value]
    if isinstance(value, BaseException):
        return redact_log_text(f"{type(value).__name__}: {value}")
    return redact_log_text(value)


def _format_exc_info(exc_info: Any) -> str | None:
    if exc_info is None or exc_info is False:
        return None
    if isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    elif not isinstance(exc_info, tuple):
        import sys

        exc_info = sys.exc_info()
    if not exc_info or exc_info[0] is None:
        return None
    return "".join(traceback.format_exception(*exc_info))


def redact_event_dict(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: render ``exc_info`` and redact every value."""
    if "exc_info" in event_dict:
        rendered = _format_exc_info(event_dict.pop("exc_info"))
        if rendered and "exception" not in event_dict:
            event_dict["exception"] = rendered
    if "stack" in event_dict and not isinstance(event_dict["stack"], str):
        event_dict["stack"] = str(event_dict["stack"])
    for key, value in list(event_dict.items()):
        event_dict[key] = _redact_value(value)
    return event_dict


# ── stdlib ──────────────────────────────────────────────────────────────────


class _RedactingRecordFactory:
    """Wraps the previous ``LogRecord`` factory and redacts each new record."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record: logging.LogRecord = self.inner(*args, **kwargs)
        try:
            _redact_record(record)
        except Exception:
            # Fail closed: never emit an unredacted line because redaction broke.
            record.msg = "<log message withheld: redaction failed>"
            record.args = None
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record


def _redact_record(record: logging.LogRecord) -> None:
    if isinstance(record.msg, dict) and not record.args:
        record.msg = _redact_value(record.msg)
    else:
        try:
            message = record.getMessage()
        except Exception:
            message = f"{record.msg!s} {record.args!r}"
        record.msg = redact_log_text(message)
        record.args = None
    if record.exc_info:
        rendered = _format_exc_info(record.exc_info)
        # Pre-rendered exc_text is what Formatter.format() prints; dropping
        # exc_info stops any other handler from re-rendering the raw traceback.
        record.exc_text = redact_log_text(rendered) if rendered else None
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact_log_text(record.exc_text)
    if record.stack_info:
        record.stack_info = redact_log_text(record.stack_info)


_lock = threading.Lock()
_installed = False


def _install_stdlib() -> None:
    current = logging.getLogRecordFactory()
    if isinstance(current, _RedactingRecordFactory):
        return
    logging.setLogRecordFactory(_RedactingRecordFactory(current))


def _install_structlog() -> None:
    processors = list(structlog.get_config()["processors"])
    if any(p is redact_event_dict for p in processors):
        return
    # Insert just before the renderer (the last processor), after exc_info /
    # stack rendering, so the rendered traceback text is redacted too.
    index = max(len(processors) - 1, 0)
    processors.insert(index, redact_event_dict)
    structlog.configure(processors=processors)


def install_log_redaction() -> None:
    """Install redaction on stdlib logging and structlog (idempotent)."""
    global _installed
    with _lock:
        _install_stdlib()
        _install_structlog()
        _installed = True


def is_installed() -> bool:
    return (
        _installed
        and isinstance(logging.getLogRecordFactory(), _RedactingRecordFactory)
        and any(p is redact_event_dict for p in structlog.get_config()["processors"])
    )


def _reset_for_tests() -> None:
    global _installed
    with _lock:
        _installed = False
        factory = logging.getLogRecordFactory()
        while isinstance(factory, _RedactingRecordFactory):
            factory = factory.inner
        logging.setLogRecordFactory(factory)
