"""Validate docker-compose configurations are complete and correct."""
import os
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
