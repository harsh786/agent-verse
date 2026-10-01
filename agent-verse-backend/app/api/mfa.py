"""MFA (Multi-Factor Authentication) API — TOTP-based 2FA.

Endpoints:
    GET  /auth/mfa/status             — check if MFA is enabled
    POST /auth/mfa/enroll             — begin enrollment (returns QR URI + secret)
    POST /auth/mfa/verify-enrollment  — complete enrollment (verify first TOTP code)
    POST /auth/mfa/verify             — verify TOTP during login
    POST /auth/mfa/disable            — disable MFA (requires current TOTP code)
    GET  /auth/mfa/recovery-codes     — get recovery code count
    POST /auth/mfa/regenerate         — regenerate recovery codes

Storage:
    MFA state is persisted in the ``tenant_mfa`` table (see app/db/models/mfa.py).
    The TOTP secret is encrypted at rest with Fernet (app/api/mfa_crypto.py).
    An in-memory cache sits in front of the DB for low-latency reads.
    ``pending_secret`` (set during enrollment, cleared on verify) is never
    written to the DB: with Redis wired it is kept there (Fernet-encrypted,
    10-minute TTL) so enrollment works across replicas, otherwise in-process.
    TOTP replay protection is global via Redis (fails closed on Redis errors).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import secrets
import string
import time
from collections import defaultdict
from typing import Any

import pyotp
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/auth/mfa", tags=["mfa"])

APP_NAME = "AgentVerse"
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 10

# ---------------------------------------------------------------------------
# Rate limiting & TOTP replay prevention (in-process, per tenant)
# ---------------------------------------------------------------------------

_rate_limits: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
_RATE_LIMIT_WINDOW = 60.0  # seconds
_RATE_LIMITS = {
    "/auth/mfa/verify": 10,
    "/auth/mfa/verify-enrollment": 5,
    "/auth/mfa/disable": 5,
    "/auth/mfa/regenerate": 5,
    "/auth/mfa/enroll": 3,
}


def _check_rate_limit(tenant_id: str, endpoint: str) -> None:
    """Raise HTTPException(429) if rate limit exceeded."""
    now = time.monotonic()
    limit = _RATE_LIMITS.get(endpoint, 20)
    _rate_limits[tenant_id][endpoint] = [
        t for t in _rate_limits[tenant_id][endpoint] if now - t < _RATE_LIMIT_WINDOW
    ]
    bucket = _rate_limits[tenant_id][endpoint]
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {_RATE_LIMIT_WINDOW:.0f} seconds.",
            headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW))},
        )
    bucket.append(now)


async def _check_rate_limit_global(tenant_id: str, endpoint: str, request: Any = None) -> None:
    """Rate limit with Redis when available, falls back to in-process."""
    limit = _RATE_LIMITS.get(endpoint, 20)

    # Try Redis first (global rate limiting across replicas)
    redis = None
    if request is not None:
        with contextlib.suppress(Exception):
            redis = getattr(request.app.state, "_redis", None)

    if redis is not None:
        try:
            key = f"mfa_rl:{tenant_id}:{endpoint}"
            pipe = redis.pipeline()
            await pipe.incr(key)
            await pipe.expire(key, int(_RATE_LIMIT_WINDOW))
            results = await pipe.execute()
            count = results[0]
            if count > limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many attempts. Try again in {int(_RATE_LIMIT_WINDOW)} seconds.",
                    headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW))},
                )
            return
        except HTTPException:
            raise
        except Exception:
            pass  # Fall through to in-process if Redis fails

    # Fallback: in-process rate limiting
    _check_rate_limit(tenant_id, endpoint)


# { tenant_id → set of "code:window_bucket" strings }
_used_totp_codes: dict[str, set[str]] = defaultdict(set)


def _is_totp_replayed(tenant_id: str, code: str) -> bool:
    """Return True if this exact TOTP code was already used in current window.

    The bucket is epoch-based because that is how a TOTP window is defined
    (``floor(unix_time / 30)``). This previously used ``time.monotonic()``,
    whose origin is an arbitrary per-process reference point, so the remembered
    window was offset from the window the code is actually valid for — a code
    could be forgotten while it was still valid (allowing a replay), and two
    processes bucketed the same instant differently.
    """
    now_step = int(time.time() // 30)  # TOTP time-step bucket
    # valid_window=1 accepts a code during up to three consecutive steps, so a
    # used code is remembered for that long (it used to be forgotten after the
    # next step, while still acceptable — a replay within ~60 s).
    oldest = now_step - _REPLAY_STEPS
    live = {k for k in _used_totp_codes[tenant_id] if int(k.rsplit(":", 1)[1]) >= oldest}
    _used_totp_codes[tenant_id] = live
    if any(k.rsplit(":", 1)[0] == code for k in live):
        return True
    live.add(f"{code}:{now_step}")
    return False


# A code verified with valid_window=1 is acceptable for 3 TOTP steps (90 s).
_REPLAY_STEPS = 2
_REPLAY_TTL = 30 * (_REPLAY_STEPS + 1) + 1


def _redis_of(request: Any) -> Any:
    if request is None:
        return None
    try:
        return getattr(request.app.state, "_redis", None)
    except Exception:
        return None


async def _check_totp_replay(tenant_id: str, code: str, request: Any = None) -> bool:
    """Return True if code was already used (replay detected).

    With Redis wired the check is global across replicas (``SET NX`` with a TTL
    covering the whole acceptance window) and a Redis error **fails closed**
    (:class:`MFAStateUnavailableError` → 503): falling back to the process-local
    set would let the same code be replayed on every other replica. Without
    Redis (a single process) the in-process set is authoritative.
    """
    redis = _redis_of(request)
    if redis is None:
        return _is_totp_replayed(tenant_id, code)
    key = f"mfa:used_totp:{tenant_id}:{hashlib.sha256(code.encode()).hexdigest()}"
    try:
        # Truthy when newly created; None/False when it already existed → replay.
        was_set = await redis.set(key, "1", nx=True, ex=_REPLAY_TTL)
    except Exception as exc:
        raise MFAStateUnavailableError(f"TOTP replay check unavailable: {exc}") from exc
    return not was_set


async def _reject_replayed_totp(request: Request, tenant_id: str, code: str) -> None:
    """422 on a replayed TOTP code, 503 when the replay store is unreachable."""
    try:
        replayed = await _check_totp_replay(tenant_id, code, request)
    except MFAStateUnavailableError as exc:
        raise _unavailable(exc) from exc
    if replayed:
        raise HTTPException(422, "TOTP code already used. Wait for next code.")


# ---------------------------------------------------------------------------
# Pending enrollment secret (shared across replicas via Redis)
# ---------------------------------------------------------------------------
# /enroll and /verify-enrollment are separate requests that a load balancer can
# send to different replicas; the pending secret used to live only in this
# process's cache, so confirmation failed ("No pending enrollment") elsewhere.
# With Redis wired it is stored there, Fernet-encrypted, with a TTL; a Redis
# error fails closed (503). Without Redis the process cache is used.

_PENDING_ENROLLMENT_TTL = 600  # seconds to finish enrollment


def _pending_key(tenant_id: str) -> str:
    return f"mfa:pending:{tenant_id}"


async def _set_pending_secret(request: Request, tenant_id: str, secret: str) -> None:
    redis = _redis_of(request)
    if redis is None:
        _mfa_db_store._cache_entry(tenant_id)["pending_secret"] = secret
        return
    from app.api.mfa_crypto import encrypt_secret

    try:
        await redis.set(_pending_key(tenant_id), encrypt_secret(secret), ex=_PENDING_ENROLLMENT_TTL)
    except Exception as exc:
        raise _unavailable(MFAStateUnavailableError(str(exc))) from exc


async def _get_pending_secret(request: Request, tenant_id: str) -> str | None:
    redis = _redis_of(request)
    if redis is None:
        pending = _mfa_db_store._cache_entry(tenant_id).get("pending_secret")
        return str(pending) if pending else None
    try:
        raw = await redis.get(_pending_key(tenant_id))
    except Exception as exc:
        raise _unavailable(MFAStateUnavailableError(str(exc))) from exc
    if not raw:
        return None
    from app.api.mfa_crypto import decrypt_secret

    try:
        return decrypt_secret(raw.decode() if isinstance(raw, bytes) else str(raw))
    except ValueError:
        return None  # undecryptable → treat as no pending enrollment (re-enroll)


async def _clear_pending_secret(request: Request, tenant_id: str) -> None:
    _mfa_db_store._cache_entry(tenant_id)["pending_secret"] = None
    redis = _redis_of(request)
    if redis is None:
        return
    try:
        await redis.delete(_pending_key(tenant_id))
    except Exception as exc:
        # Harmless leftover: it expires, and verify-enrollment refuses once
        # MFA is enabled. Not worth failing a completed enrollment/disable.
        with contextlib.suppress(Exception):
            from app.observability.logging import get_logger

            get_logger(__name__).warning("mfa_pending_clear_failed", error=str(exc)[:200])


# ---------------------------------------------------------------------------
# DB-backed MFA store with in-memory cache
# ---------------------------------------------------------------------------

_DEFAULT_STATE: dict[str, Any] = {
    "enabled": False,
    "secret": None,
    "pending_secret": None,
    "recovery_codes_hashed": [],
}


class MFAStateUnavailableError(Exception):
    """MFA state (or an MFA session) could not be read or written.

    Raised instead of falling back to the in-memory cache: an empty cache entry
    reads as ``enabled: False``, which the enforcement middleware treats as "no
    MFA required" — a DB blip used to switch MFA off for the tenant.
    """


class MFAStore:
    """DB-backed MFA configuration store.

    The in-memory ``_cache`` is the source of truth when no DB is wired
    (e.g. in unit tests).  When a DB session factory is provided via
    :meth:`set_db`, the DB becomes the persistent source of truth and the
    cache acts as a read-through layer.

    ``pending_secret`` is intentionally never persisted to the DB — it
    lives in the cache until enrollment is completed or the process restarts.
    """

    def __init__(self) -> None:
        # { tenant_id → state dict }  — also exposed as the module-level
        # _mfa_store for backward-compat with tests that import it directly.
        self._cache: dict[str, dict[str, Any]] = {}
        self._db: Any = None  # Async session factory; set by app lifespan.

    def set_db(self, db_factory: Any) -> None:
        """Wire the async DB session factory (called from app lifespan)."""
        self._db = db_factory

    def _cache_entry(self, tenant_id: str) -> dict[str, Any]:
        """Return the cached state for *tenant_id*, creating a default if absent."""
        return self._cache.setdefault(
            tenant_id,
            {
                "enabled": False,
                "secret": None,
                "pending_secret": None,
                "recovery_codes_hashed": [],
            },
        )

    async def get(self, tenant_id: str) -> dict[str, Any]:
        """Return MFA state, loading from DB when a factory is wired.

        ``pending_secret`` is always sourced from the in-memory cache
        because it is never written to the DB.
        """
        if self._db is None:
            return self._cache_entry(tenant_id)

        try:
            from sqlalchemy import select

            from app.api.mfa_crypto import decrypt_secret
            from app.db.models.mfa import TenantMFA
            from app.db.rls import sqlalchemy_rls_context

            # tenant_mfa is FORCE-RLS: without app.tenant_id set, a least-privilege
            # role sees no row and MFA would read as *disabled* — which the
            # enforcement middleware treats as "no MFA required". Read under the
            # tenant GUC, keeping the explicit tenant predicate as well.
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(select(TenantMFA).where(TenantMFA.tenant_id == tenant_id))
                ).scalar_one_or_none()
                # Copy out while the session is open; nothing below touches the ORM.
                found = row is not None
                enabled = bool(row.enabled) if row is not None else False
                encrypted_secret = row.encrypted_secret if row is not None else None
                recovery_raw = row.recovery_codes_hashed if row is not None else None

            if not found:
                return self._cache_entry(tenant_id)

            # Decrypt TOTP secret (a failure raises: see the except below)
            secret: str | None = None
            if encrypted_secret:
                secret = decrypt_secret(encrypted_secret)

            # Parse hashed recovery codes
            codes_hashed: list[str] = []
            if recovery_raw:
                codes_hashed = [c for c in recovery_raw.split("\n") if c.strip()]

            # Merge DB state with in-memory pending_secret (never stored in DB)
            pending = self._cache_entry(tenant_id).get("pending_secret")
            state: dict[str, Any] = {
                "enabled": enabled,
                "secret": secret,
                "pending_secret": pending,
                "recovery_codes_hashed": codes_hashed,
            }
            # Keep cache in sync
            self._cache[tenant_id] = state
            return state

        except Exception as exc:
            # Never fall back to the cache: a missing cache entry reads as
            # "MFA disabled", i.e. enforcement off (fail-open).
            raise MFAStateUnavailableError(f"MFA state unavailable: {exc}") from exc

    async def save(self, tenant_id: str, state: dict[str, Any]) -> None:
        """Persist MFA state to DB and update the in-memory cache.

        ``pending_secret`` is stored in the cache only; it is deliberately
        excluded from the DB row. The cache is updated only AFTER the DB write
        commits: a failed write raises :class:`MFAStateUnavailableError` (it
        used to be swallowed, so e.g. "MFA enabled" was reported while the DB
        — and every other replica — still said disabled).
        """
        if self._db is None:
            self._cache[tenant_id] = state
            return

        try:
            from datetime import UTC, datetime

            from sqlalchemy import select

            from app.api.mfa_crypto import encrypt_secret
            from app.db.models.mfa import TenantMFA
            from app.db.rls import sqlalchemy_rls_context

            encrypted = encrypt_secret(state["secret"]) if state.get("secret") else None
            codes_joined = "\n".join(state.get("recovery_codes_hashed", []))
            now = datetime.now(UTC)

            # Under the tenant GUC: the SELECT must see this tenant's row and the
            # INSERT/UPDATE must pass the tenant_mfa policy's WITH CHECK.
            # sqlalchemy_rls_context flushes the ORM add before it resets the GUC.
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(select(TenantMFA).where(TenantMFA.tenant_id == tenant_id))
                ).scalar_one_or_none()

                if row is None:
                    row = TenantMFA(
                        tenant_id=tenant_id,
                        enabled=state["enabled"],
                        encrypted_secret=encrypted,
                        recovery_codes_hashed=codes_joined,
                        enrolled_at=now if state["enabled"] else None,
                    )
                    session.add(row)
                else:
                    row.enabled = state["enabled"]
                    row.encrypted_secret = encrypted
                    row.recovery_codes_hashed = codes_joined
                    row.updated_at = now
                    if state["enabled"] and not row.enrolled_at:
                        row.enrolled_at = now

        except Exception as exc:
            raise MFAStateUnavailableError(f"MFA state could not be saved: {exc}") from exc
        self._cache[tenant_id] = state

    # ── MFA-KEY-RING: re-seal a secret still under a previous SECRET_KEY ──────

    async def _read_encrypted_secret(self, tenant_id: str) -> str | None:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text("SELECT encrypted_secret FROM tenant_mfa WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            ).fetchone()
        return str(row[0]) if row is not None and row[0] else None

    async def _swap_encrypted_secret(self, tenant_id: str, old: str, new: str) -> bool:
        """Compare-and-swap: a concurrent re-enrolment / disable always wins."""
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            result = await session.execute(
                text(
                    "UPDATE tenant_mfa SET encrypted_secret = :new, updated_at = NOW() "
                    "WHERE tenant_id = :t AND encrypted_secret = :old"
                ),
                {"t": tenant_id, "old": old, "new": new},
            )
        return bool(getattr(result, "rowcount", 0))

    async def reseal_if_needed(self, tenant_id: str) -> bool:
        """After a successful verify: re-seal the stored TOTP secret with the
        current SECRET_KEY when it is still under a previous key (or a legacy
        ``.b64`` row). Best effort — the verify already succeeded. True if
        re-sealed."""
        if self._db is None:
            return False
        from app.api.mfa_crypto import needs_reseal, reseal

        try:
            current = await self._read_encrypted_secret(tenant_id)
            if not current or not needs_reseal(current):
                return False
            return await self._swap_encrypted_secret(tenant_id, current, reseal(current))
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("mfa_reseal_failed", error=type(exc).__name__)
            return False


# Module-level singleton
_mfa_db_store = MFAStore()

# ---------------------------------------------------------------------------
# Backward-compat aliases (imported directly by tests)
# ---------------------------------------------------------------------------
# _mfa_store is the same dict as _mfa_db_store._cache; tests that do
#   _mfa_store["tid"] = {...}  or  _mfa_store.pop("tid", None)
# are directly manipulating the cache, which the async store reads/writes.
_mfa_store: dict[str, dict[str, Any]] = _mfa_db_store._cache

# ---------------------------------------------------------------------------
# Short-lived MFA session tokens (1-hour TTL)
# ---------------------------------------------------------------------------
# Maps session_token → {"tenant_id": str, "created_at": float, "method": str}
_mfa_verified_sessions: dict[str, dict] = {}


_MFA_SESSION_TTL = 3600  # seconds
_MFA_SESSION_PREFIX = "mfa_session:"


def _mfa_session_key(token: str) -> str:
    # Only a digest of the token is stored: a Redis dump never yields a usable token.
    return _MFA_SESSION_PREFIX + hashlib.sha256(token.encode()).hexdigest()


def _mfa_session_redis(app: Any) -> Any:
    return getattr(getattr(app, "state", None), "_redis", None)


async def issue_mfa_session(app: Any, tenant_id: str, method: str) -> str:
    """Mint an X-MFA-Token valid for one hour.

    Stored in Redis when it is wired, so a token issued by one replica is
    honoured by every replica (it used to live only in this process's dict, so
    the next request load-balanced elsewhere got MFA_REQUIRED). The in-process
    dict is used only when no Redis is configured (single process).
    """
    token = secrets.token_urlsafe(32)
    redis = _mfa_session_redis(app)
    if redis is not None:
        try:
            await redis.set(
                _mfa_session_key(token),
                json.dumps({"tenant_id": tenant_id, "method": method}),
                ex=_MFA_SESSION_TTL,
            )
        except Exception as exc:
            raise MFAStateUnavailableError(f"MFA session could not be stored: {exc}") from exc
        return token
    _cleanup_mfa_sessions()
    _mfa_verified_sessions[token] = {
        "tenant_id": tenant_id,
        "created_at": time.monotonic(),
        "method": method,
    }
    return token


async def check_mfa_session(app: Any, token: str, tenant_id: str) -> str:
    """Return ``"valid"``, ``"invalid"`` or ``"expired"`` for an X-MFA-Token.

    Raises:
        MFAStateUnavailableError: Redis is wired but could not be read.
    """
    redis = _mfa_session_redis(app)
    if redis is not None:
        try:
            raw = await redis.get(_mfa_session_key(token))
        except Exception as exc:
            raise MFAStateUnavailableError(f"MFA session lookup failed: {exc}") from exc
        if raw is None:
            return "invalid"  # unknown or expired (Redis TTL)
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return "invalid"
        return "valid" if data.get("tenant_id") == tenant_id else "invalid"
    entry = _mfa_verified_sessions.get(token)
    if not entry or entry.get("tenant_id") != tenant_id:
        return "invalid"
    if time.monotonic() - entry.get("created_at", 0) > _MFA_SESSION_TTL:
        _mfa_verified_sessions.pop(token, None)
        return "expired"
    return "valid"


def _cleanup_mfa_sessions() -> None:
    """Remove expired MFA session tokens (>1 hour old)."""
    now = time.monotonic()
    expired = [k for k, v in _mfa_verified_sessions.items() if now - v.get("created_at", 0) > 3600]
    for k in expired:
        del _mfa_verified_sessions[k]


def _get_mfa_state(tenant_id: str) -> dict[str, Any]:
    """Sync helper — returns (and creates) the cache entry for *tenant_id*.

    Used directly by unit tests and internally by endpoints that need to
    seed state without a full async round-trip (e.g. tests).
    """
    return _mfa_db_store._cache_entry(tenant_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unavailable(exc: MFAStateUnavailableError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="MFA state is temporarily unavailable; retry shortly.",
        headers={"Retry-After": "5"},
    )


async def _get_state(tenant_id: str) -> dict[str, Any]:
    try:
        return await _mfa_db_store.get(tenant_id)
    except MFAStateUnavailableError as exc:
        raise _unavailable(exc) from exc


async def _save_state(tenant_id: str, state: dict[str, Any]) -> None:
    try:
        await _mfa_db_store.save(tenant_id, state)
    except MFAStateUnavailableError as exc:
        raise _unavailable(exc) from exc


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _generate_recovery_codes() -> list[str]:
    """Return RECOVERY_CODE_COUNT random codes formatted as XXXXX-XXXXX."""
    alphabet = string.ascii_uppercase + string.digits
    return [
        f"{''.join(secrets.choice(alphabet) for _ in range(5))}"
        f"-"
        f"{''.join(secrets.choice(alphabet) for _ in range(5))}"
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def _hash_recovery_code(code: str) -> str:
    """SHA-256 hex digest of a normalised recovery code."""
    return hashlib.sha256(code.upper().strip().encode()).hexdigest()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class VerifyRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=6,
        max_length=11,
        description="6-digit TOTP code or 11-char recovery code (XXXXX-XXXXX)",
    )


class DisableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=11)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_mfa_status(request: Request) -> dict[str, Any]:
    """Return whether MFA is enabled for this tenant."""
    tenant = _require_tenant(request)
    state = await _get_state(tenant.tenant_id)
    pending = await _get_pending_secret(request, tenant.tenant_id)
    return {
        "enabled": state["enabled"],
        "has_pending_enrollment": pending is not None,
        "recovery_codes_count": len(state["recovery_codes_hashed"]),
    }


@router.post("/enroll")
async def begin_enrollment(request: Request) -> dict[str, Any]:
    """Start MFA enrollment — returns provisioning URI, raw secret, and optional QR SVG."""
    tenant = _require_tenant(request)
    await _check_rate_limit_global(tenant.tenant_id, "/auth/mfa/enroll", request)
    state = await _get_state(tenant.tenant_id)

    if state["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled. Disable it first.",
        )

    secret = pyotp.random_base32()
    # Never written to the tenant_mfa row; shared via Redis (encrypted, TTL)
    # so /verify-enrollment works on any replica.
    await _set_pending_secret(request, tenant.tenant_id, secret)

    totp = pyotp.TOTP(secret)

    # Resolve a human-readable account name (email preferred)
    account_name = tenant.tenant_id[:16]
    tenant_svc = getattr(request.app.state, "tenant_service", None)
    if tenant_svc is not None:
        try:
            info = await tenant_svc.get_tenant(tenant.tenant_id)
            account_name = info.get("email", account_name)
        except Exception:
            pass

    provisioning_uri = totp.provisioning_uri(name=account_name, issuer_name=APP_NAME)

    # Generate QR code as a base64-encoded SVG data URL (best-effort)
    qr_svg: str | None = None
    try:
        import qrcode  # type: ignore[import]
        import qrcode.image.svg  # type: ignore[import]

        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(provisioning_uri, image_factory=factory)
        buf = io.BytesIO()
        img.save(buf)
        svg_bytes = buf.getvalue()
        qr_svg = "data:image/svg+xml;base64," + base64.b64encode(svg_bytes).decode()
    except Exception:
        pass

    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "qr_code": qr_svg,
        "account_name": account_name,
        "issuer": APP_NAME,
        "algorithm": "SHA1",
        "digits": 6,
        "period": 30,
    }


@router.post("/verify-enrollment")
async def complete_enrollment(request: Request, body: VerifyRequest) -> dict[str, Any]:
    """Complete MFA enrollment by verifying the first TOTP code."""
    tenant = _require_tenant(request)
    await _check_rate_limit_global(tenant.tenant_id, "/auth/mfa/verify-enrollment", request)
    state = await _get_state(tenant.tenant_id)

    if state["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled.",
        )

    pending = await _get_pending_secret(request, tenant.tenant_id)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending enrollment. Call /auth/mfa/enroll first.",
        )

    totp = pyotp.TOTP(pending)
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(
            status_code=422,
            detail="Invalid TOTP code. Check your authenticator app and try again.",
        )

    await _reject_replayed_totp(request, tenant.tenant_id, body.code.strip())

    recovery_codes = _generate_recovery_codes()
    state["secret"] = pending
    state["pending_secret"] = None
    state["enabled"] = True
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in recovery_codes]

    await _save_state(tenant.tenant_id, state)
    await _clear_pending_secret(request, tenant.tenant_id)

    return {
        "status": "enabled",
        "recovery_codes": recovery_codes,
        "message": "MFA enabled successfully. Save these recovery codes in a safe place.",
    }


@router.post("/verify")
async def verify_mfa(request: Request, body: VerifyRequest) -> dict[str, Any]:
    """Verify a TOTP code (during login) or a one-time recovery code."""
    tenant = _require_tenant(request)
    await _check_rate_limit_global(tenant.tenant_id, "/auth/mfa/verify", request)
    state = await _get_state(tenant.tenant_id)

    if not state["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled for this account.",
        )

    code = body.code.strip().upper()

    # Recovery code path — format XXXXX-XXXXX (length 11, dash at position 5)
    if len(code) == 11 and code[5] == "-":
        hashed = _hash_recovery_code(code)
        if hashed in state["recovery_codes_hashed"]:
            state["recovery_codes_hashed"].remove(hashed)
            await _save_state(tenant.tenant_id, state)
            # A recovery code is a full second factor: without a session token
            # the enforcement middleware kept answering MFA_REQUIRED, so a user
            # who lost their authenticator could never get back in.
            try:
                recovery_token = await issue_mfa_session(
                    request.app, tenant.tenant_id, "recovery_code"
                )
            except MFAStateUnavailableError as exc:
                raise _unavailable(exc) from exc
            return {
                "status": "verified",
                "method": "recovery_code",
                "remaining_recovery_codes": len(state["recovery_codes_hashed"]),
                "session_token": recovery_token,
                "expires_in": _MFA_SESSION_TTL,
            }
        raise HTTPException(
            status_code=422,
            detail="Invalid recovery code.",
        )

    # TOTP path
    totp = pyotp.TOTP(state["secret"])
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(
            status_code=422,
            detail="Invalid TOTP code.",
        )

    await _reject_replayed_totp(request, tenant.tenant_id, body.code.strip())
    # MFA-KEY-RING: a secret still sealed with a previous SECRET_KEY moves to the
    # current one, so SECRET_KEY_PREVIOUS can be retired.
    await _mfa_db_store.reseal_if_needed(tenant.tenant_id)

    # Issue a short-lived session token (1-hour TTL); frontend stores as X-MFA-Token
    try:
        session_token = await issue_mfa_session(request.app, tenant.tenant_id, "totp")
    except MFAStateUnavailableError as exc:
        raise _unavailable(exc) from exc
    return {
        "status": "verified",
        "method": "totp",
        "session_token": session_token,
        "expires_in": _MFA_SESSION_TTL,
    }


@router.post("/disable")
async def disable_mfa(request: Request, body: DisableRequest) -> dict[str, Any]:
    """Disable MFA after verifying the current TOTP code."""
    tenant = _require_tenant(request)
    await _check_rate_limit_global(tenant.tenant_id, "/auth/mfa/disable", request)
    state = await _get_state(tenant.tenant_id)

    if not state["enabled"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled.")

    totp = pyotp.TOTP(state["secret"])
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(
            status_code=422,
            detail="Invalid TOTP code. MFA not disabled.",
        )

    await _reject_replayed_totp(request, tenant.tenant_id, body.code.strip())

    state["enabled"] = False
    state["secret"] = None
    state["recovery_codes_hashed"] = []
    state["pending_secret"] = None

    await _save_state(tenant.tenant_id, state)
    await _clear_pending_secret(request, tenant.tenant_id)

    return {"status": "disabled", "message": "MFA has been disabled."}


@router.get("/recovery-codes")
async def get_recovery_codes_count(request: Request) -> dict[str, Any]:
    """Return count of remaining recovery codes (the codes themselves are never returned)."""
    tenant = _require_tenant(request)
    state = await _get_state(tenant.tenant_id)
    if not state["enabled"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled.")
    return {
        "remaining": len(state["recovery_codes_hashed"]),
        "message": "Recovery codes are hashed and cannot be retrieved. Regenerate if needed.",
    }


@router.post("/regenerate")
async def regenerate_recovery_codes(request: Request, body: VerifyRequest) -> dict[str, Any]:
    """Regenerate recovery codes after verifying the current TOTP code."""
    tenant = _require_tenant(request)
    await _check_rate_limit_global(tenant.tenant_id, "/auth/mfa/regenerate", request)
    state = await _get_state(tenant.tenant_id)

    if not state["enabled"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled.")

    totp = pyotp.TOTP(state["secret"])
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(
            status_code=422,
            detail="Invalid TOTP code.",
        )

    await _reject_replayed_totp(request, tenant.tenant_id, body.code.strip())

    new_codes = _generate_recovery_codes()
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in new_codes]

    await _save_state(tenant.tenant_id, state)

    return {
        "recovery_codes": new_codes,
        "message": "Recovery codes regenerated. Previous codes are now invalid.",
    }
