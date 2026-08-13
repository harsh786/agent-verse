from datetime import UTC, datetime, timedelta

from app.sandbox_runtime.certification import certify_sandbox


def test_sandbox_certification_fails_closed_for_missing_or_expired_evidence() -> None:
    now = datetime.now(UTC)
    incomplete = certify_sandbox(runtime_id="k8s", runtime_version="v1", probes={}, now=now)
    assert not incomplete.certified
    complete = certify_sandbox(
        runtime_id="k8s",
        runtime_version="v1",
        probes={
            "readiness": True,
            "exact_host_network_policy": True,
            "filesystem_denial": True,
            "process_denial": True,
            "package_denial": True,
            "secret_denial": True,
            "resource_caps": True,
            "cancellation": True,
            "artifact_sanitation": True,
            "audit_correlation": True,
            "restart_safe": True,
        },
        now=now,
        ttl=timedelta(minutes=1),
    )
    assert complete.certified
    expired = complete.model_copy(update={"expires_at": now - timedelta(seconds=1)})
    assert not expired.certified
