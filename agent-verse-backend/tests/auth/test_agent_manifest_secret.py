"""Manifest signing never uses the old public default HMAC key."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app.auth.agent_manifest import (
    ManifestSigningNotConfiguredError,
    sign_manifest,
    verify_manifest,
)

_PUBLIC_DEFAULT = "agentverse-manifest-secret"


def _forge(manifest: dict[str, object]) -> dict[str, object]:
    canonical = json.dumps(manifest, sort_keys=True).encode()
    sig = hmac.new(_PUBLIC_DEFAULT.encode(), canonical, hashlib.sha256).digest()
    return {**manifest, "_signature": base64.urlsafe_b64encode(sig).decode(), "_signed": True}


@pytest.mark.parametrize("env_value", [None, _PUBLIC_DEFAULT])
def test_manifest_forged_with_public_default_does_not_verify(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    if env_value is None:
        monkeypatch.delenv("MANIFEST_SIGNING_SECRET", raising=False)
    else:
        monkeypatch.setenv("MANIFEST_SIGNING_SECRET", env_value)
    forged = _forge({"agent_id": "a1", "allowed_tools": ["*"]})
    assert verify_manifest(forged) is False
    # a manifest signed by this process still verifies
    assert verify_manifest(sign_manifest({"agent_id": "a1"})) is True


def test_production_requires_a_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("MANIFEST_SIGNING_SECRET", raising=False)
    with pytest.raises(ManifestSigningNotConfiguredError):
        sign_manifest({"agent_id": "a1"})
    monkeypatch.setenv("MANIFEST_SIGNING_SECRET", _PUBLIC_DEFAULT)
    with pytest.raises(ManifestSigningNotConfiguredError):
        sign_manifest({"agent_id": "a1"})


def test_configured_secret_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MANIFEST_SIGNING_SECRET", "a-real-operator-secret")
    signed = sign_manifest({"agent_id": "a1"})
    assert verify_manifest(dict(signed), secret="a-real-operator-secret") is True
