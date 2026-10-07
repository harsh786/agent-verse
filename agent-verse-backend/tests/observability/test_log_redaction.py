"""OI-3: secrets never reach API / worker logs (messages, exception text, tracebacks).

Every fake secret below is assembled from split literals so no provider-key-shaped
string exists in the source tree.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from app.observability import log_redaction
from app.observability.log_redaction import (
    install_log_redaction,
    redact_event_dict,
    redact_log_text,
)

BEARER = "eyAbc" + "DEF123456789" + "ghiJKL"
ANTHROPIC_KEY = "sk-" + "ant-" + "api03-" + "Q" * 40
OPENAI_KEY = "sk-" + "proj-" + "Z" * 40
GOOGLE_KEY = "AI" + "za" + "S" * 35
GROQ_KEY = "gs" + "k_" + "G" * 40
XAI_KEY = "xa" + "i-" + "X" * 40
VOYAGE_KEY = "p" + "a-" + "V" * 40
HF_KEY = "h" + "f_" + "H" * 34
DB_PASSWORD = "hunter" + "2pw" + "Secret"
QUERY_KEY = "qk" + "Value" + "987654"

ALL_SECRETS = (
    BEARER,
    ANTHROPIC_KEY,
    OPENAI_KEY,
    GOOGLE_KEY,
    GROQ_KEY,
    XAI_KEY,
    VOYAGE_KEY,
    HF_KEY,
    DB_PASSWORD,
    QUERY_KEY,
)


def _assert_clean(text: str) -> None:
    for secret in ALL_SECRETS:
        assert secret not in text, f"secret leaked: {secret[:6]}..."


@pytest.mark.parametrize(
    "raw",
    [
        f"401 from upstream, sent Bearer {BEARER}",
        f"headers={{'Authorization': 'Bearer {BEARER}'}}",
        f"Authorization: Bearer {BEARER}",
        f"x-api-key: {ANTHROPIC_KEY}",
        f"provider rejected key {ANTHROPIC_KEY} (401)",
        f"provider rejected key {OPENAI_KEY}",
        f"GET https://generativelanguage.googleapis.com/v1/models?key={GOOGLE_KEY}",
        f"groq {GROQ_KEY} xai {XAI_KEY} voyage {VOYAGE_KEY} hf {HF_KEY}",
        f"connect postgresql://agentverse:{DB_PASSWORD}@db:5432/agentverse failed",
        f"redis://:{DB_PASSWORD}@redis:6379/0 refused",
        f"GET https://api.example.com/v1/items?apikey={QUERY_KEY}&page=2",
        f"GET https://api.example.com/v1/items?page=2&access_token={QUERY_KEY}",
        f"GET https://api.example.com/v1/items?client_secret={QUERY_KEY}",
    ],
)
def test_redact_log_text_masks_every_secret_shape(raw: str) -> None:
    out = redact_log_text(raw)
    _assert_clean(out)
    assert "[REDACTED]" in out


def test_redact_log_text_keeps_non_secret_context() -> None:
    out = redact_log_text(
        f"connect postgresql://agentverse:{DB_PASSWORD}@db:5432/agentverse failed"
    )
    assert "postgresql://agentverse:[REDACTED]@db:5432/agentverse" in out
    out = redact_log_text(f"GET https://h/x?page=2&apikey={QUERY_KEY}&sort=asc")
    assert "page=2" in out and "sort=asc" in out


@pytest.fixture
def restore_logging() -> Iterator[None]:
    factory = logging.getLogRecordFactory()
    config = structlog.get_config()
    yield
    logging.setLogRecordFactory(factory)
    structlog.configure(**config)
    log_redaction._reset_for_tests()


def _stdlib_capture(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    lg = logging.getLogger(name)
    lg.handlers = [handler]
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    return lg, stream


def test_stdlib_records_are_redacted_including_args_and_traceback(
    restore_logging: None,
) -> None:
    install_log_redaction()
    lg, stream = _stdlib_capture("oi3.stdlib")
    exc = PermissionError(f"denied: upstream said Bearer {BEARER}")
    lg.info("goal_denied_by_governance goal_id=%s: %s", "g1", exc)
    try:
        raise RuntimeError(f"provider key {ANTHROPIC_KEY} rejected")
    except RuntimeError:
        lg.exception("call failed for %s", f"postgresql://u:{DB_PASSWORD}@h/db")
    out = stream.getvalue()
    _assert_clean(out)
    assert "goal_denied_by_governance goal_id=g1" in out
    assert "Traceback" in out and "RuntimeError" in out


def test_install_is_idempotent(restore_logging: None) -> None:
    install_log_redaction()
    first = logging.getLogRecordFactory()
    install_log_redaction()
    assert logging.getLogRecordFactory() is first
    processors = structlog.get_config()["processors"]
    assert sum(1 for p in processors if p is redact_event_dict) == 1


def test_structlog_default_config_is_redacted_on_workers(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # A worker never calls configure_logging: start from structlog's defaults.
    structlog.reset_defaults()
    install_log_redaction()
    log = structlog.get_logger("oi3.worker")
    log.info("goal_denied_by_governance goal_id=%s: %s", "g1", f"Bearer {BEARER}")
    try:
        raise PermissionError(f"Authorization: Bearer {BEARER} key={GOOGLE_KEY}")
    except PermissionError:
        log.exception("worker_failed", detail=f"redis://:{DB_PASSWORD}@r:6379/0")
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "goal_denied_by_governance" in out
    assert "PermissionError" in out


def test_api_configure_logging_redacts_json_and_exceptions(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.observability.logging import configure_logging

    configure_logging(level="INFO", json_logs=True)
    log = structlog.get_logger("oi3.api")
    try:
        raise ValueError(f"bad token {OPENAI_KEY}")
    except ValueError:
        log.exception("request_failed", url=f"https://x/y?api_key={QUERY_KEY}")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    _assert_clean("\n".join(lines))
    payload = json.loads(lines[-1])
    assert payload["event"] == "request_failed"
    assert "ValueError" in payload["exception"]


def test_redact_event_dict_handles_nested_values() -> None:
    event = {
        "event": f"boom Bearer {BEARER}",
        "ctx": {"headers": {"Authorization": f"Bearer {BEARER}"}, "keys": [ANTHROPIC_KEY]},
        "count": 3,
    }
    out = redact_event_dict(None, "info", event)
    _assert_clean(json.dumps(out))
    assert out["count"] == 3


def test_celery_app_import_installs_redaction() -> None:
    """Workers / beat import the Celery app and never call configure_logging."""
    import os
    import subprocess
    import sys

    code = (
        "import logging, io\n"
        "import app.scaling.celery_app\n"
        "from app.observability import log_redaction\n"
        "assert log_redaction.is_installed()\n"
        "s = io.StringIO(); h = logging.StreamHandler(s)\n"
        "lg = logging.getLogger('oi3.celery'); lg.handlers = [h]; lg.propagate = False\n"
        "lg.warning('task failed: %s', 'Bearer ' + 'abcDEF' + '1234567890')\n"
        "assert 'abcDEF1234567890' not in s.getvalue(), s.getvalue()\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().endswith("ok")


def test_uvicorn_access_record_keeps_its_args_tuple() -> None:
    """uvicorn's AccessFormatter unpacks record.args; None broke every access line."""
    import logging

    from uvicorn.logging import AccessFormatter

    from app.observability.log_redaction import _redact_record

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("172.18.0.1:5000", "GET", "/goals?api_key=av_pro_secretvalue123", "1.1", 200), None,
    )
    _redact_record(record)
    assert isinstance(record.args, tuple) and len(record.args) == 5
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert "av_pro_secretvalue123" not in line
    assert "GET /goals" in line and "200" in line


def test_secret_only_visible_when_joined_is_still_redacted() -> None:
    import logging

    from app.observability.log_redaction import _redact_record

    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "Authorization: Bearer %s", ("abcdef0123456789secret",), None,
    )
    _redact_record(record)
    assert "abcdef0123456789secret" not in record.getMessage()
