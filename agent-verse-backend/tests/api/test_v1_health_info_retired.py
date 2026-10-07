"""a10-F253-03: the static /v1/health and /v1/info stubs are retired.

``GET /v1/health`` answered ``{"status": "ok"}`` without checking anything and
``GET /v1/info`` returned a hardcoded version "1.0.0" and a changelog URL on a
domain the project does not own. Nothing called either (no frontend, GitHub
Action or SDK consumer). Real health lives at /livez (liveness), /health and
/health/ready (dependency readiness) and /status (public summary).
"""

from __future__ import annotations

from app.main import create_app


def test_v1_health_and_info_are_not_mounted_but_real_health_is() -> None:
    paths = set(create_app().openapi().get("paths", {}))
    assert "/v1/health" not in paths
    assert "/v1/info" not in paths
    assert {"/livez", "/health", "/health/ready", "/status"} <= paths
    # Other /v1 routers are unaffected.
    assert any(p.startswith("/v1/org") for p in paths)
