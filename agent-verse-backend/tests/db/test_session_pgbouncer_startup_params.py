"""The app's connection startup parameters must be accepted by the compose PgBouncer.

aa947cdcd started sending statement_timeout and idle_in_transaction_session_timeout as
asyncpg ``server_settings`` (startup parameters). PgBouncer rejects any startup
parameter not in ``ignore_startup_parameters`` ("unsupported startup parameter"), so
behind the compose PgBouncer every new connection failed: signup returned 503 and
all DB-backed seeding silently failed, while /health stayed green.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from app.db.session import _server_settings

_COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"
# Parameters PgBouncer understands natively and forwards without configuration.
_NATIVE = {"client_encoding", "datestyle", "timezone", "standard_conforming_strings", "application_name"}


def _pgbouncer_ignored() -> set[str]:
    compose = yaml.safe_load(_COMPOSE.read_text())
    env = compose["services"]["pgbouncer"]["environment"]
    raw = env.get("IGNORE_STARTUP_PARAMETERS", "")
    return {p.strip().lower() for p in str(raw).split(",") if p.strip()}


def test_every_startup_parameter_is_accepted_by_compose_pgbouncer() -> None:
    settings = SimpleNamespace(db_idle_in_transaction_timeout_ms=30_000, db_statement_timeout_ms=60_000)
    sent = {k.lower() for k in _server_settings(settings)}
    assert sent, "expected the default timeouts to be sent"
    rejected = sent - _pgbouncer_ignored() - _NATIVE
    assert not rejected, f"PgBouncer would refuse connections carrying {sorted(rejected)}"


def test_disabled_timeouts_send_nothing() -> None:
    settings = SimpleNamespace(db_idle_in_transaction_timeout_ms=0, db_statement_timeout_ms=0)
    assert _server_settings(settings) == {}
