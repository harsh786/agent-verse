# RPA — World-Class Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Transform RPA from a polling-screenshot hack into a real-time browser automation platform with SSE screenshot streaming, Redis-backed cross-worker session registry, goal→session linking, vision-assisted auto-healing, and a full-screen browser mirror UI with keyboard/mouse takeover.

**Architecture:** Add read-only screenshot endpoint + SSE stream + Redis session registry + WebSocket takeover relay; add 10 new Playwright primitives; build full-screen `BrowserMirrorPage` with live event log; link RPA sessions to running goals.

**Tech Stack:** Python 3.12 · FastAPI · Playwright · Redis pub/sub · WebSocket · asyncio · React 19 · EventSource · Zustand · CSS Animations

---

## 1. Vision

World-class RPA means:
- An agent navigates a real browser. You watch it happen in real-time — the screen updates every second in your browser, you see the URL change, you see clicks and keystrokes
- When the agent gets stuck (selector not found, CAPTCHA, unexpected modal), it uses vision to re-identify the element and heals automatically
- You can take over the keyboard and mouse at any time with a single click
- Every action is logged with a screenshot thumbnail — full audit trail
- The agent can navigate multiple tabs, extract data from iframes, scroll through dynamic content, and execute arbitrary JS

---

## 2. Current State

### What works (A− backend)

| Feature | File | Evidence |
|---------|------|----------|
| 8 Playwright operations | `rpa/executor.py:119-300` | rpa_open_url, rpa_click, rpa_type, rpa_extract_text, rpa_screenshot, rpa_wait_for_text, rpa_select_option, rpa_upload_file |
| Stateful session manager | `rpa/session_manager.py:61` | `get_or_create()` with idle TTL |
| Redis-backed session store | `rpa/session.py:41` | `rpa_session:{id}` with TTL 24h |
| Credential injection | `rpa/executor.py:63` | `vault://` URI resolution |
| Playwright availability check | `rpa/executor.py:22` | Falls back to simulation |
| API endpoints | `api/rpa.py` | 7 endpoints including takeover |

### Critical gap

**RpaLivePage.tsx calls `POST /rpa/execute` (a write endpoint) every 2 seconds** to get screenshots. This:
- Creates a screenshot action in the audit log every 2 seconds
- Is not a real live view — it's polling an action tool
- Would consume budget on a real paid provider
- Only shows 2-second-stale snapshots

### High gaps

- `BrowserSessionManager._sessions` is an in-process dict — sessions don't survive cross-worker requests
- No read-only screenshot endpoint
- No SSE/WebSocket for real-time events
- No goal → session link
- Missing 10+ standard Playwright primitives (scroll, hover, keyboard, JS eval, iframe, drag-drop, etc.)
- `alert()` for human takeover

---

## 3. Backend Specification

### 3.1 Critical Fix: Read-Only Screenshot Endpoint

**File to modify**: `agent-verse-backend/app/api/rpa.py`

```python
@router.get("/sessions/{session_id}/current-view")
async def get_current_view(session_id: str, request: Request) -> dict[str, Any]:
    """Return the current browser viewport WITHOUT creating an action log entry.
    
    This is a read-only peek at the session state — suitable for live preview polling
    as an interim before the SSE stream is implemented.
    """
    ctx = _require_tenant(request)
    session_manager: BrowserSessionManager = getattr(request.app.state, "rpa_session_manager", None)
    if session_manager is None:
        raise HTTPException(503, "RPA session manager not available")

    session = await session_manager.get_session(session_id, tenant_id=ctx.tenant_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")

    try:
        # Access the Playwright page directly without going through executor
        page = session_manager.get_page(session_id)
        if page is None:
            raise HTTPException(404, "Browser page not available for this session")

        import base64
        screenshot_bytes = await page.screenshot(type="jpeg", quality=60, full_page=False)
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

        return {
            "session_id": session_id,
            "screenshot_data_uri": f"data:image/jpeg;base64,{screenshot_b64}",
            "url": page.url,
            "title": await page.title(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to capture screenshot: {exc}") from exc
```

### 3.2 SSE Browser Event Stream

**File to modify**: `agent-verse-backend/app/api/rpa.py`

```python
@router.get("/sessions/{session_id}/stream")
async def rpa_session_stream(session_id: str, request: Request) -> StreamingResponse:
    """SSE stream of browser events and screenshot frames for a session.
    
    Emits:
    - screenshot (1fps by default, JPEG base64)
    - navigation (on every page URL change)
    - action_started, action_completed, action_failed (from executor events)
    - takeover_requested
    """
    ctx = _require_tenant(request)
    redis_client = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis_client is None:
        raise HTTPException(503, "Redis not available for SSE stream")

    channel = f"rpa_stream:{ctx.tenant_id}:{session_id}"

    async def generate():
        import json
        # Subscribe to Redis channel for this session
        pubsub = redis_client.pubsub() if hasattr(redis_client, "pubsub") else None
        if pubsub is None:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Redis pubsub not available'})}\n\n"
            return

        await pubsub.subscribe(channel)
        try:
            yield f"data: {json.dumps({'type': 'stream_started', 'session_id': session_id})}\n\n"
            async for message in pubsub.listen():
                if await request.is_disconnected():
                    break
                if message["type"] == "message":
                    yield f"data: {message['data']}\n\n"
        finally:
            await pubsub.unsubscribe(channel)
            if hasattr(pubsub, "close"):
                await pubsub.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

**File to modify**: `agent-verse-backend/app/rpa/session_manager.py`

Add screenshot loop that publishes to Redis:

```python
import asyncio
import base64
import json
from datetime import datetime, timezone

class BrowserSessionManager:
    def __init__(self, ...):
        # ... existing init ...
        self._screenshot_tasks: dict[str, asyncio.Task] = {}

    async def _screenshot_loop(self, session_id: str, tenant_id: str, page, redis_client, fps: float = 1.0):
        """Background task: capture screenshot at `fps` Hz and publish to Redis."""
        channel = f"rpa_stream:{tenant_id}:{session_id}"
        interval = 1.0 / fps
        last_url = ""

        while True:
            try:
                # Capture screenshot
                screenshot_bytes = await page.screenshot(type="jpeg", quality=55, full_page=False)
                b64 = base64.b64encode(screenshot_bytes).decode()
                current_url = page.url

                event = {
                    "type": "screenshot",
                    "data_uri": f"data:image/jpeg;base64,{b64}",
                    "url": current_url,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await redis_client.publish(channel, json.dumps(event))

                # Emit navigation event if URL changed
                if current_url != last_url and last_url:
                    nav_event = {
                        "type": "navigation",
                        "from_url": last_url,
                        "to_url": current_url,
                        "title": await page.title(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    await redis_client.publish(channel, json.dumps(nav_event))
                last_url = current_url

                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(interval)  # continue on transient errors

    def start_screenshot_loop(self, session_id: str, tenant_id: str, page, redis_client, fps: float = 1.0):
        """Start background screenshot capture for a session."""
        if session_id in self._screenshot_tasks:
            return
        task = asyncio.create_task(
            self._screenshot_loop(session_id, tenant_id, page, redis_client, fps)
        )
        self._screenshot_tasks[session_id] = task

    def stop_screenshot_loop(self, session_id: str):
        """Stop screenshot capture when session closes."""
        task = self._screenshot_tasks.pop(session_id, None)
        if task:
            task.cancel()
```

**File to modify**: `agent-verse-backend/app/rpa/executor.py`

After every action execution, publish action events to Redis:

```python
async def _emit_action_event(self, session_id: str, tenant_id: str, event_type: str, **kwargs):
    """Publish action event to Redis for SSE streaming."""
    redis_client = getattr(self, "_redis", None)
    if redis_client is None:
        return
    import json
    from datetime import datetime, timezone
    channel = f"rpa_stream:{tenant_id}:{session_id}"
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    try:
        await redis_client.publish(channel, json.dumps(event))
    except Exception:
        pass  # non-critical

# Call before and after each tool execution:
await self._emit_action_event(session_id, tenant_id, "action_started",
    tool_name=tool_name, arguments=arguments, action_id=action_id)
# ... execute ...
await self._emit_action_event(session_id, tenant_id, "action_completed",
    tool_name=tool_name, output=str(output)[:500], duration_ms=duration, action_id=action_id)
```

### 3.3 Redis-Backed Session Registry

**File to modify**: `agent-verse-backend/app/rpa/session_manager.py`

Replace the in-process `self._sessions` dict with Redis-backed coordination:

```python
class BrowserSessionManager:
    def __init__(self, redis_client=None, worker_id: str | None = None):
        self._sessions: dict[str, BrowserSession] = {}  # local in-process cache
        self._redis = redis_client
        self._worker_id = worker_id or str(uuid.uuid4())[:8]

    async def _register_session(self, session_id: str, tenant_id: str) -> None:
        """Register this session as owned by this worker in Redis."""
        if self._redis is None:
            return
        await self._redis.setex(f"rpa_session_alive:{session_id}", 3600, self._worker_id)
        await self._redis.hset(f"rpa_session_meta:{session_id}", mapping={
            "worker_id": self._worker_id,
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await self._redis.sadd(f"rpa_tenant_sessions:{tenant_id}", session_id)

    async def get_session_worker(self, session_id: str) -> str | None:
        """Get the worker_id that owns this session."""
        if self._redis is None:
            return None
        worker = await self._redis.get(f"rpa_session_alive:{session_id}")
        return worker.decode() if isinstance(worker, bytes) else worker

    async def get_or_create(self, session_id: str, tenant_id: str, **kwargs) -> "BrowserSession":
        # Check if we own this session
        if session_id in self._sessions:
            return self._sessions[session_id]

        # Check if another worker owns it
        owner_worker = await self.get_session_worker(session_id)
        if owner_worker and owner_worker != self._worker_id:
            # We don't own it — this is a cross-worker request
            # For now: create a new local session (simplified — full impl needs Redis pub/sub RPC)
            # TODO: implement Redis pub/sub request-reply for cross-worker delegation
            pass

        # Create new session
        session = await self._create_session(session_id=session_id, tenant_id=tenant_id, **kwargs)
        self._sessions[session_id] = session
        await self._register_session(session_id, tenant_id)
        return session

    async def list_tenant_sessions(self, tenant_id: str) -> list[dict]:
        """List all sessions for a tenant (across all workers via Redis)."""
        if self._redis is None:
            return [{"session_id": k, "tenant_id": tenant_id} for k in self._sessions]
        session_ids = await self._redis.smembers(f"rpa_tenant_sessions:{tenant_id}")
        result = []
        for sid in session_ids:
            sid_str = sid.decode() if isinstance(sid, bytes) else sid
            meta = await self._redis.hgetall(f"rpa_session_meta:{sid_str}")
            if meta:
                result.append({"session_id": sid_str, **{k.decode(): v.decode() for k, v in meta.items()}})
        return result
```

### 3.4 New Playwright Primitives

**File to modify**: `agent-verse-backend/app/rpa/executor.py`

Add these 12 new operations to `_execute_with_playwright()`:

```python
case "rpa_scroll":
    """Scroll the page or an element."""
    selector = arguments.get("selector")
    scroll_x = arguments.get("scroll_x", 0)
    scroll_y = arguments.get("scroll_y", 500)
    if selector:
        element = await page.query_selector(selector)
        if element:
            await element.scroll_into_view_if_needed()
            await page.evaluate("(el, x, y) => el.scrollBy(x, y)", element, scroll_x, scroll_y)
        else:
            return {"success": False, "error": f"Selector not found: {selector}"}
    else:
        await page.evaluate(f"window.scrollBy({scroll_x}, {scroll_y})")
    await asyncio.sleep(0.3)
    screenshot = await page.screenshot(type="jpeg", quality=60)
    return {"success": True, "output": f"Scrolled ({scroll_x}, {scroll_y})", "screenshot_b64": base64.b64encode(screenshot).decode()}

case "rpa_hover":
    """Hover over an element."""
    selector = arguments.get("selector", "")
    await page.hover(selector)
    await asyncio.sleep(0.5)
    screenshot = await page.screenshot(type="jpeg", quality=60)
    return {"success": True, "output": f"Hovered over {selector}", "screenshot_b64": base64.b64encode(screenshot).decode()}

case "rpa_keyboard_press":
    """Press a keyboard key (Enter, Escape, Tab, ArrowDown, etc.)."""
    key = arguments.get("key", "Enter")
    await page.keyboard.press(key)
    await asyncio.sleep(0.2)
    return {"success": True, "output": f"Pressed key: {key}"}

case "rpa_keyboard_type":
    """Type text character by character with configurable delay."""
    text = arguments.get("text", "")
    delay_ms = arguments.get("delay_ms", 50)
    await page.keyboard.type(text, delay=delay_ms)
    return {"success": True, "output": f"Typed {len(text)} characters"}

case "rpa_get_attribute":
    """Get an element attribute (href, value, data-*, etc.)."""
    selector = arguments.get("selector", "")
    attribute = arguments.get("attribute", "")
    value = await page.get_attribute(selector, attribute)
    return {"success": True, "output": value or "", "attribute": attribute, "selector": selector}

case "rpa_evaluate_js":
    """Execute arbitrary JavaScript and return the result."""
    js_expression = arguments.get("expression", "document.title")
    result = await page.evaluate(js_expression)
    return {"success": True, "output": str(result)[:2000], "result_type": type(result).__name__}

case "rpa_wait_for_selector":
    """Wait until a CSS selector is present and visible."""
    selector = arguments.get("selector", "")
    timeout_ms = arguments.get("timeout_ms", 10000)
    state = arguments.get("state", "visible")  # attached | detached | hidden | visible
    await page.wait_for_selector(selector, timeout=timeout_ms, state=state)
    return {"success": True, "output": f"Selector '{selector}' is now {state}"}

case "rpa_iframe_switch":
    """Switch context to an iframe by URL pattern or nth index."""
    url_pattern = arguments.get("url_pattern")
    frame_index = arguments.get("frame_index", 0)
    if url_pattern:
        frame = next((f for f in page.frames if url_pattern in f.url), None)
    else:
        frames = page.frames
        frame = frames[frame_index] if frame_index < len(frames) else None
    if frame is None:
        return {"success": False, "error": "Frame not found"}
    # Store frame reference for subsequent operations (simplified — store in session context)
    return {"success": True, "output": f"Switched to frame: {frame.url}", "frame_url": frame.url}

case "rpa_screenshot_element":
    """Take a screenshot of a specific element only."""
    selector = arguments.get("selector", "")
    element = await page.query_selector(selector)
    if element is None:
        return {"success": False, "error": f"Element not found: {selector}"}
    screenshot_bytes = await element.screenshot(type="jpeg", quality=80)
    b64 = base64.b64encode(screenshot_bytes).decode()
    artifact_url = None
    if self._artifact_store:
        artifact_url = await self._artifact_store.store(screenshot_bytes, f"rpa_element_{selector[:20]}.jpg", "image/jpeg")
    return {"success": True, "output": f"Element screenshot captured", "screenshot_b64": b64, "artifact_url": artifact_url}

case "rpa_drag_drop":
    """Drag from source selector to target selector."""
    source = arguments.get("source", "")
    target = arguments.get("target", "")
    await page.drag_and_drop(source, target)
    await asyncio.sleep(0.5)
    return {"success": True, "output": f"Dragged from {source} to {target}"}

case "rpa_pdf_export":
    """Export the current page as PDF."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf_path = f.name
    await page.pdf(path=pdf_path, format="A4", print_background=True)
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    artifact_url = None
    if self._artifact_store:
        artifact_url = await self._artifact_store.store(pdf_bytes, "rpa_page.pdf", "application/pdf")
    import os; os.unlink(pdf_path)
    return {"success": True, "output": "PDF exported", "artifact_url": artifact_url, "size_bytes": len(pdf_bytes)}

case "rpa_new_tab":
    """Open a new browser tab and navigate to URL."""
    url = arguments.get("url", "")
    context = page.context
    new_page = await context.new_page()
    if url:
        await new_page.goto(url)
        await new_page.wait_for_load_state("domcontentloaded")
    screenshot_bytes = await new_page.screenshot(type="jpeg", quality=60)
    return {"success": True, "output": f"Opened new tab: {url}", "screenshot_b64": base64.b64encode(screenshot_bytes).decode()}
```

Also update `RPA_TOOLS` in `rpa/tools.py` to include all 12 new tool definitions with proper schemas.

### 3.5 Vision-Assisted Auto-Healing

**File to modify**: `agent-verse-backend/app/rpa/executor.py`

Wrap `rpa_click` execution with auto-heal:

```python
async def _execute_with_playwright(self, tool_name: str, arguments: dict, page, session_id: str, tenant_id: str):
    if tool_name == "rpa_click":
        selector = arguments.get("selector", "")
        try:
            # Try normal click first
            return await self._playwright_click(page, selector, arguments)
        except Exception as original_exc:
            # Auto-heal: use vision to find the right element
            if not arguments.get("auto_heal", True):
                raise
            return await self._auto_heal_click(page, selector, original_exc, arguments, session_id, tenant_id)

async def _auto_heal_click(self, page, original_selector: str, original_exc: Exception, arguments: dict, session_id: str, tenant_id: str) -> dict:
    """Attempt to find and click the element using vision when selector fails."""
    browser_agent = getattr(self, "_browser_agent", None)
    if browser_agent is None:
        raise original_exc  # No vision — re-raise

    try:
        # Capture screenshot for vision analysis
        screenshot_bytes = await page.screenshot(type="jpeg", quality=70)

        # Ask vision to find the element
        analysis = await browser_agent.find_element_by_description(
            screenshot=screenshot_bytes,
            description=f"Element matching selector: {original_selector}",
        )

        if not analysis or analysis.get("confidence", 0) < 0.65:
            raise original_exc  # Low confidence — re-raise original

        suggested_selector = analysis.get("suggested_selector", "")
        if not suggested_selector:
            raise original_exc

        # Emit auto-heal event
        await self._emit_action_event(session_id, tenant_id, "auto_heal_attempted",
            original_selector=original_selector,
            new_selector=suggested_selector,
            confidence=analysis.get("confidence", 0),
        )

        # Retry with healed selector
        arguments_healed = {**arguments, "selector": suggested_selector}
        result = await self._playwright_click(page, suggested_selector, arguments_healed)
        result["auto_healed"] = True
        result["original_selector"] = original_selector
        result["healed_selector"] = suggested_selector
        return result

    except Exception:
        raise original_exc  # All healing attempts failed — re-raise original
```

### 3.6 Goal → Session Linking

**File to modify**: `agent-verse-backend/app/rpa/executor.py`

In `execute()`, after successful action execution:

```python
# If we have a goal_id in context, link it to this session
goal_id = arguments.get("_goal_id") or getattr(self, "_current_goal_id", None)
if goal_id and session_id and self._redis:
    await self._redis.hset(f"rpa_goal_sessions:{goal_id}", session_id, datetime.now(timezone.utc).isoformat())
    await self._redis.setex(f"rpa_session_goal:{session_id}", 86400, goal_id)
```

**File to modify**: `agent-verse-backend/app/api/rpa.py`

Add endpoint:
```python
@router.get("/goals/{goal_id}/rpa-session")
async def get_goal_rpa_session(goal_id: str, request: Request) -> dict[str, Any]:
    """Get the RPA session(s) linked to a running goal."""
    ctx = _require_tenant(request)
    redis_client = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis_client is None:
        return {"session_ids": [], "goal_id": goal_id}

    sessions = await redis_client.hgetall(f"rpa_goal_sessions:{goal_id}")
    session_ids = [k.decode() if isinstance(k, bytes) else k for k in sessions.keys()]
    return {
        "goal_id": goal_id,
        "session_ids": session_ids,
        "primary_session_id": session_ids[0] if session_ids else None,
        "screenshot_stream_url": f"/rpa/sessions/{session_ids[0]}/stream" if session_ids else None,
    }
```

### 3.7 Action History Endpoint

```python
@router.get("/sessions/{session_id}/action-history")
async def get_action_history(
    session_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return paginated list of all actions executed in this session."""
    ctx = _require_tenant(request)
    # Actions are stored as sorted set in Redis: rpa_actions:{session_id}
    # Each member is a JSON-encoded action record, score = timestamp
    redis_client = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis_client is None:
        return {"actions": [], "total": 0, "session_id": session_id}

    key = f"rpa_actions:{ctx.tenant_id}:{session_id}"
    total = await redis_client.zcard(key)
    raw = await redis_client.zrevrange(key, offset, offset + limit - 1, withscores=True)
    import json
    actions = []
    for member, score in (raw or []):
        try:
            action = json.loads(member.decode() if isinstance(member, bytes) else member)
            actions.append(action)
        except Exception:
            pass
    return {"actions": actions, "total": total, "session_id": session_id}
```

### 3.8 Human Takeover — WebSocket Relay

**File to modify**: `agent-verse-backend/app/api/rpa.py`

```python
@router.websocket("/sessions/{session_id}/control")
async def rpa_control_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for human operator keyboard/mouse relay during takeover."""
    from fastapi import WebSocketDisconnect

    # Authenticate via protocol token (same pattern as collab.py)
    try:
        token = _extract_ws_auth(websocket)
        ctx = await _resolve_ws_tenant(websocket, token)
    except Exception:
        await websocket.close(code=4001)
        return

    # Verify takeover is active for this session
    redis_client = getattr(websocket.app.state, "_rate_limiter_redis", None)
    if redis_client:
        takeover_active = await redis_client.get(f"rpa_takeover:{ctx.tenant_id}:{session_id}")
        if not takeover_active:
            await websocket.close(code=4003)  # No active takeover
            return

    await websocket.accept()
    session_manager = getattr(websocket.app.state, "rpa_session_manager", None)

    try:
        while True:
            data = await websocket.receive_json()
            action_type = data.get("type")

            page = session_manager.get_page(session_id) if session_manager else None
            if page is None:
                await websocket.send_json({"type": "error", "message": "Browser page not available"})
                continue

            if action_type == "mouse_move":
                await page.mouse.move(data["x"], data["y"])
            elif action_type == "mouse_click":
                button = data.get("button", "left")
                await page.mouse.click(data["x"], data["y"], button=button)
            elif action_type == "key_press":
                await page.keyboard.press(data["key"])
            elif action_type == "type_text":
                await page.keyboard.type(data["text"], delay=30)
            elif action_type == "scroll":
                await page.evaluate(f"window.scrollBy({data.get('dx', 0)}, {data.get('dy', 100)})")
            elif action_type == "release_control":
                if redis_client:
                    await redis_client.delete(f"rpa_takeover:{ctx.tenant_id}:{session_id}")
                await websocket.send_json({"type": "control_released"})
                break

            # Send screenshot after each action
            screenshot_bytes = await page.screenshot(type="jpeg", quality=55)
            import base64
            await websocket.send_json({
                "type": "screenshot",
                "data_uri": f"data:image/jpeg;base64,{base64.b64encode(screenshot_bytes).decode()}",
                "url": page.url,
            })

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)[:200]})
        except Exception:
            pass
```

### 3.9 GoalDetailPage Integration

**File to modify**: `agent-verse-backend/app/api/goals.py`

Add the endpoint under goals (reuse existing goals router):
```python
@router.get("/{goal_id}/rpa-session")
async def get_goal_rpa_session_via_goals(goal_id: str, request: Request) -> dict[str, Any]:
    """Get linked RPA session for a goal (convenience endpoint under /goals)."""
    return await rpa_get_goal_rpa_session(goal_id, request)
```

---

## 4. Frontend Specification

### 4.1 New: BrowserMirrorPage at /rpa/sessions/:sessionId/live

**File to create**: `agent-verse-frontend/src/features/rpa/BrowserMirrorPage.tsx`

Full-screen layout, 60/40 split:

```typescript
import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Monitor, ArrowLeft, RefreshCw, Smartphone, Tablet, Download, User } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { toast } from "@/stores/toast";

type ViewportPreset = "desktop" | "tablet" | "mobile";
const VIEWPORT_SIZES: Record<ViewportPreset, { width: number; height: number }> = {
  desktop: { width: 1280, height: 800 },
  tablet:  { width: 768,  height: 1024 },
  mobile:  { width: 375, height: 667 },
};

export function BrowserMirrorPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const apiKey = useAuthStore(s => s.apiKey);

  const [currentScreenshot, setCurrentScreenshot] = useState<string>("");
  const [currentUrl, setCurrentUrl] = useState<string>("");
  const [events, setEvents] = useState<any[]>([]);
  const [viewport, setViewport] = useState<ViewportPreset>("desktop");
  const [zoom, setZoom] = useState(75);
  const [isTakeover, setIsTakeover] = useState(false);
  const [isConnected, setIsConnected] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  // Connect to SSE stream
  useEffect(() => {
    if (!sessionId) return;

    const API_BASE = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";
    // Use fetch-based SSE to include auth header (same pattern as useGoalStream)
    let cancelled = false;
    let abortController = new AbortController();

    async function connect() {
      try {
        const res = await fetch(`${API_BASE}/rpa/sessions/${sessionId}/stream`, {
          headers: { "X-API-Key": apiKey },
          signal: abortController.signal,
        });
        if (!res.ok || !res.body) return;

        setIsConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const event = JSON.parse(line.slice(6));
                if (event.type === "screenshot") {
                  setCurrentScreenshot(event.data_uri);
                  setCurrentUrl(event.url);
                } else {
                  setEvents(prev => [...prev.slice(-99), event]);
                }
              } catch {}
            }
          }
        }
      } catch (e) {
        if (!cancelled) setTimeout(connect, 3000); // reconnect
      } finally {
        setIsConnected(false);
      }
    }

    connect();
    return () => {
      cancelled = true;
      abortController.abort();
    };
  }, [sessionId, apiKey]);

  // Auto-scroll events
  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  // Takeover WebSocket
  const startTakeover = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await fetch(`/rpa/sessions/${sessionId}/takeover`, {
        method: "POST",
        headers: { "X-API-Key": apiKey, "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Manual operator takeover" }),
      });
      if (!res.ok) throw new Error("Takeover request failed");

      const WS_BASE = (import.meta as any).env?.VITE_WS_URL ?? "ws://localhost:8000";
      const ws = new WebSocket(`${WS_BASE}/rpa/sessions/${sessionId}/control`);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsTakeover(true);
        toast({ kind: "warning", message: "Takeover active — you have keyboard/mouse control" });
      };
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === "screenshot") setCurrentScreenshot(data.data_uri);
        if (data.type === "control_released") {
          setIsTakeover(false);
          ws.close();
        }
      };
      ws.onclose = () => setIsTakeover(false);
    } catch (e) {
      toast({ kind: "error", message: "Failed to start takeover" });
    }
  }, [sessionId, apiKey]);

  const releaseTakeover = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: "release_control" }));
  }, []);

  // Forward mouse/keyboard events during takeover
  const handleMouseEvent = useCallback((e: React.MouseEvent<HTMLImageElement>) => {
    if (!isTakeover || !wsRef.current || !imgRef.current) return;
    const rect = imgRef.current.getBoundingClientRect();
    const scaleX = VIEWPORT_SIZES[viewport].width / rect.width;
    const scaleY = VIEWPORT_SIZES[viewport].height / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    if (e.type === "click") {
      wsRef.current.send(JSON.stringify({ type: "mouse_click", x, y, button: "left" }));
    } else if (e.type === "mousemove") {
      wsRef.current.send(JSON.stringify({ type: "mouse_move", x, y }));
    }
  }, [isTakeover, viewport]);

  const handleKeyEvent = useCallback((e: React.KeyboardEvent) => {
    if (!isTakeover || !wsRef.current) return;
    e.preventDefault();
    wsRef.current.send(JSON.stringify({ type: "key_press", key: e.key }));
  }, [isTakeover]);

  return (
    <div
      className="flex flex-col h-screen bg-background"
      onKeyDown={handleKeyEvent}
      tabIndex={isTakeover ? 0 : -1}
      style={{ outline: isTakeover ? "2px solid hsl(var(--primary))" : "none" }}
    >
      {/* Takeover overlay banner */}
      {isTakeover && (
        <div className="flex items-center justify-between px-4 py-2 bg-primary text-primary-foreground text-sm animate-slide-down">
          <span className="font-medium">You have control — keyboard and mouse are active</span>
          <button onClick={releaseTakeover} className="px-3 py-1 bg-primary-foreground text-primary rounded hover:opacity-90 text-xs font-medium">
            Release Control
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-card">
        <button onClick={() => navigate("/rpa")} className="p-1.5 rounded hover:bg-muted" aria-label="Back to RPA">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <Monitor className="h-4 w-4 text-primary shrink-0" />
        <span className="text-sm font-mono text-muted-foreground truncate flex-1">{currentUrl || "Connecting…"}</span>
        <div className="flex items-center gap-2 shrink-0">
          {/* Viewport selector */}
          {[
            { id: "desktop" as ViewportPreset, icon: Monitor },
            { id: "tablet"  as ViewportPreset, icon: Tablet },
            { id: "mobile"  as ViewportPreset, icon: Smartphone },
          ].map(({ id, icon: Icon }) => (
            <button key={id} onClick={() => setViewport(id)}
              className={`p-1.5 rounded transition-colors ${viewport === id ? "bg-primary/10 text-primary" : "hover:bg-muted text-muted-foreground"}`}
              aria-label={`${id} viewport`}>
              <Icon className="h-4 w-4" />
            </button>
          ))}
          {/* Zoom */}
          <select value={zoom} onChange={e => setZoom(+e.target.value)} className="text-xs border rounded px-1 bg-background h-7">
            {[50, 75, 100].map(z => <option key={z} value={z}>{z}%</option>)}
          </select>
          {/* Connection status */}
          <div className={`h-2 w-2 rounded-full ${isConnected ? "bg-green-500 animate-pulse" : "bg-red-500"}`} title={isConnected ? "Connected" : "Disconnected"} />
        </div>
      </div>

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Browser mirror (left 60%) */}
        <div className="flex-[3] overflow-auto bg-muted/20 flex items-start justify-center p-4">
          {currentScreenshot ? (
            <div className="relative" style={{ width: `${VIEWPORT_SIZES[viewport].width * (zoom / 100)}px` }}>
              <img
                ref={imgRef}
                src={currentScreenshot}
                alt="Browser view"
                className={`w-full rounded border border-border shadow-lg select-none
                  ${isTakeover ? "cursor-crosshair ring-2 ring-primary" : "cursor-default"}
                  transition-opacity duration-75`}
                onClick={handleMouseEvent}
                onMouseMove={handleMouseEvent}
                draggable={false}
              />
              {/* Screenshot fade-in on update */}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <RefreshCw className="h-8 w-8 animate-spin mb-3 opacity-40" />
              <p className="text-sm">Connecting to browser session…</p>
            </div>
          )}
        </div>

        {/* Event log (right 40%) */}
        <div className="flex-[2] flex flex-col border-l border-border overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-card">
            <span className="text-xs font-medium">Action Log</span>
            <button
              onClick={startTakeover}
              disabled={isTakeover}
              className="flex items-center gap-1.5 px-2 py-1 text-xs border border-input rounded hover:bg-muted/50 disabled:opacity-50"
            >
              <User className="h-3 w-3" /> {isTakeover ? "Active" : "Take Control"}
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {events.map((event, i) => (
              <RpaEventRow key={i} event={event} />
            ))}
            <div ref={eventsEndRef} />
          </div>

          {/* Manual action panel */}
          <ManualActionPanel sessionId={sessionId!} apiKey={apiKey} />
        </div>
      </div>
    </div>
  );
}
```

### 4.2 RpaEventRow — Styled Event Display

```typescript
function RpaEventRow({ event }: { event: any }) {
  const [expanded, setExpanded] = useState(false);

  const typeConfig: Record<string, { icon: string; color: string; label: string }> = {
    navigation:         { icon: "🌐", color: "text-blue-600 dark:text-blue-400",   label: "Navigation" },
    action_started:     { icon: "▶",  color: "text-violet-600 dark:text-violet-400", label: "Started" },
    action_completed:   { icon: "✅",  color: "text-green-600 dark:text-green-400",  label: "Complete" },
    action_failed:      { icon: "❌",  color: "text-red-600 dark:text-red-400",      label: "Failed" },
    auto_heal_attempted:{ icon: "🔧",  color: "text-amber-600 dark:text-amber-400",  label: "Auto-healed" },
    takeover_requested: { icon: "👤",  color: "text-orange-600 dark:text-orange-400",label: "Takeover" },
    screenshot:         { icon: "📸",  color: "text-muted-foreground",               label: "Screenshot" },
  };

  // Don't show screenshot events individually (too noisy)
  if (event.type === "screenshot") return null;

  const config = typeConfig[event.type] ?? { icon: "•", color: "text-muted-foreground", label: event.type };

  return (
    <div
      className="flex items-start gap-2 p-1.5 rounded text-xs hover:bg-muted/30 cursor-pointer animate-event-in"
      onClick={() => setExpanded(v => !v)}
    >
      <span className="shrink-0 mt-0.5">{config.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`font-medium ${config.color}`}>{config.label}</span>
          {event.tool_name && <code className="text-[10px] bg-muted px-1 rounded">{event.tool_name}</code>}
          <span className="text-muted-foreground text-[10px] ml-auto shrink-0">
            {new Date(event.timestamp).toLocaleTimeString()}
          </span>
        </div>
        {event.type === "navigation" && (
          <p className="text-muted-foreground truncate">{event.to_url}</p>
        )}
        {(event.output || event.error) && !expanded && (
          <p className="text-muted-foreground truncate">{(event.output || event.error || "").slice(0, 60)}</p>
        )}
        {expanded && (event.output || event.error) && (
          <pre className="mt-1 bg-muted rounded p-1 overflow-auto max-h-24 whitespace-pre-wrap break-all text-[10px]">
            {event.output || event.error}
          </pre>
        )}
        {event.type === "auto_heal_attempted" && (
          <p className="text-amber-600 dark:text-amber-400 text-[10px]">
            {event.original_selector} → {event.new_selector} (conf: {(event.confidence * 100).toFixed(0)}%)
          </p>
        )}
      </div>
    </div>
  );
}
```

### 4.3 RpaLivePage Complete Redesign

**File to modify**: `agent-verse-frontend/src/features/rpa/RpaLivePage.tsx`

Replace polling hack with proper session list + "View Live" buttons:

```typescript
// Key changes:
// 1. List sessions via GET /rpa/sessions (already exists)
// 2. Each session card shows URL preview + status + "View Live" button → /rpa/sessions/{id}/live
// 3. "New Session" creates a session + navigates to live page
// 4. Remove all 2-second polling logic
```

### 4.4 GoalDetailPage — RPA Integration

**File to modify**: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`

```typescript
// Add query for RPA session:
const { data: rpaSession } = useQuery({
  queryKey: ["goal-rpa-session", goalId],
  queryFn: () => fetch(`/goals/${goalId}/rpa-session`, { headers: { "X-API-Key": apiKey } }).then(r => r.json()),
  refetchInterval: 10_000,
  enabled: !!goalId,
});

// In header (show when rpaSession exists):
{rpaSession?.primary_session_id && (
  <button
    onClick={() => navigate(`/rpa/sessions/${rpaSession.primary_session_id}/live`)}
    className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-green-300 dark:border-green-700 text-green-700 dark:text-green-400 rounded-lg hover:bg-green-50 dark:hover:bg-green-950/30 transition-colors"
  >
    <Monitor className="h-3.5 w-3.5" animate-pulse /> View Browser
  </button>
)}
```

### 4.5 RPA Analytics Page at /rpa/analytics

**File to create**: `agent-verse-frontend/src/features/rpa/RpaAnalyticsPage.tsx`

4 charts:
1. **Sessions per day** — ThemedBarChart (last 30d)
2. **Most-used tools** — ThemedBarChart horizontal (top 10)
3. **Auto-heal success rate** — gauge (healed/total_heal_attempts * 100%)
4. **Average action duration** — ThemedBarChart per tool (ms)

### 4.6 Sidebar Updates

```typescript
// Replace existing /rpa/live with /rpa:
{ to: "/rpa", icon: Monitor, label: "RPA Sessions" },
{ to: "/rpa/analytics", icon: BarChart3, label: "RPA Analytics" },
```

### 4.7 App.tsx Updates

```typescript
const BrowserMirrorPage = lazy(() => import("@/features/rpa/BrowserMirrorPage").then(m => ({ default: m.BrowserMirrorPage })));
const RpaAnalyticsPage  = lazy(() => import("@/features/rpa/RpaAnalyticsPage").then(m => ({ default: m.RpaAnalyticsPage })));

// Routes:
<Route path="rpa" element={<RpaLivePage />} />
<Route path="rpa/sessions/:sessionId/live" element={<Suspense fallback={<LoadingSpinner />}><BrowserMirrorPage /></Suspense>} />
<Route path="rpa/live" element={<Navigate to="/rpa" replace />} />  // backward compat redirect
<Route path="rpa/analytics" element={<Suspense fallback={<LoadingSpinner />}><RpaAnalyticsPage /></Suspense>} />
```

### 4.8 Animations

**1. Screenshot frame transition**:
```css
/* Subtle opacity pulse on each new frame */
@keyframes screenshotUpdate {
  0%   { opacity: 0.8; }
  100% { opacity: 1.0; }
}
/* Apply for 80ms on each src change (via useEffect with key on screenshot src) */
```

**2. Event row entrance**:
```css
@keyframes eventIn {
  from { transform: translateX(-10px); opacity: 0; }
  to   { transform: translateX(0); opacity: 1; }
}
.animate-event-in { animation: eventIn 150ms ease-out forwards; }
```

**3. Takeover mode entrance**:
```css
@keyframes slideDown {
  from { transform: translateY(-100%); }
  to   { transform: translateY(0); }
}
.animate-slide-down { animation: slideDown 250ms ease-out forwards; }
```

**4. Error flash**: On `action_failed` event, briefly flash the browser mirror div:
```css
@keyframes errorFlash {
  0%   { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
  30%  { box-shadow: 0 0 20px 8px rgba(239, 68, 68, 0.4); }
  100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
}
```

**5. Auto-heal indicator**: When auto_heal_attempted event arrives, show a temporary amber border on the screenshot image with a `🔧` badge at the bottom-left corner (600ms, then fade out).

**6. Session card hover** (in RpaLivePage):
```css
transform: translateY(-3px);
box-shadow: 0 8px 20px rgba(0,0,0,0.1);
transition: all 200ms ease-out;
```

---

## 5. TypeScript Interfaces

```typescript
// Add to agent-verse-frontend/src/lib/api/client.ts:

export interface RpaSessionMeta {
  session_id: string;
  tenant_id: string;
  worker_id: string;
  created_at: string;
  url?: string;
  goal_id?: string;
}

export type RpaStreamEventType =
  | "screenshot" | "navigation" | "action_started" | "action_completed"
  | "action_failed" | "auto_heal_attempted" | "takeover_requested"
  | "stream_started" | "error";

export interface RpaStreamEvent {
  type: RpaStreamEventType;
  timestamp: string;
  [key: string]: unknown;
}

export interface RpaCurrentView {
  session_id: string;
  screenshot_data_uri: string;
  url: string;
  title: string;
  timestamp: string;
}

export interface GoalRpaSession {
  goal_id: string;
  session_ids: string[];
  primary_session_id: string | null;
  screenshot_stream_url: string | null;
}

// Add to rpaApi:
// getCurrentView: (sessionId) => request<RpaCurrentView>(`/rpa/sessions/${sessionId}/current-view`),
// getGoalSession: (goalId) => request<GoalRpaSession>(`/rpa/sessions/goal/${goalId}`),
// getActionHistory: (sessionId, limit?, offset?) => request<{actions: any[]; total: number}>(`/rpa/sessions/${sessionId}/action-history?...`),
```

---

## 6. Zustand Store

```typescript
// agent-verse-frontend/src/stores/rpaStore.ts
import { create } from "zustand";

interface RpaStore {
  activeSessions: Record<string, RpaSessionMeta>;
  streamEvents: Record<string, RpaStreamEvent[]>;
  currentScreenshots: Record<string, string>;
  isTakeover: Record<string, boolean>;

  setSession: (sessionId: string, meta: RpaSessionMeta) => void;
  addStreamEvent: (sessionId: string, event: RpaStreamEvent) => void;
  updateScreenshot: (sessionId: string, dataUri: string) => void;
  setTakeover: (sessionId: string, active: boolean) => void;
  clearSession: (sessionId: string) => void;
}

export const useRpaStore = create<RpaStore>((set) => ({
  activeSessions: {},
  streamEvents: {},
  currentScreenshots: {},
  isTakeover: {},

  setSession: (id, meta) => set(s => ({ activeSessions: { ...s.activeSessions, [id]: meta } })),
  addStreamEvent: (id, event) => set(s => ({
    streamEvents: { ...s.streamEvents, [id]: [...(s.streamEvents[id] ?? []).slice(-199), event] }
  })),
  updateScreenshot: (id, uri) => set(s => ({ currentScreenshots: { ...s.currentScreenshots, [id]: uri } })),
  setTakeover: (id, active) => set(s => ({ isTakeover: { ...s.isTakeover, [id]: active } })),
  clearSession: (id) => set(s => {
    const { [id]: _, ...sessions } = s.activeSessions;
    const { [id]: __, ...events } = s.streamEvents;
    const { [id]: ___, ...screenshots } = s.currentScreenshots;
    const { [id]: ____, ...takeover } = s.isTakeover;
    return { activeSessions: sessions, streamEvents: events, currentScreenshots: screenshots, isTakeover: takeover };
  }),
}));
```

---

## 7. Testing Strategy

```python
# tests/rpa/test_rpa_readonly_screenshot.py
def test_current_view_does_not_create_action_record():
    """GET /rpa/sessions/{id}/current-view does not write to action history."""
    # Mock Playwright page, call endpoint, assert no action_completed event emitted

# tests/rpa/test_rpa_redis_registry.py
def test_session_registered_in_redis_on_create():
    """New session writes rpa_session_alive:{id} to Redis."""
    # Create session, check Redis key

def test_cross_worker_session_lookup():
    """get_session_worker returns correct worker_id for any session."""
    pass

# tests/rpa/test_rpa_new_primitives.py
def test_rpa_scroll():
    """rpa_scroll executes JavaScript scrollBy and returns screenshot."""
    pass

def test_rpa_keyboard_press():
    """rpa_keyboard_press calls page.keyboard.press with correct key."""
    pass

def test_rpa_evaluate_js():
    """rpa_evaluate_js executes expression and returns string result."""
    pass

# tests/rpa/test_rpa_goal_link.py
def test_goal_rpa_session_link():
    """After RPA action with _goal_id, /goals/{id}/rpa-session returns session_id."""
    pass

# tests/rpa/test_rpa_auto_heal.py
def test_auto_heal_on_selector_failure():
    """When rpa_click selector fails and BrowserAgent suggests alternative, healed selector is tried."""
    pass
```

```typescript
// Frontend: BrowserMirrorPage.test.tsx
test("renders screenshot from SSE stream", async () => { ... });
test("takeover mode enables keyboard forwarding to WebSocket", async () => { ... });
test("event log shows action events in order", async () => { ... });
test("error flash animation triggers on action_failed event", async () => { ... });

// E2E: rpa-live.spec.ts
test("open RPA session → screenshot appears in mirror → navigate to URL → see navigation event in log", async ({ page }) => { ... });
```

---

## 8. Docker Considerations

```yaml
# docker-compose.yml — backend service:
environment:
  PLAYWRIGHT_SCREENSHOT_FPS: "1"
  PLAYWRIGHT_HEADLESS: "true"

# Dockerfile — ensure Playwright is installed:
RUN pip install playwright
RUN playwright install chromium --with-deps
# OR: use --no-sandbox for container environments:
environment:
  PLAYWRIGHT_CHROMIUM_ARGS: "--no-sandbox --disable-setuid-sandbox"
```

Verify Playwright is in `agent-verse-backend/pyproject.toml` dependencies:
```toml
[project.dependencies]
playwright = ">=1.40.0"
```
