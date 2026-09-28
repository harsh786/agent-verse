"""Regression: the app-wired ProactiveEngine dropped every audit record.

``_wire_proactive_engine`` captured ``app.state.audit_log`` at wiring time and
called ``record(event_dict)`` without ``tenant_ctx`` inside
``suppress(Exception)`` — the TypeError was swallowed, so nothing was audited,
and the lifespan's DB-backed AuditLog swap was never seen.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI

from app.bootstrap.routers import _wire_proactive_engine
from app.governance.audit import AuditLog


def _app() -> FastAPI:
    app = FastAPI()
    app.state.chat_service = SimpleNamespace()
    return app


def test_wired_audit_writes_a_real_audit_event_for_the_tenant() -> None:
    app = _app()
    app.state.audit_log = AuditLog()  # the one present at wiring time
    _wire_proactive_engine(app)
    swapped = AuditLog()  # lifespan swaps in the DB-backed log later
    app.state.audit_log = swapped

    app.state.proactive_engine._audit(
        {"source": "proactive", "tenant_id": "t1", "action": "nudge", "channel": "web"}
    )

    events = swapped._log.get("t1", [])
    assert len(events) == 1
    assert events[0].tool_name == "proactive.nudge"
    assert json.loads(events[0].note)["channel"] == "web"
