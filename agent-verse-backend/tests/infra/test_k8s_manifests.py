from pathlib import Path

K8S_DIR = Path(__file__).resolve().parents[2] / "infra" / "k8s"


def read_manifest(name: str) -> str:
    return (K8S_DIR / name).read_text()


def kustomization_resources() -> set[str]:
    resources: set[str] = set()
    in_resources = False

    for raw_line in read_manifest("kustomization.yaml").splitlines():
        line = raw_line.strip()
        if line == "resources:":
            in_resources = True
            continue
        if not in_resources:
            continue
        if raw_line.startswith("  - "):
            resources.add(line.removeprefix("- "))
            continue
        if line and not raw_line.startswith(" "):
            break

    return resources


def test_kustomization_includes_phase_12_resources() -> None:
    resources = kustomization_resources()

    assert {
        "migration-job.yaml",
        "networkpolicy.yaml",
        "backend-pdb.yaml",
        "worker-pdb.yaml",
        "external-secret.yaml",
        "worker-hpa.yaml",
    } <= resources
    assert "secrets.yaml" not in resources


def test_migration_job_runs_alembic_upgrade_head_before_rollout() -> None:
    manifest = read_manifest("migration-job.yaml")

    assert "kind: Job" in manifest
    assert "name: agentverse-db-migration" in manifest
    assert "argocd.argoproj.io/hook: PreSync" in manifest
    assert "alembic" in manifest
    assert "upgrade" in manifest
    assert "head" in manifest
    assert "secretRef:" in manifest
    assert "name: agentverse-secrets" in manifest


def test_networkpolicy_limits_backend_worker_data_store_access() -> None:
    manifest = read_manifest("networkpolicy.yaml")

    assert "kind: NetworkPolicy" in manifest
    assert "agentverse-backend" in manifest
    assert "agentverse-worker" in manifest
    assert "policyTypes:" in manifest
    assert "Egress" in manifest
    assert "Ingress" in manifest
    assert "port: 5432" in manifest
    assert "port: 6379" in manifest
    assert "app: pgbouncer" in manifest
    assert "app: redis" in manifest
    assert "ipBlock:" in manifest
    assert "cidr: 0.0.0.0/0" in manifest
    assert "port: 443" in manifest


def test_backend_and_worker_pdbs_protect_rollouts() -> None:
    expected_targets = {
        "backend-pdb.yaml": "agentverse-backend",
        "worker-pdb.yaml": "agentverse-worker",
    }

    for manifest_name, app_name in expected_targets.items():
        manifest = read_manifest(manifest_name)
        assert "kind: PodDisruptionBudget" in manifest
        assert f"name: {app_name}-pdb" in manifest
        assert f"app: {app_name}" in manifest
        assert "minAvailable: 1" in manifest


def test_external_secret_replaces_raw_secret_placeholder() -> None:
    manifest = read_manifest("external-secret.yaml")

    assert "kind: ExternalSecret" in manifest
    assert "name: agentverse-secrets" in manifest
    assert "secretStoreRef:" in manifest
    assert "stringData:" not in manifest
    assert "CHANGE_ME" not in manifest
    assert "secretKey: AGENTVERSE_VAULT_KEY" in manifest

    dev_secret = K8S_DIR / "secrets.yaml"
    if dev_secret.exists():
        assert "dev-only" in dev_secret.read_text().lower()


def test_worker_hpa_uses_queue_depth_external_metric() -> None:
    manifest = read_manifest("worker-hpa.yaml")

    assert "kind: HorizontalPodAutoscaler" in manifest
    assert "name: agentverse-worker" in manifest
    assert "type: External" in manifest
    assert "name: agentverse_queue_depth" in manifest
    assert "celery_queue_depth" not in manifest
    selector_start = manifest.index("          selector:")
    selector_block = manifest[selector_start:manifest.index("        target:", selector_start)]
    assert "matchLabels:" in selector_block
    assert "queue: goals" in selector_block
    assert "queue-depth:" not in selector_block
    assert "averageValue:" in manifest
