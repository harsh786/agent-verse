"""Vault key canary: prove at startup that this process holds the fleet's key (BYOK-2).

The API encrypts tenant secrets (BYOK LLM keys, connector credentials) and the
Celery workers decrypt them. A worker deployed with another (or no)
VAULT_MASTER_KEY used to start normally and fail every BYOK goal with an opaque
"could not be decrypted". Now:

* the API writes a canary — a known plaintext encrypted with its vault key, plus
  that key's non-secret fingerprint — to ``vault_key_canary`` (one row) at
  startup, and its ``/health/ready`` re-verifies it (``vault_key`` check);
* a worker opens the canary at startup (``run_vault_self_check``): when its key
  cannot, it logs both fingerprints and refuses to start outside development.

The first API to start establishes the canary; an API whose key cannot open the
existing one is unready rather than overwriting it, so one misconfigured pod
never flips the fleet. After a master-key rotation (new key current, old key in
VAULT_PREVIOUS_MASTER_KEYS) the API re-seals the canary under the new key. A
stale canary written by a wrong key can be reset with
``DELETE FROM vault_key_canary`` (the next API start re-creates it).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.observability.health import HealthCheck

logger = logging.getLogger(__name__)

CANARY_ID = "platform"
CANARY_PLAINTEXT = "agentverse-vault-canary-v1"

CanaryStatus = Literal["ok", "mismatch", "missing", "unavailable", "no_key"]


@dataclass(frozen=True)
class VaultCanaryResult:
    status: CanaryStatus
    local_fingerprint: str | None
    canary_fingerprint: str | None
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


_LAST_RESULT: VaultCanaryResult | None = None


def last_canary_result() -> VaultCanaryResult | None:
    """The most recent canary verdict of this process (None before any check)."""
    return _LAST_RESULT


def _set_last_canary_result(result: VaultCanaryResult | None) -> None:
    global _LAST_RESULT
    _LAST_RESULT = result


def reset_last_canary_result() -> None:
    _set_last_canary_result(None)


def _mismatch(local_fps: tuple[str, ...], canary_fp: str, role: str) -> VaultCanaryResult:
    here = local_fps[0]
    extra = f" (previous: {', '.join(local_fps[1:])})" if len(local_fps) > 1 else ""
    return VaultCanaryResult(
        "mismatch",
        here,
        canary_fp,
        f"vault key mismatch: the vault canary was written with vault key fingerprint "
        f"{canary_fp}, but this {role} process has {here}{extra} and cannot open it. Set "
        "the same VAULT_MASTER_KEY on the API and every worker and beat process (or, if "
        "the canary itself is stale, DELETE FROM vault_key_canary and restart the API).",
    )


async def _read(session: Any) -> tuple[str, str] | None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT fingerprint, ciphertext FROM vault_key_canary WHERE id = :id"),
            {"id": CANARY_ID},
        )
    ).fetchone()
    return (str(row[0]), str(row[1])) if row is not None else None


def _opens(vault: Any, ciphertext: str) -> bool:
    try:
        return bool(vault.decrypt(ciphertext) == CANARY_PLAINTEXT)
    except Exception:
        return False


def _local_vault(role: str) -> tuple[Any, VaultCanaryResult | None]:
    from app.providers.vault import get_vault

    try:
        return get_vault(), None
    except Exception as exc:
        return None, VaultCanaryResult(
            "no_key", None, None, f"this {role} process has no usable vault master key: {exc}"
        )


async def check_vault_canary(db_factory: Any, role: str = "worker") -> VaultCanaryResult:
    """Open the fleet's canary with this process's key (read-only)."""
    vault, err = _local_vault(role)
    if err is not None:
        _set_last_canary_result(err)
        return err
    fps = vault.fingerprints()
    try:
        async with db_factory() as session:
            row = await _read(session)
    except Exception as exc:
        result = VaultCanaryResult(
            "unavailable", fps[0], None, f"vault canary could not be read ({type(exc).__name__})"
        )
        _set_last_canary_result(result)
        return result
    if row is None:
        result = VaultCanaryResult(
            "missing", fps[0], None, "no vault canary yet (the API writes it at startup)"
        )
    elif _opens(vault, row[1]):
        result = VaultCanaryResult("ok", fps[0], row[0], "vault key matches the canary")
    else:
        result = _mismatch(fps, row[0], role)
    _set_last_canary_result(result)
    return result


async def publish_vault_canary(db_factory: Any, role: str = "api") -> VaultCanaryResult:
    """Create the canary if absent, verify it otherwise (re-seal after a rotation)."""
    from sqlalchemy import text

    vault, err = _local_vault(role)
    if err is not None:
        _set_last_canary_result(err)
        return err
    fps = vault.fingerprints()
    try:
        async with db_factory() as session, session.begin():
            row = await _read(session)
            if row is None:
                await session.execute(
                    text(
                        "INSERT INTO vault_key_canary (id, fingerprint, ciphertext, written_by) "
                        "VALUES (:id, :fp, :ct, :by) ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": CANARY_ID,
                        "fp": fps[0],
                        "ct": vault.encrypt(CANARY_PLAINTEXT),
                        "by": role,
                    },
                )
                row = await _read(session)
            if row is None:  # pragma: no cover - the insert above guarantees a row
                raise RuntimeError("vault canary row missing after insert")
            if not _opens(vault, row[1]):
                result = _mismatch(fps, row[0], role)
            else:
                if row[0] != fps[0]:
                    # Opens only with a previous key: a rotation is in progress and
                    # this process holds the new key — re-seal under it.
                    await session.execute(
                        text(
                            "UPDATE vault_key_canary SET fingerprint = :fp, ciphertext = :ct, "
                            "written_by = :by, updated_at = NOW() WHERE id = :id"
                        ),
                        {
                            "id": CANARY_ID,
                            "fp": fps[0],
                            "ct": vault.encrypt(CANARY_PLAINTEXT),
                            "by": role,
                        },
                    )
                result = VaultCanaryResult("ok", fps[0], fps[0], "vault key matches the canary")
    except Exception as exc:
        result = VaultCanaryResult(
            "unavailable",
            fps[0],
            None,
            f"vault canary could not be written/read ({type(exc).__name__})",
        )
    _set_last_canary_result(result)
    return result


def run_vault_self_check(role: str) -> VaultCanaryResult:
    """Sync startup check for a Celery process: open the API's canary."""
    from app.db.session import get_session_factory, run_in_fresh_loop

    try:
        factory = get_session_factory()
    except Exception as exc:
        result = VaultCanaryResult(
            "unavailable", None, None, f"no database to read the vault canary ({exc})"
        )
        _set_last_canary_result(result)
        return result
    result: VaultCanaryResult = run_in_fresh_loop(check_vault_canary(factory, role=role))
    return result


def vault_key_health_check(db_factory: Any) -> HealthCheck:
    """``vault_key`` readiness check for the API: down while its key cannot open the canary."""

    async def _check() -> None:
        result = await publish_vault_canary(db_factory, role="api")
        if not result.ok:
            raise RuntimeError(result.message)

    return HealthCheck(name="vault_key", check=_check)


_STILL_SAME_KEY_HINT = "Re-entering the credential does not help until the keys match."


async def explain_undecryptable_secret(db_factory: Any, exc: BaseException) -> str:
    """Why a stored secret does not open in THIS process (fingerprints only, never a key).

    Used where a decrypt failure used to read "re-enter the credential": when this
    process holds another ``VAULT_MASTER_KEY`` than the API (the fleet's canary
    does not open here) re-entering cannot help, and the message says so and
    names the setting. When the canary does open here the value was sealed by a
    process with another key, and re-entering it is the fix.
    """
    from app.providers.tenant_vault import TenantVaultError, TenantVaultUnwrapError
    from app.providers.vault import process_role

    role = process_role()
    if isinstance(exc, TenantVaultError) and not isinstance(exc, TenantVaultUnwrapError):
        # The tenant's OWN vault key (BYOK) is missing or no longer opens the
        # value; the platform key is not involved.
        return (
            f"the tenant's vault key cannot open it ({exc}). Re-enter the connector's "
            "credentials, or restore the tenant vault key it was sealed with."
        )
    if db_factory is None:
        result = last_canary_result()
    else:
        try:
            result = await check_vault_canary(db_factory, role=role)
        except Exception:  # the explanation must never mask the failure
            result = last_canary_result()
    if result is not None and result.status in ("mismatch", "no_key"):
        return f"{result.message} {_STILL_SAME_KEY_HINT}"
    if result is not None and result.ok:
        return (
            f"this {role} process's vault key (fingerprint {result.local_fingerprint}) "
            "matches the API's vault canary, so the value was sealed by a process using "
            "another VAULT_MASTER_KEY. Re-enter the connector's credentials, and make sure "
            "every API, worker and beat process sets the same VAULT_MASTER_KEY."
        )
    local = (
        f" (fingerprint {result.local_fingerprint})"
        if result is not None and result.local_fingerprint
        else ""
    )
    unverified = f" ({result.message})" if result is not None else ""
    return (
        f"this {role} process's vault key{local} cannot open it and the vault canary "
        f"could not be checked{unverified}. If the API runs with another VAULT_MASTER_KEY, "
        "set the same value on every API, worker and beat process; otherwise re-enter "
        "the connector's credentials."
    )
