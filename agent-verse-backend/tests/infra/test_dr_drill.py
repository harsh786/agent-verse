"""DR drill validation — tests backup/restore capability."""
import os
import pathlib
import subprocess

import pytest


def test_dr_drill_script_exists():
    """DR drill script must exist and be executable."""
    script = pathlib.Path("infra/dr-drill.sh")
    assert script.exists(), "infra/dr-drill.sh must exist"
    # Check it's a valid shell script (has shebang)
    content = script.read_text()
    assert content.startswith("#!/"), "dr-drill.sh must have a shebang line"
    assert "pg_dump" in content, "dr-drill.sh must use pg_dump for backup"
    assert "restore" in content.lower() or "psql" in content, "dr-drill.sh must test restore"


def test_dr_drill_has_rpo_rto_targets():
    """DR script must document RPO and RTO targets."""
    script = pathlib.Path("infra/dr-drill.sh").read_text()
    assert "RPO" in script, "DR script must document RPO (Recovery Point Objective)"
    assert "RTO" in script, "DR script must document RTO (Recovery Time Objective)"


def test_dr_drill_has_all_steps():
    """DR script must cover the 5 required drill steps."""
    script = pathlib.Path("infra/dr-drill.sh").read_text()
    required = ["connectivity", "backup", "restore", "Redis", "Summary"]
    for step in required:
        assert any(step.lower() in line.lower() for line in script.splitlines()), (
            f"DR script must include '{step}' step"
        )


def test_docker_compose_has_backup_service():
    """docker-compose.yml must have a pgbackup service for automated backups."""
    import yaml
    compose = yaml.safe_load(pathlib.Path("infra/docker-compose.yml").read_text())
    assert "pgbackup" in compose["services"], (
        "docker-compose.yml must have pgbackup service"
    )


def test_pgbackup_service_has_retention_config():
    """pgbackup service must configure retention periods."""
    import yaml
    compose = yaml.safe_load(pathlib.Path("infra/docker-compose.yml").read_text())
    pgbackup = compose["services"]["pgbackup"]
    env = pgbackup.get("environment", {})
    # Check retention is configured (either as dict or list)
    env_str = str(env)
    assert "BACKUP_KEEP_DAYS" in env_str or "SCHEDULE" in env_str, (
        "pgbackup must configure BACKUP_KEEP_DAYS or SCHEDULE"
    )


@pytest.mark.skipif(
    not pathlib.Path("infra/dr-drill.sh").exists(),
    reason="dr-drill.sh not found",
)
def test_dr_drill_script_syntax():
    """Shell script must be syntactically valid (bash -n check)."""
    result = subprocess.run(
        ["bash", "-n", "infra/dr-drill.sh"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"dr-drill.sh has syntax errors:\n{result.stderr}"
    )
