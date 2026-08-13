from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.execution_environment.artifacts import (
    DurableExecutionArtifactStore,
    make_artifact,
    validate_artifact_name,
)


@dataclass
class Stored:
    artifact_id: str = "artifact"
    uri: str = "s3://bucket/key"


class Backend:
    def __init__(self) -> None:
        self.names: list[str] = []

    async def write_bytes(self, *, goal_id: str, name: str, content: bytes) -> Stored:
        assert goal_id == "goal" and content == b"payload"
        self.names.append(name)
        return Stored()


async def _content():
    yield b"pay"
    yield b"load"


@pytest.mark.asyncio
async def test_durable_store_scopes_key_and_verifies_checksum() -> None:
    backend = Backend()
    artifact = await DurableExecutionArtifactStore(backend).put(
        tenant_id="tenant",
        goal_id="goal",
        workload_id="workload",
        name="results/data.json",
        content=_content(),
        maximum_bytes=100,
    )
    assert backend.names == ["tenant/workload/results/data.json"]
    assert artifact.storage_url == "s3://bucket/key"
    assert len(artifact.checksum_sha256) == 64


@pytest.mark.parametrize("name", ("/etc/passwd", "../secret", ".runtime", "a/../../b"))
def test_artifact_names_reject_escape_and_hidden_runtime(name: str) -> None:
    with pytest.raises(ValueError):
        validate_artifact_name(name)


def test_legacy_artifact_constructor_requires_durable_reference() -> None:
    with pytest.raises(ValueError, match="durable storage_url"):
        make_artifact(goal_id="goal", tenant_id="tenant", name="x.txt", content=b"x")
