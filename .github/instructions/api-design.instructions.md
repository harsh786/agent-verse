---
applyTo: "agent-verse-backend/**/*.py,agent-verse-backend/helm/**"
---

# API Design Instructions — AgentVerse

## REST Conventions

### URL Structure
```
GET    /v1/{resources}              → list (paginated)
GET    /v1/{resources}/{id}         → get one
POST   /v1/{resources}             → create (returns 201)
PUT    /v1/{resources}/{id}         → full replace (returns 200)
PATCH  /v1/{resources}/{id}         → partial update (returns 200)
DELETE /v1/{resources}/{id}         → delete (returns 204)

# Nested resources (max 2 levels deep):
GET    /v1/orgs/{org_id}/missions
POST   /v1/orgs/{org_id}/missions

# Actions (not CRUD):
POST   /v1/missions/{id}/approve
POST   /v1/missions/{id}/cancel
POST   /v1/graphify/{id}/start
```

### Required Headers on All Routes
```python
@router.post("", operation_id="mission_create")   # ALWAYS set operation_id
async def create(
    body: CreateRequest,
    tenant: TenantContext = Depends(get_tenant),
    x_request_id: str = Header(default_factory=lambda: str(uuid4())),
    x_idempotency_key: str | None = Header(default=None),
) -> Response:
    ...
```

## Cursor Pagination (All List Endpoints)

```python
# Request:  GET /v1/missions?cursor=<opaque>&limit=20
# Response: { "data": [...], "cursor": "<next>", "hasMore": true, "total": 156 }

class CursorPage(BaseModel, Generic[T]):
    data:    list[T]
    cursor:  str | None     # None = no more pages
    hasMore: bool
    total:   int | None = None   # optional count

# Repository implementation:
async def list_cursor(self, cursor: str | None, limit: int) -> CursorPage[T]:
    rows = await session.scalars(
        select(Model)
        .where(Model.tenant_id == self._tenant.id)
        .where(Model.id < cursor if cursor else True)
        .order_by(Model.created_at.desc(), Model.id.desc())
        .limit(limit + 1)   # fetch one extra to detect hasMore
    )
    items = list(rows)
    has_more = len(items) > limit
    return CursorPage(
        data=items[:limit],
        cursor=str(items[limit - 1].id) if has_more else None,
        hasMore=has_more,
    )
```

## RFC 7807 Error Format (All Errors)

```python
# ALL errors must return Problem Details format:
{
    "type": "https://docs.agentverse.io/errors/{error-code}",
    "title": "Human-readable title",
    "status": 422,
    "detail": "Specific explanation of what went wrong",
    "instance": "/v1/missions",
    "request_id": "req_abc123xyz",
    "errors": [   # optional: validation field errors
        {"field": "title", "code": "required", "message": "Title is required"}
    ]
}

# FastAPI global exception handler:
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        "type": "https://docs.agentverse.io/errors/validation-error",
        "title": "Validation Error",
        "status": 422,
        "detail": "Request body failed validation",
        "request_id": getattr(request.state, "request_id", ""),
        "errors": [{"field": ".".join(str(l) for l in e["loc"][1:]),
                    "message": e["msg"]} for e in exc.errors()],
    })
```

## HTTP Status Codes

| Situation | Code |
|-----------|------|
| Successful read | 200 OK |
| Successful create | 201 Created + Location header |
| Successful update | 200 OK |
| Successful delete | 204 No Content |
| Validation error | 422 Unprocessable Entity |
| Not found | 404 Not Found |
| Unauthorized | 401 Unauthorized |
| Forbidden | 403 Forbidden |
| Duplicate/conflict | 409 Conflict |
| Rate limited | 429 Too Many Requests + Retry-After |
| Service overloaded | 503 Service Unavailable + Retry-After |

## Rate Limit Headers (Automatic via Middleware)

```
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 597
X-RateLimit-Reset: 1724028000
Retry-After: 60   (on 429 only)
```

## API Versioning Strategy

```
/v1/  → current stable (supported indefinitely)
/v2/  → new major version (add only when breaking changes needed)

# Deprecation process:
# 1. Add Deprecation: <date> header to old endpoints
# 2. 6-month notice period
# 3. Remove in next major version only
```

## Idempotency

```python
# All create endpoints support X-Idempotency-Key:
# Same key + same payload → same response (cached 24h)
# Prevents duplicate creation on network retry

@router.post("")
async def create(
    body: CreateRequest,
    x_idempotency_key: str | None = Header(default=None),
    service: Service = Depends(get_service),
):
    return await service.create(body, idempotency_key=x_idempotency_key)
```

## Response Compression

```python
# Already configured in app/main.py:
# GZipMiddleware compresses responses > 1KB automatically
# No action needed in routers
```
