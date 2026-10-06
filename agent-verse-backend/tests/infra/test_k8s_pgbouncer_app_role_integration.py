"""NF-16 follow-up (integration): the k8s pgBouncer auth file lists the app and maintenance roles.

Runs the manifest's image with its own command, the ConfigMap script and the
manifest env (secret refs resolved to test values), and reads the auth file the
wrapper + the image entrypoint produced. The local ``edoburu/pgbouncer`` image
is used (no pull).
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.integration

K8S = Path(__file__).resolve().parents[2] / "infra" / "k8s"
_SECRET = {
    "POSTGRES_PASSWORD": "owner-pw",
    "APP_DB_USER": "agentverse_app",
    "APP_DB_PASSWORD": "app-pw-123",
    "MAINTENANCE_DB_USER": "agentverse_maint",
    "MAINTENANCE_DB_PASSWORD": "maint-pw-456",
}


def _manifest() -> tuple[dict[str, Any], str]:
    docs = list(yaml.safe_load_all((K8S / "pgbouncer-deployment.yaml").read_text()))
    deploy = next(d for d in docs if d and d["kind"] == "Deployment")
    script = next(d for d in docs if d and d["kind"] == "ConfigMap")["data"]["add-app-user.sh"]
    return deploy["spec"]["template"]["spec"]["containers"][0], script


def test_pgbouncer_auth_file_lists_owner_and_app_role() -> None:
    from testcontainers.core.container import DockerContainer

    container_spec, script = _manifest()
    env: dict[str, str] = {}
    for e in container_spec["env"]:
        ref = (e.get("valueFrom") or {}).get("secretKeyRef")
        env[e["name"]] = _SECRET[ref["key"]] if ref else str(e["value"])

    # Under the repo (inside $HOME): colima shares only the home directory.
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
        Path(tmp, "add-app-user.sh").write_text(script)
        # Same command as the manifest; the image entrypoint then execs this
        # instead of pgbouncer, so the produced auth file is printed.
        cmd = " ".join(container_spec["command"]) + " cat /etc/pgbouncer/userlist.txt"
        container = (
            DockerContainer(container_spec["image"])
            .with_volume_mapping(tmp, "/opt/agentverse", "ro")
            .with_kwargs(entrypoint=[])
            .with_command(cmd)
        )
        for k, v in env.items():
            container = container.with_env(k, v)
        try:
            container.start()
        except Exception as exc:  # pragma: no cover - Docker down
            pytest.skip(f"could not start the pgbouncer image: {exc}")
        try:
            deadline = time.monotonic() + 60
            out = ""
            while time.monotonic() < deadline:
                stdout, stderr = container.get_logs()
                out = (stdout or b"").decode() + (stderr or b"").decode()
                if ('"agentverse_maint"' in out and '"agentverse"' in out) or "can't" in out:
                    break
                time.sleep(0.5)
        finally:
            container.stop()

    assert '"agentverse_app" "app-pw-123"' in out, out
    assert '"agentverse"' in out, out  # the owner, written by the image itself
    # A separate BYPASSRLS maintenance role (MAINTENANCE_DATABASE_URL).
    assert '"agentverse_maint" "maint-pw-456"' in out, out
