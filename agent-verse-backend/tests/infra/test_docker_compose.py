"""Validate docker-compose configurations are complete and correct."""
import os
from collections import defaultdict

import yaml

COMPOSE_PATH = os.path.join(os.path.dirname(__file__), "../../infra/docker-compose.yml")
PROD_PATH = os.path.join(os.path.dirname(__file__), "../../infra/docker-compose.prod.yml")


def _load_compose(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def test_dev_compose_has_all_required_services():
    compose = _load_compose(COMPOSE_PATH)
    services = compose.get("services", {})
    required = ["postgres", "redis", "keycloak", "backend", "worker", "beat", "minio",
                 "pgbouncer", "mailpit", "otel-collector", "jaeger", "searxng", "frontend"]
    missing = [s for s in required if s not in services]
    assert not missing, f"Missing services in docker-compose.yml: {missing}"


def test_keycloak_uses_postgres_not_dev_mem():
    compose = _load_compose(COMPOSE_PATH)
    kc = compose["services"]["keycloak"]
    env = kc.get("environment", {})
    db = env.get("KC_DB", "dev-mem")
    assert db != "dev-mem", "Keycloak must use postgres DB, not dev-mem (state lost on restart)"


def test_keycloak_healthcheck_uses_available_shell_transport():
    """The Keycloak image has bash but intentionally does not ship curl/wget."""
    compose = _load_compose(COMPOSE_PATH)
    healthcheck = compose["services"]["keycloak"]["healthcheck"]["test"]
    command = " ".join(str(part) for part in healthcheck)

    assert "/health/ready" in command
    assert "curl" not in command
    assert "wget" not in command
    assert "/dev/tcp/127.0.0.1/8080" in command


def test_otel_healthcheck_uses_collector_binary_present_in_image():
    """The distroless collector image exposes its binary but no shell or wget."""
    compose = _load_compose(COMPOSE_PATH)
    healthcheck = compose["services"]["otel-collector"]["healthcheck"]["test"]
    command = " ".join(str(part) for part in healthcheck)

    assert "wget" not in command
    assert command == (
        "CMD /otelcol-contrib validate --config=/etc/otel-collector-config.yaml"
    )


def test_jaeger_volume_is_initialized_for_non_root_runtime():
    """Jaeger's UID 10001 must own its persistent Badger directories."""
    compose = _load_compose(COMPOSE_PATH)
    services = compose["services"]
    initializer = services["jaeger-init"]
    initializer_command = " ".join(str(part) for part in initializer["command"])

    assert initializer["user"] == "0:0"
    assert "jaeger_data:/badger" in initializer["volumes"]
    assert "mkdir -p /badger/data /badger/key" in initializer_command
    assert "chown -R 10001:10001 /badger" in initializer_command
    assert services["jaeger"]["depends_on"]["jaeger-init"]["condition"] == (
        "service_completed_successfully"
    )


def test_mailhog_replaced_with_mailpit():
    compose = _load_compose(COMPOSE_PATH)
    services = compose.get("services", {})
    assert "mailhog" not in services, "MailHog is unmaintained — replace with mailpit"
    assert "mailpit" in services, "mailpit service must exist"


def test_prod_compose_has_goals_dlq_queue():
    if not os.path.exists(PROD_PATH):
        return
    compose = _load_compose(PROD_PATH)
    worker = compose.get("services", {}).get("worker", {})
    cmd = " ".join(str(c) for c in worker.get("command", []))
    assert "goals_dlq" in cmd, "Production worker must consume goals_dlq queue"


def test_backend_env_has_embedding_dim():
    compose = _load_compose(COMPOSE_PATH)
    backend_env = compose["services"]["backend"].get("environment", {})
    assert "EMBEDDING_DIM" in backend_env, "EMBEDDING_DIM must be in backend environment"
    assert str(backend_env["EMBEDDING_DIM"]) == "1536", "EMBEDDING_DIM must be 1536"


def test_runtime_services_load_local_provider_credentials():
    """The development API and worker need the configured provider/connector keys."""
    compose = _load_compose(COMPOSE_PATH)

    for service_name in ("backend", "worker"):
        env_files = compose["services"][service_name].get("env_file", [])
        assert "../.env" in env_files, (
            f"{service_name} must load ../.env for live development execution"
        )


def test_pgbouncer_auth_matches_postgres_scram_passwords():
    """PostgreSQL 16 initializes roles with SCRAM credentials by default."""
    compose = _load_compose(COMPOSE_PATH)
    environment = compose["services"]["pgbouncer"].get("environment", {})

    assert environment.get("AUTH_TYPE") == "scram-sha-256"


def test_backend_healthcheck_uses_runtime_dependency():
    """The slim runtime has Python but intentionally does not install curl/wget."""
    compose = _load_compose(COMPOSE_PATH)
    healthcheck = compose["services"]["backend"]["healthcheck"]["test"]
    command = " ".join(str(part) for part in healthcheck)

    assert "/health" in command
    assert "curl" not in command
    assert "wget" not in command
    assert ".venv/bin/python" in command


def test_frontend_healthcheck_uses_bound_ipv4_loopback():
    """Alpine resolves localhost to IPv6 while this nginx config listens on IPv4."""
    compose = _load_compose(COMPOSE_PATH)
    healthcheck = compose["services"]["frontend"]["healthcheck"]["test"]
    command = " ".join(str(part) for part in healthcheck)

    assert "http://127.0.0.1/health" in command


def test_dev_compose_host_ports_are_unique():
    """Every always-on service must be able to bind during a full-stack start."""
    compose = _load_compose(COMPOSE_PATH)
    owners: dict[str, list[str]] = defaultdict(list)

    for service_name, service in compose["services"].items():
        if service.get("profiles"):
            continue
        for mapping in service.get("ports", []):
            if isinstance(mapping, str):
                host_port = mapping.rsplit(":", 1)[0].split(":")[-1]
            else:
                host_port = str(mapping["published"])
            owners[host_port].append(service_name)

    collisions = {port: names for port, names in owners.items() if len(names) > 1}
    assert not collisions, f"Duplicate host port bindings: {collisions}"


def test_beat_healthcheck_checks_scheduler_process_not_worker_ping():
    """Celery control ping targets workers; beat is a scheduler, not a worker."""
    compose = _load_compose(COMPOSE_PATH)
    healthcheck = compose["services"]["beat"]["healthcheck"]["test"]
    command = " ".join(str(part) for part in healthcheck)

    assert "inspect ping" not in command
    assert "/proc/1/cmdline" in command
    assert "beat" in command


def test_dev_worker_concurrency_fits_its_memory_limit():
    """Eight prefork children repeatedly exceeded the former 512 MiB limit."""
    compose = _load_compose(COMPOSE_PATH)
    worker = compose["services"]["worker"]
    command = " ".join(str(part) for part in worker["command"])

    assert "--concurrency=2" in command
    assert worker["deploy"]["resources"]["limits"]["memory"] == "1G"


# ── PITR / backup tests ────────────────────────────────────────────────────────

def test_pgbackup_service_exists():
    """Backup service must be defined for PITR readiness."""
    compose = _load_compose(COMPOSE_PATH)
    services = compose.get("services", {})
    assert "pgbackup" in services, (
        "pgbackup service missing — add a postgres-backup-local or wal-g container"
    )


def test_pgbackup_depends_on_postgres():
    compose = _load_compose(COMPOSE_PATH)
    pgbackup = compose["services"].get("pgbackup", {})
    depends = pgbackup.get("depends_on", {})
    assert "postgres" in depends or "postgres" in str(depends), (
        "pgbackup must depend on postgres"
    )


def test_pgbackup_volume_defined():
    compose = _load_compose(COMPOSE_PATH)
    volumes = compose.get("volumes", {})
    assert "pgbackups" in volumes, "pgbackups volume must be defined"


def test_all_services_have_restart_policy():
    compose = _load_compose(COMPOSE_PATH)
    for name, svc in compose.get("services", {}).items():
        assert svc.get("restart"), f"Service '{name}' must have a restart policy"


def test_postgres_has_healthcheck():
    compose = _load_compose(COMPOSE_PATH)
    pg = compose["services"]["postgres"]
    assert "healthcheck" in pg, "postgres must have a healthcheck"
