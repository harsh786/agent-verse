---
title: "Chat — API Reference"
description: "All 7 REST endpoints for the chat interface: request/response schemas, auth, pagination, error codes, and SSE stream protocol."
outline: deep
---

# API Reference

All endpoints require a valid API key (`X-API-Key: <key>`) or Bearer token. All responses are scoped to the authenticated tenant via RLS.

Base path: `/v1/chat`

---

## POST /v1/chat/sessions

Create a new chat session.

**Request body** (optional):
```json
{
  "agent_id": "uuid",      // optional — default agent used if omitted
  "title": "string"        // optional — auto-generated from first message if omitted
}
```

**Response 201:**
```json
{
  "data": {
    "id": "uuid",
    "title": "New Chat",
    "pinned": false,
    "ttl_days": 7,
    "agent_id": null,
    "created_at": "2026-08-15T10:00:00Z",
    "updated_at": "2026-08-15T10:00:00Z"
  }
}
```

---

## GET /v1/chat/sessions

List sessions with pagination. Sorted by `updated_at DESC`.

**Query params:**
- `page` (int, default 1)
- `per_page` (int, default 20, max 100)
- `pinned` (bool, optional — filter pinned only)
- `search` (string, optional — fuzzy search on title)

**Response 200:**
```json
{
  "data": [
    {
      "id": "uuid",
      "title": "Deploy to staging",
      "pinned": true,
      "ttl_days": null,
      "message_count": 24,
      "last_message_at": "2026-08-15T09:55:00Z",
      "has_active_goal": false
    }
  ],
  "meta": {
    "page": 1,
    "per_page": 20,
    "total": 47,
    "has_more": true
  }
}
```

---

## GET /v1/chat/sessions/{id}

Get session detail with last 50 messages.

**Response 200:**
```json
{
  "data": {
    "id": "uuid",
    "title": "Deploy to staging",
    "pinned": true,
    "agent_id": "uuid",
    "messages": [
      {
        "id": "uuid",
        "role": "user",
        "content": "Deploy the app to staging",
        "metadata": {},
        "created_at": "2026-08-15T09:50:00Z"
      },
      {
        "id": "uuid",
        "role": "assistant",
        "content": "✅ Deployment complete",
        "metadata": {
          "intent": "goal",
          "goal_id": "uuid",
          "step_count": 3,
          "latency_ms": 4300
        },
        "created_at": "2026-08-15T09:50:05Z"
      }
    ],
    "meta": {
      "message_count": 24,
      "loaded": 50
    }
  }
}
```

---

## DELETE /v1/chat/sessions/{id}

Delete session and all its messages. Cascades via FK.

**Response 204:** No content.

---

## PATCH /v1/chat/sessions/{id}

Update session metadata (pin/unpin, rename, change agent).

**Request body** (all fields optional):
```json
{
  "pinned": true,
  "title": "My renamed session",
  "agent_id": "uuid"
}
```

**Response 200:** Updated session object.

---

## POST /v1/chat/sessions/{id}/messages

Send a message to the session. This triggers intent routing, starts execution (if GOAL), and saves the message. Streaming output is delivered separately on the SSE endpoint.

**Request body:**
```json
{
  "content": "Deploy the app to staging",
  "file_ids": ["uuid"],    // optional — uploaded file attachments
  "agent_id": "uuid"       // optional — override session agent for this message
}
```

**Response 202:**
```json
{
  "data": {
    "message_id": "uuid",
    "intent": "goal",
    "goal_id": "uuid",      // present if intent=goal
    "stream_url": "/v1/chat/sessions/{id}/stream"
  }
}
```

The client should already have the SSE stream open before sending the message. Events begin flowing immediately after the 202 response.

---

## GET /v1/chat/sessions/{id}/stream

Server-Sent Events stream. Keep this connection open for the duration of the session page.

**Headers:**
```
Accept: text/event-stream
Cache-Control: no-cache
```

**Event format:**
```
data: {"type":"routing","intent":"goal","message_id":"..."}

data: {"type":"step_started","step":1,"description":"Run tests","goal_id":"..."}

data: {"type":"token","content":"The","message_id":"..."}
```

**All event types:** See [03-goal-execution-and-sse-streaming.md](03-goal-execution-and-sse-streaming.md).

**Reconnect:** On disconnect, the client reconnects with the `Last-Event-ID` header. The server replays any events missed since that ID (buffered in Redis for 60 seconds).

---

## POST /v1/chat/sessions/{id}/messages/{msg_id}/feedback

Submit thumbs-up/down feedback on an assistant message.

**Request body:**
```json
{
  "rating": 1,          // 1 = positive, -1 = negative
  "comment": "string"   // optional
}
```

**Response 204:** No content.

---

## POST /v1/chat/sessions/{id}/push-subscription

Register browser push subscription for background goal notifications.

**Request body:** Web Push subscription JSON (from `PushManager.subscribe()`).

**Response 201:** No content.

---

## Error Codes

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Invalid request body |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 403 | `FORBIDDEN` | Tenant does not own this session |
| 404 | `NOT_FOUND` | Session or message not found |
| 409 | `GOAL_ALREADY_RUNNING` | Session has an active goal; send stop first |
| 422 | `CONTENT_POLICY` | Message blocked by guardrails |
| 429 | `RATE_LIMITED` | Too many requests; `Retry-After` header present |
| 500 | `INTERNAL_ERROR` | Server error; `requestId` in response for tracing |

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "You have exceeded the chat rate limit",
    "details": {},
    "request_id": "req-abc123"
  }
}
```

---

## Rate Limits

| Endpoint | Limit |
|---|---|
| POST /sessions | 10/minute per tenant |
| POST /sessions/{id}/messages | 60/minute per tenant |
| GET /sessions/{id}/stream | 5 concurrent per tenant |
| GET /sessions (list) | 120/minute |

Rate limit headers returned on every response:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 58
X-RateLimit-Reset: 1723718460
```
