"""Production Kubernetes Job runner with deny-by-default workload isolation."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.execution_environment.envelope import verify_envelope
from app.execution_environment.health import HealthStatus, RunnerHealthCheck
from app.execution_environment.models import (
    CodeCancellationReceipt,
    ExecutionFailureReason,
    ExecutionRequest,
    ExecutionResult,
    RunnerType,
)
from app.execution_environment.runner_client import BaseRunner

_DIGEST_IMAGE = re.compile(r"^[a-zA-Z0-9._/:+-]+@sha256:[a-f0-9]{64}$")


class KubernetesClient(Protocol):
    async def health(self) -> bool: ...
    async def create(self, kind: str, manifest: dict[str, Any]) -> None: ...
    async def wait_job(self, name: str, timeout_seconds: int) -> dict[str, Any]: ...
    async def read_logs(self, name: str, maximum_bytes: int) -> str: ...
    async def delete_workload(self, name: str, fencing_token: str) -> None: ...


class InClusterKubernetesClient:
    """Small async Kubernetes REST client using the mounted service-account identity."""

    def __init__(self, namespace: str) -> None:
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        if not host:
            raise RuntimeError("Kubernetes in-cluster service host is unavailable")
        token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        self._namespace = namespace
        self._base = f"https://{host}:{port}"
        self._headers = {"Authorization": f"Bearer {token_path.read_text().strip()}"}
        self._verify: str | bool = str(ca_path) if ca_path.exists() else True

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base, headers=self._headers, verify=self._verify, timeout=10
        ) as client:
            response = await client.request(method, path, **kwargs)
            if response.status_code not in {200, 201, 202, 404}:
                response.raise_for_status()
            return response

    async def health(self) -> bool:
        try:
            return (await self._request("GET", "/version")).status_code == 200
        except Exception:
            return False

    async def create(self, kind: str, manifest: dict[str, Any]) -> None:
        resource = "jobs" if kind == "Job" else "networkpolicies"
        group = "apis/batch/v1" if kind == "Job" else "apis/networking.k8s.io/v1"
        await self._request(
            "POST", f"/{group}/namespaces/{self._namespace}/{resource}", json=manifest
        )

    async def wait_job(self, name: str, timeout_seconds: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        path = f"/apis/batch/v1/namespaces/{self._namespace}/jobs/{name}"
        while time.monotonic() < deadline:
            response = await self._request("GET", path)
            body = response.json()
            status = body.get("status", {})
            if status.get("succeeded"):
                return {"succeeded": True, "exit_code": 0}
            if status.get("failed"):
                return {"succeeded": False, "exit_code": 1}
            await asyncio.sleep(1)
        return {"succeeded": False, "timed_out": True, "exit_code": None}

    async def read_logs(self, name: str, maximum_bytes: int) -> str:
        response = await self._request(
            "GET",
            f"/api/v1/namespaces/{self._namespace}/pods",
            params={"labelSelector": f"job-name={name}"},
        )
        items = response.json().get("items", [])
        if not items:
            return ""
        pod = items[0]["metadata"]["name"]
        logs = await self._request(
            "GET", f"/api/v1/namespaces/{self._namespace}/pods/{pod}/log"
        )
        return logs.text.encode()[:maximum_bytes].decode(errors="replace")

    async def delete_workload(self, name: str, fencing_token: str) -> None:
        options = {"propagationPolicy": "Foreground", "gracePeriodSeconds": 0}
        await self._request(
            "DELETE",
            f"/apis/batch/v1/namespaces/{self._namespace}/jobs/{name}",
            headers={"X-AgentVerse-Fencing-Token": fencing_token},
            json=options,
        )
        await self._request(
            "DELETE",
            f"/apis/networking.k8s.io/v1/namespaces/{self._namespace}/networkpolicies/{name}",
            headers={"X-AgentVerse-Fencing-Token": fencing_token},
            json=options,
        )


class KubernetesRunnerHealthCheck(RunnerHealthCheck):
    def __init__(self, client: KubernetesClient | None) -> None:
        self._client = client

    @property
    def runner_type(self) -> str:
        return RunnerType.KUBERNETES.value

    async def check(self) -> HealthStatus:
        started = time.monotonic()
        healthy = self._client is not None and await self._client.health()
        return HealthStatus(
            healthy=healthy,
            runner_type=self.runner_type,
            message="Kubernetes API ready" if healthy else "Kubernetes API unavailable",
            latency_ms=(time.monotonic() - started) * 1000,
        )


def build_workload_manifests(
    request: ExecutionRequest, *, namespace: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a digest-pinned Job and matching default-deny NetworkPolicy."""
    envelope = request.envelope
    if envelope.policy.allow_privileged or envelope.policy.allow_host_path_mounts:
        raise PermissionError("privileged execution and hostPath mounts are forbidden")
    if not _DIGEST_IMAGE.fullmatch(envelope.spec.image):
        raise ValueError("execution image must use an immutable @sha256 digest")
    digest = envelope.spec.image.rsplit("@", 1)[1]
    if envelope.spec.image_tag and envelope.spec.image_tag != digest:
        raise ValueError("image_tag must match the immutable image digest")
    name = f"av-code-{envelope.attempt_id[:20].lower()}"
    limits = envelope.policy.resource_limits
    encoded = base64.b64encode(
        json.dumps(envelope.to_dict(), separators=(",", ":")).encode()
    ).decode()
    labels = {"app": "agentverse-code", "agentverse-workload": name}
    job: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": limits.wall_clock_seconds,
            "ttlSecondsAfterFinished": 60,
            "template": {
                "metadata": {
                    "labels": labels,
                    "annotations": {
                        "container.apparmor.security.beta.kubernetes.io/runner": (
                            "runtime/default"
                        )
                    },
                },
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65534,
                        "runAsGroup": 65534,
                        "fsGroup": 65534,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "runner",
                            "image": envelope.spec.image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "python", "-m", "app.execution_environment.worker_entrypoint"
                            ],
                            "env": [
                                {"name": "AGENTVERSE_EXECUTION_ENVELOPE_B64", "value": encoded}
                            ],
                            "securityContext": {
                                "runAsNonRoot": True,
                                "runAsUser": 65534,
                                "runAsGroup": 65534,
                                "readOnlyRootFilesystem": True,
                                "allowPrivilegeEscalation": False,
                                "privileged": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {
                                    "cpu": str(limits.cpu_cores),
                                    "memory": f"{limits.memory_mb}Mi",
                                    "ephemeral-storage": f"{limits.artifact_bytes}",
                                },
                            },
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace"},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "workspace",
                            "emptyDir": {"sizeLimit": str(limits.artifact_bytes)},
                        },
                        {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                    ],
                },
            },
        },
    }
    network = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "podSelector": {"matchLabels": {"agentverse-workload": name}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [],
        },
    }
    return job, network


class KubernetesRunner(BaseRunner):
    def __init__(
        self, client: KubernetesClient | None = None, *, namespace: str | None = None
    ) -> None:
        self._namespace = namespace or os.environ.get(
            "ISOLATED_EXECUTION_NAMESPACE", "agentverse-code"
        )
        if client is None:
            try:
                client = InClusterKubernetesClient(self._namespace)
            except (OSError, RuntimeError):
                client = None
        self._client = client
        self._health = KubernetesRunnerHealthCheck(client)
        self._fencing_tokens: dict[str, str] = {}

    @property
    def runner_type(self) -> str:
        return RunnerType.KUBERNETES.value

    @property
    def health_check(self) -> RunnerHealthCheck:
        return self._health

    async def cancel(self, workload_id: str, reason: str) -> CodeCancellationReceipt:
        del reason
        requested = datetime.now(UTC)
        token = self._fencing_tokens.get(workload_id, "cancel-reconcile")
        try:
            if self._client is None:
                raise RuntimeError("Kubernetes API unavailable")
            await self._client.delete_workload(workload_id, token)
            cleanup_state = "complete"
            terminated = True
        except Exception:
            cleanup_state = "quarantined"
            terminated = False
        return CodeCancellationReceipt(
            workload_id=workload_id,
            requested_at=requested,
            acknowledged_at=datetime.now(UTC),
            process_group_terminated=terminated,
            cleanup_state=cleanup_state,
        )

    async def run(
        self, request: ExecutionRequest, event_callback: Any | None = None
    ) -> ExecutionResult:
        del event_callback
        envelope = request.envelope
        started = time.monotonic()
        capsule_id = f"av-code-{envelope.attempt_id[:20].lower()}"
        if self._client is None:
            return self._failure(request, capsule_id, "Kubernetes API unavailable")
        if not verify_envelope(envelope):
            return self._failure(
                request, capsule_id, "Envelope integrity verification failed",
                ExecutionFailureReason.SANDBOX_VIOLATION,
            )
        token = uuid.uuid4().hex
        self._fencing_tokens[capsule_id] = token
        result: ExecutionResult
        try:
            job, network = build_workload_manifests(request, namespace=self._namespace)
            await self._client.create("NetworkPolicy", network)
            await self._client.create("Job", job)
            outcome = await self._client.wait_job(
                capsule_id, envelope.policy.resource_limits.wall_clock_seconds + 5
            )
            logs = await self._client.read_logs(
                capsule_id, envelope.policy.resource_limits.output_bytes
            )
            payload: dict[str, Any] = {}
            if logs:
                try:
                    payload = json.loads(logs.splitlines()[-1])
                except json.JSONDecodeError:
                    payload = {}
            success = bool(outcome.get("succeeded")) and bool(payload.get("success", True))
            result = ExecutionResult(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                attempt_id=envelope.attempt_id,
                success=success,
                status=str(payload.get("status", "complete" if success else "failed")),
                failure_reason=(
                    None
                    if success
                    else ExecutionFailureReason.TIMEOUT
                    if outcome.get("timed_out")
                    else ExecutionFailureReason.INTERNAL_ERROR
                ),
                runner_type=self.runner_type,
                capsule_id=capsule_id,
                execution_time_ms=(time.monotonic() - started) * 1000,
                exit_code=outcome.get("exit_code"),
                timeout_hit=bool(outcome.get("timed_out")),
                cleanup_status="ok",
            )
        except BaseException as exc:
            reason = (
                ExecutionFailureReason.CANCELLED
                if type(exc).__name__ == "CancelledError"
                else ExecutionFailureReason.RUNNER_UNAVAILABLE
            )
            result = self._failure(request, capsule_id, str(exc), reason)
        finally:
            try:
                await self._client.delete_workload(capsule_id, token)
            except Exception:
                result.cleanup_status = "quarantined"
                result.success = False
                result.status = "failed"
                result.failure_reason = ExecutionFailureReason.SANDBOX_VIOLATION
                result.error_message = "Kubernetes workload cleanup failed; capsule quarantined"
            self._fencing_tokens.pop(capsule_id, None)
        return result

    def _failure(
        self,
        request: ExecutionRequest,
        capsule_id: str,
        message: str,
        reason: ExecutionFailureReason = ExecutionFailureReason.RUNNER_UNAVAILABLE,
    ) -> ExecutionResult:
        envelope = request.envelope
        return ExecutionResult(
            goal_id=envelope.goal_id,
            tenant_id=envelope.tenant_id,
            attempt_id=envelope.attempt_id,
            success=False,
            status="failed",
            failure_reason=reason,
            error_message=message[:500],
            runner_type=self.runner_type,
            capsule_id=capsule_id,
        )


__all__ = [
    "InClusterKubernetesClient",
    "KubernetesClient",
    "KubernetesRunner",
    "KubernetesRunnerHealthCheck",
    "build_workload_manifests",
]
