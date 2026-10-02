"""ENT-42: liveness never depends on Postgres/Redis; readiness does.

``/health`` ran the dependency checks and was the livenessProbe in every
manifest, so a DB/Redis blip restarted every API pod. ``/livez`` is now the
process-only liveness probe and ``/health/ready`` (dependency checks + startup
warm-ups) the readiness probe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.observability.health import HealthCheck

ROOT = Path(__file__).resolve().parents[2]

API_MANIFESTS = [
    "infra/k8s/backend-deployment.yaml",
    "infra/k8s/backend-deployment-blue.yaml",
    "infra/k8s/backend-deployment-green.yaml",
    "infra/helm/agentverse/templates/app-workloads.yaml",
    "helm/agentverse/templates/deployment.yaml",
]


def _probe_paths(text: str, probe: str) -> list[str]:
    """Paths of every ``<probe>: httpGet: path:`` block in a (templated) manifest."""
    return re.findall(rf"{probe}:\s*\n\s*httpGet:\s*\n\s*path:\s*(\S+)", text)


@pytest.mark.parametrize("manifest", API_MANIFESTS)
def test_api_liveness_probe_is_livez(manifest: str) -> None:
    text = (ROOT / manifest).read_text()
    # The helm app-workloads template also holds the frontend (nginx) workload.
    paths = [p for p in _probe_paths(text, "livenessProbe") if p != "/"]
    assert paths == ["/livez"], f"{manifest}: livenessProbe paths {paths}"


@pytest.mark.parametrize("manifest", API_MANIFESTS)
def test_api_readiness_probe_is_health_ready(manifest: str) -> None:
    text = (ROOT / manifest).read_text()
    paths = [p for p in _probe_paths(text, "readinessProbe") if p != "/"]
    assert paths == ["/health/ready"], f"{manifest}: readinessProbe paths {paths}"


@pytest.mark.parametrize(
    "compose", ["infra/docker-compose.yml", "infra/docker-compose.prod.yml"]
)
def test_compose_backend_healthcheck_gates_on_readiness(compose: str) -> None:
    import yaml

    data = yaml.safe_load((ROOT / compose).read_text())
    test = " ".join(str(p) for p in data["services"]["backend"]["healthcheck"]["test"])
    assert "/health/ready" in test


def test_dockerfile_healthcheck_is_liveness() -> None:
    text = (ROOT / "Dockerfile").read_text()
    healthcheck = text[text.index("HEALTHCHECK") :].split("\nCMD")[0]
    assert "/livez" in healthcheck


def _app_with_down_db() -> TestClient:
    from app.main import create_app

    app = create_app(manage_pools=False)

    async def _down() -> None:
        raise ConnectionError("postgres down")

    app.state.health.register(HealthCheck(name="postgres", check=_down))
    return TestClient(app)


def test_livez_200_with_db_down_and_no_api_key() -> None:
    client = _app_with_down_db()
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_readiness_503_with_db_down() -> None:
    client = _app_with_down_db()
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health").status_code == 503
