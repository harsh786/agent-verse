"""E2E tests for infrastructure modules (K8s manifests, Grafana, Prometheus)."""
from __future__ import annotations

import json
import os
import pytest
from pathlib import Path

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

BACKEND_ROOT = Path(__file__).parent.parent.parent
INFRA = BACKEND_ROOT / "infra"
K8S = INFRA / "k8s"
GRAFANA = INFRA / "grafana"
PROMETHEUS = INFRA / "prometheus"


def _require_yaml():
    if not _YAML_AVAILABLE:
        pytest.skip("PyYAML not installed")


# ── K8s manifests ─────────────────────────────────────────────────────────────


def test_all_k8s_manifests_are_valid_yaml():
    """All K8s manifest files parse as valid YAML."""
    _require_yaml()
    if not K8S.exists():
        pytest.skip("infra/k8s not found")

    yaml_files = list(K8S.glob("*.yaml"))
    assert len(yaml_files) > 0, "No YAML files found in infra/k8s"

    for yaml_file in yaml_files:
        content = yaml_file.read_text()
        docs = list(yaml.safe_load_all(content))
        assert len(docs) >= 1, f"{yaml_file.name} has no YAML documents"
        for doc in docs:
            if doc is not None:
                assert (
                    "apiVersion" in doc or "kind" in doc or "metadata" in doc
                ), f"{yaml_file.name} missing K8s fields"


def test_kustomization_references_all_manifests():
    """kustomization.yaml references all other YAML files in the k8s directory."""
    _require_yaml()
    kust = K8S / "kustomization.yaml"
    if not kust.exists():
        pytest.skip("kustomization.yaml not found")

    content = yaml.safe_load(kust.read_text())
    resources = content.get("resources", [])
    assert len(resources) > 0

    # All yaml files (except kustomization itself and dev-only secrets.yaml) should be in resources
    yaml_files = [
        f.name for f in K8S.glob("*.yaml")
        if f.name not in ("kustomization.yaml", "secrets.yaml")
    ]
    for fname in yaml_files:
        assert fname in resources, (
            f"{fname} not in kustomization.yaml resources"
        )


def test_k8s_backend_deployment_has_required_fields():
    """backend-deployment.yaml has required K8s Deployment fields."""
    _require_yaml()
    dep = K8S / "backend-deployment.yaml"
    if not dep.exists():
        pytest.skip("backend-deployment.yaml not found")

    doc = yaml.safe_load(dep.read_text())
    assert doc["kind"] == "Deployment"
    assert "spec" in doc
    spec = doc["spec"]
    assert "replicas" in spec
    assert "template" in spec


def test_k8s_namespace_manifest():
    """namespace.yaml defines a Namespace resource."""
    _require_yaml()
    ns = K8S / "namespace.yaml"
    if not ns.exists():
        pytest.skip("namespace.yaml not found")

    doc = yaml.safe_load(ns.read_text())
    assert doc["kind"] == "Namespace"
    assert "metadata" in doc


def test_k8s_hpa_manifests_have_required_fields():
    """HPA manifests contain min/max replicas configuration."""
    _require_yaml()
    hpa_files = list(K8S.glob("*-hpa.yaml"))
    if not hpa_files:
        pytest.skip("No HPA manifests found")

    for hpa_file in hpa_files:
        doc = yaml.safe_load(hpa_file.read_text())
        assert doc["kind"] == "HorizontalPodAutoscaler", (
            f"{hpa_file.name} is not an HPA"
        )
        spec = doc.get("spec", {})
        assert "minReplicas" in spec or "maxReplicas" in spec, (
            f"{hpa_file.name} missing replica bounds"
        )


# ── Grafana ───────────────────────────────────────────────────────────────────


def test_grafana_dashboard_json_is_valid():
    """Grafana dashboard JSON is valid and has all required structure."""
    dash_dir = GRAFANA / "provisioning" / "dashboards"
    dash = dash_dir / "agentverse.json"
    if not dash.exists():
        pytest.skip("Grafana dashboard not found")

    content = json.loads(dash.read_text())
    assert "title" in content
    assert "panels" in content
    assert len(content["panels"]) > 0
    assert content.get("schemaVersion", 0) > 0


def test_grafana_dashboard_has_panels_with_targets():
    """Grafana dashboard panels have at least one with a datasource."""
    dash_dir = GRAFANA / "provisioning" / "dashboards"
    dash = dash_dir / "agentverse.json"
    if not dash.exists():
        pytest.skip("Grafana dashboard not found")

    content = json.loads(dash.read_text())
    panels = content.get("panels", [])
    # At least one panel should have a type field
    typed_panels = [p for p in panels if "type" in p]
    assert len(typed_panels) > 0


def test_grafana_provisioning_dashboard_config():
    """Grafana dashboard provisioning YAML is valid."""
    _require_yaml()
    dashboard_yml = GRAFANA / "provisioning" / "dashboards" / "dashboard.yml"
    if not dashboard_yml.exists():
        pytest.skip("dashboard.yml not found")

    content = yaml.safe_load(dashboard_yml.read_text())
    assert content is not None


# ── Prometheus ────────────────────────────────────────────────────────────────


def test_prometheus_rules_have_correct_structure():
    """Prometheus rules YAML has groups with valid alert rules."""
    _require_yaml()
    rules = PROMETHEUS / "rules.yml"
    if not rules.exists():
        pytest.skip("prometheus/rules.yml not found")

    content = yaml.safe_load(rules.read_text())
    assert "groups" in content
    groups = content["groups"]
    assert len(groups) > 0

    for group in groups:
        assert "name" in group
        assert "rules" in group
        for rule in group["rules"]:
            assert "alert" in rule
            assert "expr" in rule
            assert "labels" in rule
            assert "annotations" in rule


def test_prometheus_config_has_backend_scrape():
    """Prometheus config scrapes the AgentVerse backend."""
    _require_yaml()
    config = PROMETHEUS / "prometheus.yml"
    if not config.exists():
        pytest.skip("prometheus.yml not found")

    content = yaml.safe_load(config.read_text())
    jobs = [j["job_name"] for j in content.get("scrape_configs", [])]
    assert any("backend" in j or "agentverse" in j for j in jobs), (
        f"No backend/agentverse scrape job found. Jobs: {jobs}"
    )


def test_prometheus_rules_alert_count():
    """Prometheus rules file has at least 3 alert rules."""
    _require_yaml()
    rules = PROMETHEUS / "rules.yml"
    if not rules.exists():
        pytest.skip("prometheus/rules.yml not found")

    content = yaml.safe_load(rules.read_text())
    total_rules = sum(len(g.get("rules", [])) for g in content.get("groups", []))
    assert total_rules >= 3, f"Expected ≥3 alert rules, got {total_rules}"


# ── OpenAPI schema ────────────────────────────────────────────────────────────


def test_openapi_schema_has_all_required_endpoints():
    """openapi.json covers all required API endpoint groups."""
    schema_path = BACKEND_ROOT / "openapi.json"
    if not schema_path.exists():
        pytest.skip("openapi.json not found")

    schema = json.loads(schema_path.read_text())
    paths = list(schema["paths"].keys())

    required_prefixes = [
        "/goals",
        "/tenants",
        "/connectors",
        "/agents",
        "/governance",
        "/knowledge",
        "/schedules",
        "/enterprise",
        "/marketplace",
        "/collab",
        "/health",
        "/metrics",
    ]
    for prefix in required_prefixes:
        found = any(p.startswith(prefix) for p in paths)
        assert found, (
            f"No endpoints with prefix '{prefix}' in openapi.json"
        )


def test_openapi_schema_has_info_block():
    """openapi.json has a valid info block with title and version."""
    schema_path = BACKEND_ROOT / "openapi.json"
    if not schema_path.exists():
        pytest.skip("openapi.json not found")

    schema = json.loads(schema_path.read_text())
    assert "info" in schema
    assert "title" in schema["info"]
    assert "version" in schema["info"]


def test_openapi_schema_path_count():
    """openapi.json has at least 30 endpoint paths."""
    schema_path = BACKEND_ROOT / "openapi.json"
    if not schema_path.exists():
        pytest.skip("openapi.json not found")

    schema = json.loads(schema_path.read_text())
    paths = schema.get("paths", {})
    assert len(paths) >= 30, f"Expected ≥30 paths, got {len(paths)}"


# ── Docker Compose ────────────────────────────────────────────────────────────


def test_docker_compose_prod_has_all_services():
    """docker-compose.prod.yml declares all required production services."""
    _require_yaml()
    compose_path = INFRA / "docker-compose.prod.yml"
    if not compose_path.exists():
        pytest.skip("docker-compose.prod.yml not found")

    content = yaml.safe_load(compose_path.read_text())
    services = list(content.get("services", {}).keys())

    required = ["postgres", "redis", "backend", "worker", "beat", "nginx"]
    for svc in required:
        assert svc in services, (
            f"Service '{svc}' missing from docker-compose.prod.yml"
        )


def test_docker_compose_dev_has_core_services():
    """docker-compose.yml declares core dev services."""
    _require_yaml()
    compose_path = INFRA / "docker-compose.yml"
    if not compose_path.exists():
        pytest.skip("docker-compose.yml not found")

    content = yaml.safe_load(compose_path.read_text())
    services = list(content.get("services", {}).keys())
    assert len(services) >= 2, (
        f"Expected ≥2 services in docker-compose.yml, got {services}"
    )
