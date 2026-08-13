from __future__ import annotations

from app.execution_environment.envelope import build_envelope, sign_envelope
from app.execution_environment.kubernetes_runner import KubernetesRunner, build_workload_manifests
from app.execution_environment.models import ExecutionRequest, RunnerType

IMAGE = "registry.example/agentverse-code@sha256:" + "a" * 64


class FakeKubernetesClient:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.deleted: list[tuple[str, str]] = []

    async def health(self) -> bool:
        return True

    async def create(self, kind: str, manifest: dict) -> None:
        self.created.append({"kind": kind, "manifest": manifest})

    async def wait_job(self, name: str, timeout_seconds: int) -> dict:
        return {"succeeded": True, "exit_code": 0}

    async def read_logs(self, name: str, maximum_bytes: int) -> str:
        return '{"status":"complete","success":true}'

    async def delete_workload(self, name: str, fencing_token: str) -> None:
        self.deleted.append((name, fencing_token))


def request() -> ExecutionRequest:
    envelope = build_envelope(
        tenant_id="tenant-1",
        goal_id="goal-1",
        goal_text="calculate",
        runner_type=RunnerType.KUBERNETES,
    )
    envelope.spec.image = IMAGE
    envelope.spec.image_tag = "sha256:" + "a" * 64
    sign_envelope(envelope)
    return ExecutionRequest(envelope=envelope, runner_type=RunnerType.KUBERNETES)


def test_manifest_enforces_pod_and_network_isolation() -> None:
    job, network = build_workload_manifests(request(), namespace="agentverse-code")
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert container["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert not any("hostPath" in volume for volume in pod["volumes"])
    assert network["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert network["spec"]["ingress"] == [] and network["spec"]["egress"] == []


def test_manifest_rejects_mutable_or_privileged_workloads() -> None:
    unsafe = request()
    unsafe.envelope.spec.image = "registry.example/agentverse-code:latest"
    try:
        build_workload_manifests(unsafe, namespace="agentverse-code")
    except ValueError as error:
        assert "immutable" in str(error)
    else:
        raise AssertionError("mutable image must be rejected")
    privileged = request()
    privileged.envelope.policy.allow_privileged = True
    try:
        build_workload_manifests(privileged, namespace="agentverse-code")
    except PermissionError:
        pass
    else:
        raise AssertionError("privileged workload must be rejected")


async def test_fake_client_lifecycle_always_cleans_up() -> None:
    client = FakeKubernetesClient()
    runner = KubernetesRunner(client=client, namespace="agentverse-code")
    assert (await runner.health_check.check()).healthy
    result = await runner.run(request())
    assert result.success is True
    assert [item["kind"] for item in client.created] == ["NetworkPolicy", "Job"]
    assert len(client.deleted) == 1
    receipt = await runner.cancel(result.capsule_id, "operator")
    assert receipt.cleanup_state == "complete"
