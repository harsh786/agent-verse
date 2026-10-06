"""infra/pgbouncer/add-app-user.sh: which roles land in pgbouncer's auth file.

Runs the real wrapper with ``sh`` against a temp auth file; only its final
``exec /entrypoint.sh`` (the edoburu image's entrypoint) is pointed at a stub.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "pgbouncer" / "add-app-user.sh"
_EXEC = 'exec /entrypoint.sh "$@"'

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def _run(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    text = SCRIPT.read_text()
    assert text.count(_EXEC) == 1
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(text.replace(_EXEC, 'exec "$STUB_ENTRYPOINT" "$@"'))
    stub = tmp_path / "entrypoint.sh"
    stub.write_text('#!/bin/sh\necho "entrypoint $*"\n')
    stub.chmod(0o755)
    auth = tmp_path / "userlist.txt"
    base = {
        "PATH": "/usr/bin:/bin",
        "AUTH_FILE": str(auth),
        "STUB_ENTRYPOINT": str(stub),
        "DB_USER": "agentverse",
        "APP_DB_USER": "agentverse_app",
        "APP_DB_PASSWORD": "app-pw",
    }
    proc = subprocess.run(
        ["sh", str(wrapper), "pgbouncer", "/etc/pgbouncer/pgbouncer.ini"],
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    lines = auth.read_text().splitlines() if auth.exists() else []
    return proc, lines


def test_app_role_only_when_the_maintenance_role_is_unset(tmp_path: Path) -> None:
    proc, lines = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert lines == ['"agentverse_app" "app-pw"']
    assert "entrypoint pgbouncer /etc/pgbouncer/pgbouncer.ini" in proc.stdout


def test_the_owner_as_maintenance_role_needs_no_entry(tmp_path: Path) -> None:
    # The image writes DB_USER itself; a second line could carry another password.
    proc, lines = _run(tmp_path, MAINTENANCE_DB_USER="agentverse", MAINTENANCE_DB_PASSWORD="x")
    assert proc.returncode == 0, proc.stderr
    assert lines == ['"agentverse_app" "app-pw"']


def test_a_separate_maintenance_role_is_added(tmp_path: Path) -> None:
    proc, lines = _run(
        tmp_path, MAINTENANCE_DB_USER="agentverse_maint", MAINTENANCE_DB_PASSWORD="maint-pw"
    )
    assert proc.returncode == 0, proc.stderr
    assert lines == ['"agentverse_app" "app-pw"', '"agentverse_maint" "maint-pw"']
    assert "maint-pw" not in proc.stdout + proc.stderr  # passwords are never logged
    assert "app-pw" not in proc.stdout + proc.stderr


def test_rerun_does_not_duplicate_entries(tmp_path: Path) -> None:
    env = {"MAINTENANCE_DB_USER": "agentverse_maint", "MAINTENANCE_DB_PASSWORD": "maint-pw"}
    _run(tmp_path, **env)
    proc, lines = _run(tmp_path, **env)
    assert proc.returncode == 0, proc.stderr
    assert lines == ['"agentverse_app" "app-pw"', '"agentverse_maint" "maint-pw"']


def test_a_separate_maintenance_role_without_password_fails_closed(tmp_path: Path) -> None:
    proc, _ = _run(tmp_path, MAINTENANCE_DB_USER="agentverse_maint")
    assert proc.returncode != 0
    assert "MAINTENANCE_DB_PASSWORD" in proc.stderr
    assert "entrypoint" not in proc.stdout  # pgbouncer never starts half-configured


def test_the_app_role_is_refused_as_the_maintenance_role(tmp_path: Path) -> None:
    # NOBYPASSRLS: every cross-tenant system job would silently see one tenant.
    proc, _ = _run(tmp_path, MAINTENANCE_DB_USER="agentverse_app", MAINTENANCE_DB_PASSWORD="x")
    assert proc.returncode == 1
    assert "must not be the application role" in proc.stderr
    assert "entrypoint" not in proc.stdout
