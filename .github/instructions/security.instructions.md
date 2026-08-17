---
applyTo: "agent-verse-backend/**/*.py"
---

# Security Instructions — AgentVerse

## Authentication Pattern (Already Built — Never Bypass)

```python
# The tenancy middleware handles all auth. Never bypass it.
# All protected routes: use Depends(get_tenant)

from app.tenancy.deps import get_tenant
from app.tenancy.context import TenantContext

@router.post("/resource")
async def create_resource(
    body: CreateRequest,
    tenant: TenantContext = Depends(get_tenant),  # ALWAYS
):
    ...
```

## Input Validation

```python
# ALL external inputs validated at the boundary — Pydantic v2 does this
# Additional custom validators:

class CreateMissionRequest(BaseModel):
    model_config = {"extra": "forbid"}    # Reject unknown fields

    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    # No regex injection possible with typed fields
```

## SQL Injection Prevention

```python
# ✅ CORRECT — parameterized (SQLAlchemy ORM always parameterizes)
result = await session.execute(
    select(Mission).where(Mission.tenant_id == tenant_id)
)

# ✅ CORRECT — parameterized raw SQL
result = await session.execute(
    text("SELECT * FROM missions WHERE tenant_id = :tid"),
    {"tid": str(tenant_id)}
)

# ❌ NEVER — f-string SQL injection risk
result = await session.execute(
    text(f"SELECT * FROM missions WHERE title = '{title}'")
)
```

## Secret Management

```python
# ✅ CORRECT — from settings (loaded from env)
settings.ANTHROPIC_API_KEY
settings.DATABASE_URL

# ❌ NEVER — hardcoded
api_key = "sk-ant-api03-..."
password = "mysecretpassword"

# ✅ CORRECT — Vault for tenant secrets
from app.providers.vault import CredentialVault
vault = CredentialVault()
credential = await vault.get_tenant_credential(tenant_id, "github_token")
```

## File Upload Security

```python
# ALWAYS validate uploads:
class FileUploadValidator:
    MAX_SIZE_BYTES = 50 * 1024 * 1024    # 50MB
    ALLOWED_MIMES = {
        "text/plain", "text/markdown", "application/pdf",
        "application/json", "image/jpeg", "image/png", "image/webp",
    }

    async def validate(self, file: UploadFile) -> None:
        # 1. Size
        if file.size > self.MAX_SIZE_BYTES:
            raise FileTooLargeError()

        # 2. MIME type from content (not just extension!)
        content = await file.read(2048)
        await file.seek(0)
        actual_mime = magic.from_buffer(content, mime=True)
        if actual_mime not in self.ALLOWED_MIMES:
            raise InvalidFileTypeError(actual_mime)

        # 3. Filename (prevent path traversal)
        safe_name = secure_filename(file.filename)
        if ".." in safe_name or "/" in safe_name:
            raise InvalidFilenameError()
```

## CORS Configuration

```python
# In app/main.py — already configured:
# Never use allow_origins=["*"] in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,    # explicit list
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "X-API-Key", "X-Request-ID", "X-CSRFToken"],
)
```

## Rate Limiting

```python
# Already enforced by TenantMiddleware + SlidingWindowRateLimiter
# Per-plan limits applied automatically
# For endpoint-level overrides:

from app.tenancy.rate_limiter import rate_limit

@router.post("/graphify")           # expensive operation
@rate_limit(rpm=1, burst=0)        # 1 per minute, no burst
async def start_graphify(tenant: TenantContext = Depends(get_tenant)):
    ...
```

## Audit Logging (Security Events)

```python
# ALWAYS log security-relevant events to audit trail:
from app.governance.audit import AuditLogger

audit = AuditLogger()
await audit.log(
    tenant_id=tenant.id,
    actor_id=tenant.user_id,
    action="api_key.created",
    resource_type="api_key",
    resource_id=str(key_id),
    outcome="success",
    metadata={"label": key_label},
)

# Log these events:
# - API key create/revoke
# - Role assignment changes
# - SSO config changes
# - Data deletion requests
# - Failed authentication attempts
# - Admin actions
```

## Webhook Security

```python
# Always sign outbound webhooks:
import hmac, hashlib

def sign_payload(payload: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()

# Always verify inbound webhook signatures:
def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, signature)   # timing-safe comparison
```

## Security Headers (Already Applied by SecurityHeadersMiddleware)

```
Content-Security-Policy: default-src 'self'; ...
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

## Anti-Patterns (Security)

```python
# ❌ NEVER — timing attack vulnerability
if token == stored_token:  ...

# ✅ CORRECT — constant-time comparison
import hmac
if hmac.compare_digest(token.encode(), stored_token.encode()): ...

# ❌ NEVER — log sensitive data
log.info("user.auth", password=password, api_key=api_key)

# ✅ CORRECT — redacted
log.info("user.auth", user_id=user_id, auth_method="api_key")

# ❌ NEVER — pickle user-supplied data (RCE risk)
data = pickle.loads(user_input)

# ✅ CORRECT — JSON only
data = json.loads(user_input)
```
