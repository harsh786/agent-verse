"""Workflow webhook HMAC secrets: encrypted at rest, redacted in responses (B2-OPEN-1).

A workflow's ``trigger.webhook.hmac_secret`` (``auth: hmac``, B2-9) used to sit
in the workflow definition JSON in clear: in ``workflows.definition``, its
run-engine mirror ``workflow_definitions.definition_json``, every published
version snapshot, and every workflow API response.

Policy (same envelope as ingestion source credentials, ``app.ingestion.source_secrets``):

* **At rest** the secret is ``enc:v1:<fernet>`` under the platform vault, or
  ``enc:v1:tv1:<fernet>`` under the tenant's own vault key when it has one
  (TENANT-ENVELOPE-ALL). The store seals it on every create / update, so the
  mirror row and version snapshots (copied from the stored definition) carry the
  ciphertext too. ``agentverse vault-rotate`` and ``tenant-key-compact`` re-seal
  these columns (``app.providers.vault_rotation`` / ``tenant_key_compaction``).
* **In responses** a non-empty secret is replaced by :data:`MASK`.
* **On update** a secret equal to :data:`MASK` means "unchanged": the stored
  value at the same trigger position is kept, so a client can round-trip the
  masked definition it was given.
* **A mask with nothing behind it is refused** (B2-GAP-3): a create (e.g. a
  re-import of an exported YAML) or update whose :data:`MASK` cannot be resolved
  to a stored secret raises :class:`MaskedSecretError` (422) instead of storing
  an empty secret, which would silently refuse every delivery.
* **Legacy plaintext** definitions keep working: verification uses a plaintext
  value as-is, and the store re-seals such a definition the first time it reads
  it (read-through migration, no offline step; the vault key never has to be
  available to Alembic).
* **Verification** opens the ciphertext at signature-check time only; a value
  that cannot be opened fails closed (the delivery is refused, never accepted
  with a blank / garbage secret).
"""

from __future__ import annotations

import copy
from typing import Any

ENC_PREFIX = "enc:v1:"
MASK = "********"
SECRET_FIELD = "hmac_secret"

__all__ = [
    "ENC_PREFIX",
    "MASK",
    "SECRET_FIELD",
    "MaskedSecretError",
    "WebhookSecretError",
    "has_masked_secret",
    "has_plaintext_secret",
    "is_sealed",
    "merge_masked_secrets",
    "needs_tenant_vault",
    "open_secret",
    "redact_definition",
    "redact_workflow",
    "seal_plaintext_secrets",
]


class WebhookSecretError(RuntimeError):
    """A stored webhook secret could not be opened (wrong / missing vault key)."""


MASKED_SECRET_DETAIL = (
    "trigger.webhook.hmac_secret is the masked placeholder '********' (exports and "
    "API responses never contain the secret) and there is no stored secret to keep. "
    "Set hmac_secret to a new secret (and configure the sender to sign with it), or "
    "remove 'auth: hmac', then try again."
)


class MaskedSecretError(ValueError):
    """A :data:`MASK` secret has no stored secret behind it (B2-GAP-3)."""

    def __init__(self, detail: str = MASKED_SECRET_DETAIL) -> None:
        super().__init__(detail)


Slot = tuple[Any, ...]


def _trigger_slots(base: Slot, trigger: dict[str, Any]) -> list[tuple[Slot, dict[str, Any]]]:
    out: list[tuple[Slot, dict[str, Any]]] = []
    if SECRET_FIELD in trigger:
        out.append(((*base, "flat"), trigger))
    nested = trigger.get("webhook")
    if isinstance(nested, dict) and SECRET_FIELD in nested:
        out.append(((*base, "webhook"), nested))
    return out


def _slots(definition: Any) -> list[tuple[Slot, dict[str, Any]]]:
    """Every (position, config dict) holding an ``hmac_secret`` — both trigger shapes
    (builder ``triggers`` list, DSL ``trigger`` object; nested ``webhook`` or flat)."""
    if not isinstance(definition, dict):
        return []
    out: list[tuple[Slot, dict[str, Any]]] = []
    plural = definition.get("triggers")
    if isinstance(plural, list):
        for i, trigger in enumerate(plural):
            if isinstance(trigger, dict):
                out.extend(_trigger_slots(("triggers", i), trigger))
    single = definition.get("trigger")
    if isinstance(single, dict):
        out.extend(_trigger_slots(("trigger",), single))
    return out


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def _is_plaintext(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value != MASK and not is_sealed(value)


def has_plaintext_secret(definition: Any) -> bool:
    """True when a secret is stored in clear (needs sealing)."""
    return any(_is_plaintext(cfg.get(SECRET_FIELD)) for _slot, cfg in _slots(definition))


def has_masked_secret(definition: Any) -> bool:
    return any(cfg.get(SECRET_FIELD) == MASK for _slot, cfg in _slots(definition))


def merge_masked_secrets(
    definition: dict[str, Any], existing: Any, *, strict: bool = False
) -> dict[str, Any]:
    """A copy of ``definition`` with every :data:`MASK` secret replaced by the stored
    value at the same trigger position (or the only stored secret, when there is
    exactly one). Nothing to keep → ``""`` (an ``auth: hmac`` webhook without a
    secret then fails closed), or :class:`MaskedSecretError` when ``strict``."""
    out = copy.deepcopy(definition)
    stored = {slot: cfg.get(SECRET_FIELD) for slot, cfg in _slots(existing)}
    stored = {k: v for k, v in stored.items() if isinstance(v, str) and v and v != MASK}
    only = next(iter(stored.values())) if len(stored) == 1 else ""
    for slot, cfg in _slots(out):
        if cfg.get(SECRET_FIELD) == MASK:
            cfg[SECRET_FIELD] = stored.get(slot, only)
            if strict and not cfg[SECRET_FIELD]:
                raise MaskedSecretError
    return out


def seal_plaintext_secrets(definition: dict[str, Any], tenant_vault: Any = None) -> dict[str, Any]:
    """A copy of ``definition`` with every plaintext secret vault-encrypted
    (idempotent: sealed values are left alone)."""
    from app.providers.tenant_vault import seal_for_tenant

    out = copy.deepcopy(definition)
    for _slot, cfg in _slots(out):
        value = cfg.get(SECRET_FIELD)
        if _is_plaintext(value):
            cfg[SECRET_FIELD] = ENC_PREFIX + seal_for_tenant(tenant_vault, str(value))
    return out


def open_secret(value: object, tenant_vault: Any = None) -> str:
    """The secret in clear: opens ``enc:v1:`` (platform or ``tv1:`` tenant key),
    passes a legacy plaintext value through. Raises :class:`WebhookSecretError`."""
    if not isinstance(value, str) or not value or value == MASK:
        return ""
    if not is_sealed(value):
        return value  # legacy plaintext (re-sealed by the store on its next read)
    from app.providers.tenant_vault import open_for_tenant

    try:
        return open_for_tenant(tenant_vault, value[len(ENC_PREFIX) :])
    except Exception as exc:
        raise WebhookSecretError(
            f"workflow webhook secret cannot be decrypted ({type(exc).__name__})"
        ) from exc


def needs_tenant_vault(value: object) -> bool:
    from app.providers.tenant_vault import TENANT_CIPHER_PREFIX

    return is_sealed(value) and str(value)[len(ENC_PREFIX) :].startswith(TENANT_CIPHER_PREFIX)


def redact_definition(definition: Any) -> Any:
    """A copy with every non-empty secret replaced by :data:`MASK` (API responses)."""
    if not _slots(definition):
        return definition
    out = copy.deepcopy(definition)
    for _slot, cfg in _slots(out):
        if cfg.get(SECRET_FIELD):
            cfg[SECRET_FIELD] = MASK
    return out


def redact_workflow(item: Any) -> Any:
    """A workflow / version dict with its ``definition`` redacted (shallow copy)."""
    if not isinstance(item, dict) or not isinstance(item.get("definition"), dict):
        return item
    return {**item, "definition": redact_definition(item["definition"])}
