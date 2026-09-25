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
    ``pending_secret`` (set during enrollment, cleared on verify) is kept
    *only* in-memory by design — it is never written to the DB.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
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
    now = time.time()
    key = f"{code}:{int(now // 30)}"  # TOTP time-step bucket
    if key in _used_totp_codes[tenant_id]:
        return True
    # Clean old entries (keep current and previous window only)
    _used_totp_codes[tenant_id] = {
        k for k in _used_totp_codes[tenant_id] if int(k.split(":")[1]) >= int(now // 30) - 1
    }
    _used_totp_codes[tenant_id].add(key)
    return False


async def _check_totp_replay(tenant_id: str, code: str, request: Any = None) -> bool:
    """Return True if code was already used (replay detected).

    Uses Redis for cross-replica consistency when available; falls back to the
    process-local set in degraded mode (single-replica or Redis down).

    The Redis key has a 31-second TTL — one TOTP window (30 s) plus a 1-second
    grace — so replayed codes are rejected across all replicas.
    """
    redis = None
    if request is not None:
        with contextlib.suppress(Exception):
            redis = getattr(request.app.state, "_redis", None)

    if redis is not None:
        try:
            key = f"mfa:used_totp:{tenant_id}:{code}"
            # SET key "1" NX EX 31: set only if not exists, with 31-second TTL.
            # Returns the set result (truthy) if the key was newly created,
            # or None/False if the key already existed (code already used → replay).
            was_set = await redis.set(key, "1", nx=True, ex=31)
            return was_set is None or not was_set
        except Exception:
            pass  # fall through to in-process fallback on Redis error

    # Degraded mode: in-process set (single-replica only)
    return _is_totp_replayed(tenant_id, code)


# ---------------------------------------------------------------------------
# DB-backed MFA store with in-memory cache
# ---------------------------------------------------------------------------

_DEFAULT_STATE: dict[str, Any] = {
    "enabled": False,
    "secret": None,
    "pending_secret": None,
    "recovery_codes_hashed": [],
}


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

            async with self._db() as session:
                row = (
                    await session.execute(select(TenantMFA).where(TenantMFA.tenant_id == tenant_id))
                ).scalar_one_or_none()

            if row is None:
                return self._cache_entry(tenant_id)

            # Decrypt TOTP secret
            secret: str | None = None
            if row.encrypted_secret:
                try:
                    secret = decrypt_secret(row.encrypted_secret)
                except Exception:
                    secret = None

            # Parse hashed recovery codes
            codes_hashed: list[str] = []
            if row.recovery_codes_hashed:
                codes_hashed = [c for c in row.recovery_codes_hashed.split("\n") if c.strip()]

            # Merge DB state with in-memory pending_secret (never stored in DB)
            pending = self._cache_entry(tenant_id).get("pending_secret")
            state: dict[str, Any] = {
                "enabled": row.enabled,
                "secret": secret,
                "pending_secret": pending,
                "recovery_codes_hashed": codes_hashed,
            }
            # Keep cache in sync
            self._cache[tenant_id] = state
            return state

        except Exception:
            # DB unavailable — fall through to cache
            return self._cache_entry(tenant_id)

    async def save(self, tenant_id: str, state: dict[str, Any]) -> None:
        """Persist MFA state to DB and update the in-memory cache.

        ``pending_secret`` is stored in the cache only; it is deliberately
        excluded from the DB row.  DB write failures are non-fatal — the
        cache remains the authoritative state for the current process.
        """
        # Always update cache immediately (pending_secret included)
        self._cache[tenant_id] = state

        if self._db is None:
            return

        try:
            from datetime import UTC, datetime

            from sqlalchemy import select

            from app.api.mfa_crypto import encrypt_secret
            from app.db.models.mfa import TenantMFA

            encrypted = encrypt_secret(state["secret"]) if state.get("secret") else None
            codes_joined = "\n".join(state.get("recovery_codes_hashed", []))
            now = datetime.now(UTC)

            async with self._db() as session, session.begin():
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

        except Exception:
            pass  # Cache is still updated; DB write failure is non-fatal.


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
    state = await _mfa_db_store.get(tenant.tenant_id)
    return {
        "enabled": state["enabled"],
        "has_pending_enrollment": state["pending_secret"] is not None,
        "recovery_codes_count": len(state["recovery_codes_hashed"]),
    }


@router.post("/enroll")
async def begin_enrollment(request: Request) -> dict[str, Any]:
    """Start MFA enrollment — returns provisioning URI, raw secret, and optional QR SVG."""
    tenant = _require_tenant(request)
    await _check_rate_limit_global(tenant.tenant_id, "/auth/mfa/enroll", request)
    state = await _mfa_db_store.get(tenant.tenant_id)

    if state["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled. Disable it first.",
        )

    secret = pyotp.random_base32()
    # pending_secret stays in-memory only — saved to cache but NOT to DB
    state["pending_secret"] = secret
    # Update cache directly (no DB write needed for pending-only state)
    _mfa_db_store._cache[tenant.tenant_id] = state

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
    state = await _mfa_db_store.get(tenant.tenant_id)

    if state["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled.",
        )

    pending = state.get("pending_secret")
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

    if _is_totp_replayed(tenant.tenant_id, body.code.strip()):
        raise HTTPException(422, "TOTP code already used. Wait for next code.")

    recovery_codes = _generate_recovery_codes()
    state["secret"] = pending
    state["pending_secret"] = None
    state["enabled"] = True
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in recovery_codes]

    await _mfa_db_store.save(tenant.tenant_id, state)

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
    state = await _mfa_db_store.get(tenant.tenant_id)

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
            await _mfa_db_store.save(tenant.tenant_id, state)
            return {
                "status": "verified",
                "method": "recovery_code",
                "remaining_recovery_codes": len(state["recovery_codes_hashed"]),
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

    if _is_totp_replayed(tenant.tenant_id, body.code.strip()):
        raise HTTPException(422, "TOTP code already used. Wait for next code.")

    # Issue a short-lived session token (1-hour TTL); frontend stores as X-MFA-Token
    session_token = secrets.token_urlsafe(32)
    _cleanup_mfa_sessions()
    _mfa_verified_sessions[session_token] = {
        "tenant_id": tenant.tenant_id,
        "created_at": time.monotonic(),
        "method": "totp",
    }
    return {
        "status": "verified",
        "method": "totp",
        "session_token": session_token,
        "expires_in": 3600,
    }


@router.post("/disable")
async def disable_mfa(request: Request, body: DisableRequest) -> dict[str, Any]:
    """Disable MFA after verifying the current TOTP code."""
    tenant = _require_tenant(request)
    await _check_rate_limit_global(tenant.tenant_id, "/auth/mfa/disable", request)
    state = await _mfa_db_store.get(tenant.tenant_id)

    if not state["enabled"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled.")

    totp = pyotp.TOTP(state["secret"])
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(
            status_code=422,
            detail="Invalid TOTP code. MFA not disabled.",
        )

    if _is_totp_replayed(tenant.tenant_id, body.code.strip()):
        raise HTTPException(422, "TOTP code already used. Wait for next code.")

    state["enabled"] = False
    state["secret"] = None
    state["recovery_codes_hashed"] = []
    state["pending_secret"] = None

    await _mfa_db_store.save(tenant.tenant_id, state)

    return {"status": "disabled", "message": "MFA has been disabled."}


@router.get("/recovery-codes")
async def get_recovery_codes_count(request: Request) -> dict[str, Any]:
    """Return count of remaining recovery codes (the codes themselves are never returned)."""
    tenant = _require_tenant(request)
    state = await _mfa_db_store.get(tenant.tenant_id)
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
    state = await _mfa_db_store.get(tenant.tenant_id)

    if not state["enabled"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled.")

    totp = pyotp.TOTP(state["secret"])
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(
            status_code=422,
            detail="Invalid TOTP code.",
        )

    if _is_totp_replayed(tenant.tenant_id, body.code.strip()):
        raise HTTPException(422, "TOTP code already used. Wait for next code.")

    new_codes = _generate_recovery_codes()
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in new_codes]

    await _mfa_db_store.save(tenant.tenant_id, state)

    return {
        "recovery_codes": new_codes,
        "message": "Recovery codes regenerated. Previous codes are now invalid.",
    }
