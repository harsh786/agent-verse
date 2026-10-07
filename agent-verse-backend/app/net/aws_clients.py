"""Tenant AWS / S3-compatible clients: the tenant's own keys or anonymous, never the platform's.

botocore resolves credentials that are not passed explicitly through its default
chain: environment variables, the ``~/.aws`` files, the container credentials
endpoint and finally the EC2 instance metadata service (IMDS, 169.254.169.254).
All of that is the *platform pod's* identity. A tenant client built with
``aws_access_key_id=None`` (credentials missing, or blanked because they could not
be decrypted) silently fell back to it: on a cluster it would sign the tenant's
requests with the node's IAM role, and behind the connector SSRF guard the sync
failed with ``metadata service hostname '169.254.169.254' blocked``.

Every client built here comes from an isolated botocore session:

* its credential resolver has **no providers**: explicit keys, or nothing;
* without keys the client is explicitly **UNSIGNED** (anonymous), never the chain;
* nothing ambient shapes it: no profile, no shared config / credentials files, no
  ``AWS_ENDPOINT_URL*`` redirection, and ``defaults_mode`` is pinned to ``legacy``
  (``auto`` looks the region up in IMDS);
* the region is always explicit (:data:`DEFAULT_REGION` when none is configured).

So no code path of a tenant client can reach IMDS, whatever the process
environment says (``AWS_EC2_METADATA_DISABLED=true`` in the chart is only a second
line of defence).
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from typing import Any

DEFAULT_REGION = "us-east-1"

# The credential fields an S3 / AWS source may carry (nested under
# ``credentials`` — what the UI sends — or at the top level of the config).
_KEY_FIELDS = ("access_key_id", "secret_access_key", "session_token")
_MASK = "********"  # app.ingestion.source_secrets.MASK (kept import-free here)

# Config variables pinned on every isolated session (logical name -> value), so the
# pod's environment and files never reach a tenant client.
_PINNED_CONFIG: dict[str, Any] = {
    "profile": None,
    "config_file": os.devnull,
    "credentials_file": os.devnull,
    "defaults_mode": "legacy",
    "ignore_configured_endpoint_urls": True,
    "metadata_service_num_attempts": 0,
}


class AwsCredentialError(ValueError):
    """The configured AWS credentials cannot be used (incomplete, placeholder)."""


@dataclasses.dataclass(frozen=True)
class AwsKeys:
    """Explicit static AWS credentials (an STS session token is optional)."""

    access_key_id: str
    secret_access_key: str
    session_token: str | None = None

    def __repr__(self) -> str:  # never log a secret
        return f"AwsKeys(access_key_id={self.access_key_id!r}, secret_access_key='***')"

    def client_kwargs(self) -> dict[str, str]:
        kwargs = {
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
        }
        if self.session_token:
            kwargs["aws_session_token"] = self.session_token
        return kwargs


def resolve_region(value: object) -> str:
    """The configured region, or :data:`DEFAULT_REGION` (never None / "")."""
    text = str(value or "").strip()
    return text or DEFAULT_REGION


def keys_from_config(connection_config: Mapping[str, Any]) -> AwsKeys | None:
    """The source's AWS keys, or None for an anonymous source.

    Reads ``connection_config.credentials`` (the UI's shape), falling back to
    top-level ``access_key_id`` / ``secret_access_key`` / ``session_token``.
    Raises :class:`AwsCredentialError` for half a key pair or for the response
    mask (a client that saved back the masked config it was shown) — such a
    source must never silently run anonymous.
    """
    raw = connection_config.get("credentials")
    if raw == _MASK:
        raise AwsCredentialError(
            "the stored credentials are the response mask, not real keys; "
            "re-enter the access key and secret"
        )
    if raw not in (None, "", {}) and not isinstance(raw, Mapping):
        raise AwsCredentialError(
            "connection_config.credentials must be an object with access_key_id "
            "and secret_access_key"
        )
    credentials: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    if not credentials.get("access_key_id") and connection_config.get("access_key_id"):
        credentials = {k: connection_config.get(k) for k in _KEY_FIELDS}
    key_id = str(credentials.get("access_key_id") or "").strip()
    secret = credentials.get("secret_access_key")
    secret_text = "" if secret is None else str(secret)
    if secret_text == _MASK:
        raise AwsCredentialError(
            "the stored secret_access_key is the response mask, not a real key; re-enter it"
        )
    if not key_id and not secret_text:
        return None
    if not key_id or not secret_text:
        missing = "access_key_id" if not key_id else "secret_access_key"
        raise AwsCredentialError(f"incomplete AWS credentials: {missing} is missing")
    token = credentials.get("session_token")
    return AwsKeys(key_id, secret_text, str(token) if token else None)


def isolated_botocore_session() -> Any:
    """A botocore session that can only sign with keys passed to ``create_client``.

    Its credential resolver has no providers (no env / files / container / IMDS
    lookup) and the ambient profile, config files and endpoint overrides are
    pinned away (see :data:`_PINNED_CONFIG`).
    """
    import botocore.session  # type: ignore[import-not-found]
    from botocore.configprovider import ConstantProvider  # type: ignore[import-not-found]
    from botocore.credentials import CredentialResolver  # type: ignore[import-not-found]

    session = botocore.session.Session()
    store = session.get_component("config_store")
    for name, value in _PINNED_CONFIG.items():
        store.set_config_provider(name, ConstantProvider(value))
    session.register_component("credential_provider", CredentialResolver(providers=[]))
    return session


def tenant_client(
    boto3: Any,
    service: str,
    *,
    region: object,
    keys: AwsKeys | None,
    endpoint_url: str | None = None,
    config: Any = None,
) -> Any:
    """A boto3 client for a tenant: signed with ``keys``, or UNSIGNED without them.

    ``boto3`` is the imported module (callers import it lazily so a missing SDK
    is reported, and tests can patch ``boto3.Session``). ``config`` (a botocore
    ``Config``) is merged over the hardened defaults.
    """
    from botocore import UNSIGNED  # type: ignore[import-not-found]
    from botocore.config import Config  # type: ignore[import-not-found]

    merged = Config(ignore_configured_endpoint_urls=True)
    if config is not None:
        merged = merged.merge(config)
    region_name = resolve_region(region)
    kwargs: dict[str, Any] = {"region_name": region_name}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    if keys is None:
        merged = merged.merge(Config(signature_version=UNSIGNED))
    else:
        kwargs.update(keys.client_kwargs())
    kwargs["config"] = merged
    session = boto3.Session(botocore_session=isolated_botocore_session(), region_name=region_name)
    return session.client(service, **kwargs)


__all__ = [
    "DEFAULT_REGION",
    "AwsCredentialError",
    "AwsKeys",
    "isolated_botocore_session",
    "keys_from_config",
    "resolve_region",
    "tenant_client",
]
