"""Evidence-backed production sandbox readiness certification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict


class SandboxCertificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_id: str
    runtime_version: str
    observed_at: datetime
    expires_at: datetime
    readiness: bool
    exact_host_network_policy: bool
    filesystem_denial: bool
    process_denial: bool
    package_denial: bool
    secret_denial: bool
    resource_caps: bool
    cancellation: bool
    artifact_sanitation: bool
    audit_correlation: bool
    restart_safe: bool

    @property
    def certified(self) -> bool:
        checks = self.model_dump(
            exclude={"runtime_id", "runtime_version", "observed_at", "expires_at"}
        )
        return datetime.now(UTC) < self.expires_at and all(bool(value) for value in checks.values())


def certify_sandbox(
    *,
    runtime_id: str,
    runtime_version: str,
    probes: dict[str, bool],
    ttl: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> SandboxCertificationEvidence:
    observed = now or datetime.now(UTC)
    required = {
        "readiness",
        "exact_host_network_policy",
        "filesystem_denial",
        "process_denial",
        "package_denial",
        "secret_denial",
        "resource_caps",
        "cancellation",
        "artifact_sanitation",
        "audit_correlation",
        "restart_safe",
    }
    unknown = set(probes) - required
    if unknown:
        raise ValueError(f"unknown sandbox probes: {sorted(unknown)}")
    return SandboxCertificationEvidence(
        runtime_id=runtime_id,
        runtime_version=runtime_version,
        observed_at=observed,
        expires_at=observed + ttl,
        **{name: bool(probes.get(name, False)) for name in required},
    )


__all__ = ["SandboxCertificationEvidence", "certify_sandbox"]
