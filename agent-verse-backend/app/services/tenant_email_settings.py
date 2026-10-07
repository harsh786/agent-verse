"""Per-tenant email settings: recipient allowlist + tenant-owned SMTP sender.

Owner decision a02-F036-02: (a) an optional per-tenant recipient allowlist for
the agent email tool, and (d) a tenant-owned SMTP sender as the production path
— the platform relay stays for system mail and as the fallback for tenants that
configured none.

Postgres (``tenant_email_settings``, FORCE RLS) is the source of truth; every
statement runs under the tenant's RLS context AND carries an explicit
``tenant_id = :t`` predicate. The SMTP password / API key is stored only as
vault ciphertext (the tenant's own vault key when it set one, else the platform
vault — the same scheme as tenant LLM keys) and is never returned by any view.

Without a DB (tests, no infrastructure) the settings live in this process; a
write is refused outside development, so a replica never holds a security
setting the others do not see.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

SMTP_TLS_MODES = ("starttls", "tls", "none")
SECRET_MASK = "********"


class EmailSettingsUnavailableError(RuntimeError):
    """The settings could not be read or written (never a fake empty answer)."""


@dataclass(frozen=True)
class TenantSMTPSettings:
    host: str
    port: int
    tls_mode: str
    username: str
    from_address: str
    secret_enc: str | None = None  # vault ciphertext; never leaves the backend
    vault_key_fingerprint: str | None = None

    def public_view(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "tls_mode": self.tls_mode,
            "username": self.username,
            "from_address": self.from_address,
            "secret_set": bool(self.secret_enc),
            "secret_masked": SECRET_MASK if self.secret_enc else None,
        }


@dataclass(frozen=True)
class TenantEmailSettings:
    tenant_id: str
    recipient_allowlist: list[str] = field(default_factory=list)
    smtp: TenantSMTPSettings | None = None
    updated_at: str | None = None
    updated_by: str | None = None

    def public_view(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "recipient_allowlist": list(self.recipient_allowlist),
            "smtp": self.smtp.public_view() if self.smtp else None,
            # Which relay the agent email tool uses for this tenant.
            "relay": "tenant" if self.smtp else "platform",
            "updated_at": self.updated_at,
        }


def _durable_required() -> bool:
    from app.governance.audit import _durable_audit_required

    return _durable_audit_required()


_SELECT = (
    "SELECT recipient_allowlist, smtp_host, smtp_port, smtp_tls_mode, smtp_username, "
    "smtp_from_address, smtp_secret_enc, vault_key_fingerprint, updated_at, updated_by "
    "FROM tenant_email_settings WHERE tenant_id = :t"
)


class TenantEmailSettingsStore:
    """Reads and writes ``tenant_email_settings`` (or a process-local dict)."""

    def __init__(self, db: Any = None, memory: dict[str, TenantEmailSettings] | None = None):
        self._db = db
        self._memory: dict[str, TenantEmailSettings] = memory if memory is not None else {}

    @property
    def db_factory(self) -> Any:
        return self._db

    async def get(self, tenant_id: str) -> TenantEmailSettings:
        """The tenant's settings (defaults when none were saved). Raises
        :class:`EmailSettingsUnavailableError` when the read fails — a caller
        enforcing the allowlist must refuse rather than send unchecked."""
        if self._db is None:
            return self._memory.get(tenant_id) or TenantEmailSettings(tenant_id=tenant_id)
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (await session.execute(text(_SELECT), {"t": tenant_id})).fetchone()
        except Exception as exc:
            raise EmailSettingsUnavailableError("email settings unavailable") from exc
        if row is None:
            return TenantEmailSettings(tenant_id=tenant_id)
        allowlist = row[0]
        if isinstance(allowlist, str):
            allowlist = json.loads(allowlist)
        smtp = None
        if row[1]:
            smtp = TenantSMTPSettings(
                host=str(row[1]),
                port=int(row[2]),
                tls_mode=str(row[3]),
                username=str(row[4] or ""),
                from_address=str(row[5]),
                secret_enc=row[6],
                vault_key_fingerprint=row[7],
            )
        return TenantEmailSettings(
            tenant_id=tenant_id,
            recipient_allowlist=[str(e) for e in (allowlist or [])],
            smtp=smtp,
            updated_at=row[8].isoformat() if row[8] is not None else None,
            updated_by=row[9],
        )

    async def set_allowlist(
        self, tenant_id: str, allowlist: list[str], *, updated_by: str | None
    ) -> TenantEmailSettings:
        """Replace the allowlist (entries already normalized by the caller)."""
        if self._db is None:
            self._require_memory_writes()
            current = self._memory.get(tenant_id) or TenantEmailSettings(tenant_id=tenant_id)
            self._memory[tenant_id] = replace(
                current, recipient_allowlist=list(allowlist), updated_by=updated_by
            )
            return self._memory[tenant_id]
        await self._write(
            tenant_id,
            "INSERT INTO tenant_email_settings (tenant_id, recipient_allowlist, updated_by) "
            "VALUES (:t, CAST(:a AS jsonb), :u) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "recipient_allowlist = EXCLUDED.recipient_allowlist, "
            "updated_by = EXCLUDED.updated_by, updated_at = now() "
            "WHERE tenant_email_settings.tenant_id = :t",
            {"t": tenant_id, "a": json.dumps(list(allowlist)), "u": updated_by},
        )
        return await self.get(tenant_id)

    async def set_smtp(
        self, tenant_id: str, smtp: TenantSMTPSettings, *, updated_by: str | None
    ) -> TenantEmailSettings:
        if smtp.tls_mode not in SMTP_TLS_MODES:
            raise ValueError(f"tls_mode must be one of {SMTP_TLS_MODES}")
        if self._db is None:
            self._require_memory_writes()
            current = self._memory.get(tenant_id) or TenantEmailSettings(tenant_id=tenant_id)
            self._memory[tenant_id] = replace(current, smtp=smtp, updated_by=updated_by)
            return self._memory[tenant_id]
        await self._write(
            tenant_id,
            "INSERT INTO tenant_email_settings (tenant_id, smtp_host, smtp_port, smtp_tls_mode, "
            "smtp_username, smtp_from_address, smtp_secret_enc, vault_key_fingerprint, "
            "updated_by) VALUES (:t, :h, :p, :m, :u_name, :f, :s, :fp, :u) "
            "ON CONFLICT (tenant_id) DO UPDATE SET smtp_host = EXCLUDED.smtp_host, "
            "smtp_port = EXCLUDED.smtp_port, smtp_tls_mode = EXCLUDED.smtp_tls_mode, "
            "smtp_username = EXCLUDED.smtp_username, "
            "smtp_from_address = EXCLUDED.smtp_from_address, "
            "smtp_secret_enc = EXCLUDED.smtp_secret_enc, "
            "vault_key_fingerprint = EXCLUDED.vault_key_fingerprint, "
            "updated_by = EXCLUDED.updated_by, updated_at = now() "
            "WHERE tenant_email_settings.tenant_id = :t",
            {
                "t": tenant_id,
                "h": smtp.host,
                "p": smtp.port,
                "m": smtp.tls_mode,
                "u_name": smtp.username,
                "f": smtp.from_address,
                "s": smtp.secret_enc,
                "fp": smtp.vault_key_fingerprint,
                "u": updated_by,
            },
        )
        return await self.get(tenant_id)

    async def clear_smtp(self, tenant_id: str, *, updated_by: str | None) -> TenantEmailSettings:
        """Remove the tenant SMTP sender (the agent email tool falls back to the
        platform relay). The ciphertext is deleted, not kept."""
        if self._db is None:
            self._require_memory_writes()
            current = self._memory.get(tenant_id) or TenantEmailSettings(tenant_id=tenant_id)
            self._memory[tenant_id] = replace(current, smtp=None, updated_by=updated_by)
            return self._memory[tenant_id]
        await self._write(
            tenant_id,
            "UPDATE tenant_email_settings SET smtp_host = NULL, smtp_port = NULL, "
            "smtp_tls_mode = NULL, smtp_username = NULL, smtp_from_address = NULL, "
            "smtp_secret_enc = NULL, vault_key_fingerprint = NULL, updated_by = :u, "
            "updated_at = now() WHERE tenant_id = :t",
            {"t": tenant_id, "u": updated_by},
        )
        return await self.get(tenant_id)

    # ── internals ────────────────────────────────────────────────────────────

    def _require_memory_writes(self) -> None:
        if _durable_required():
            raise EmailSettingsUnavailableError(
                "email settings need the database outside development; nothing was saved"
            )

    async def _write(self, tenant_id: str, sql: str, params: dict[str, Any]) -> None:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(text(sql), params)
        except Exception as exc:
            raise EmailSettingsUnavailableError(
                "email settings could not be saved; nothing was changed"
            ) from exc


def email_settings_for(state: Any) -> TenantEmailSettingsStore:
    """The store over this app's DB (or its process-local settings without one)."""
    memory = getattr(state, "tenant_email_settings_memory", None)
    if memory is None:
        memory = {}
        state.tenant_email_settings_memory = memory
    return TenantEmailSettingsStore(db=getattr(state, "db_session_factory", None), memory=memory)


async def seal_smtp_secret(db_factory: Any, tenant_id: str, secret: str) -> tuple[str, str]:
    """(ciphertext, platform vault fingerprint) for a tenant SMTP secret — the
    tenant's own vault key when it set one (``tv1:``), else the platform vault."""
    from app.providers.tenant_vault import encrypt_tenant_secret
    from app.providers.vault import get_vault

    ciphertext = await encrypt_tenant_secret(db_factory, tenant_id, secret)
    return ciphertext, get_vault().fingerprint()


async def open_smtp_secret(db_factory: Any, tenant_id: str, smtp: TenantSMTPSettings) -> str | None:
    """The plaintext SMTP secret (``None`` when the sender has no credentials).
    Raises when the ciphertext cannot be opened (the caller refuses to send)."""
    if not smtp.secret_enc:
        return None
    from app.providers.tenant_vault import decrypt_tenant_secret

    return await decrypt_tenant_secret(db_factory, tenant_id, smtp.secret_enc)
