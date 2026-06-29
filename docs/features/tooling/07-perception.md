# Perception

**Perception** is AgentVerse's AI-powered web intelligence subsystem. It gives agents — and operators — the ability to see the web the way a human does: open a URL in a real browser, take a screenshot, extract text, and use a vision LLM to understand the page's content and structure.

---

## What Perception Is

Perception bridges the gap between the open web and the agent loop. While MCP tools provide structured API access to known services, Perception handles **any URL** — legacy enterprise apps with no API, competitor pricing pages, public dashboards, visual UI validation, and more.

The system is composed of two classes:

| Class | Source | Role |
|---|---|---|
| `BrowserAgent` | `app/perception/browser_agent.py` | Playwright session manager — opens URLs, captures screenshots, extracts text |
| `PageAnalyzer` | `app/perception/page_analyzer.py` | Orchestrates BrowserAgent and vision LLM into a complete page analysis |

---

## Three Operations

### 1. Screenshot (`POST /perception/screenshot`)

Opens the URL in a headless Chromium browser via Playwright and captures a full-page or viewport PNG screenshot. Returns it as a base64-encoded string.

### 2. Analyze (`POST /perception/analyze`)

Passes either a URL or an existing `screenshot_b64` to the vision LLM with a configurable question. Returns the LLM's structured text analysis.

### 3. Extract Text (`POST /perception/extract`)

Uses Playwright to extract the page's plain text (rendered DOM text content, not raw HTML). Useful for scraping when you don't need visual analysis.

---

## System Components

### BrowserAgent

`BrowserAgent` wraps Playwright's async API to provide three core operations:

```python
# Screenshot (returns base64 PNG)
result: ScreenshotResult = await browser_agent.take_screenshot(url)
result.screenshot_b64   # base64-encoded PNG
result.success          # bool
result.error            # str | None

# Text extraction
result: TextResult = await browser_agent.extract_text(url)
result.output           # plain text
result.char_count       # len(output)

# Vision analysis (requires vision_provider)
analysis: str = await browser_agent.analyze_screenshot(screenshot_b64, question)
```

BrowserAgent is checked for availability at startup:

```python
from app.perception.browser_agent import _PLAYWRIGHT_AVAILABLE
```

This boolean is exposed at `GET /perception/status` and displayed in the Perception UI as the "Browser (Playwright)" status badge.

### PageAnalyzer

`PageAnalyzer` combines the two BrowserAgent operations into a complete `PageAnalysis`:

```python
@dataclass
class PageAnalysis:
    url:             str
    title:           str
    text_content:    str    # Extracted DOM text
    screenshot_b64:  str    # Base64 PNG
    llm_analysis:    str    # Vision LLM answer to the question
    success:         bool
    error:           str
    metadata:        dict

    def to_context_block(self) -> str:
        """Format for injection into planner prompt."""
        # Returns: "### Page: {url}\nTitle: {title}\nAnalysis:\n{llm_analysis}"
```

The `to_context_block()` method is called by the agent loop when a Perception result needs to be injected into the Planner's context.

---

## Multimodal Analysis

When a vision-capable LLM provider is configured (e.g., Claude 3, GPT-4 Vision), the `analyze` endpoint:

1. Takes a Playwright screenshot (base64 PNG)
2. Encodes it as a multimodal message: `[{type: "image_url", url: "data:image/png;base64,{b64}"}]`
3. Appends the question as a `text` message
4. Sends to the vision LLM
5. Returns the LLM's textual analysis

```python
# Simplified from BrowserAgent.analyze_screenshot()
messages = [
    {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            {"type": "text",      "text": question}
        ]
    }
]
response = await vision_provider.complete(messages)
return response.choices[0].message.content
```

The vision provider is the same `LLMProvider` abstraction used by the agent loop — vendor-agnostic. If no vision provider is available, `vision_available: false` is returned by the status endpoint and the Analyze button works in text-extraction-only mode.

---

## Status Check

```bash
curl "https://api.agentverse.dev/perception/status" -H "X-API-Key: $API_KEY"
```

```json
{
  "playwright_available": true,
  "vision_available": true,
  "browser_actions": ["screenshot", "extract_text", "click", "fill", "navigate"],
  "image_formats": ["png", "jpeg", "webp"]
}
```

If `playwright_available: false`, all three action buttons in the UI are disabled with the message: _"Playwright is unavailable on the server — browser actions are disabled."_

---

## API Reference

All endpoints require `X-API-Key: <tenant_api_key>`.

### `POST /perception/screenshot`

```bash
curl -X POST https://api.agentverse.dev/perception/screenshot \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "full_page": false}'
```

**Request**

| Field | Type | Required | Default |
|---|---|---|---|
| `url` | `string` | Yes | — |
| `full_page` | `boolean` | No | `false` |

URL must start with `http://` or `https://` (validated at API layer).

**Response**

```json
{
  "success":       true,
  "url":           "https://example.com",
  "screenshot_b64": "iVBORw0KGgoAAAANSUhEUgAA...",
  "error":         null
}
```

---

### `POST /perception/analyze`

```bash
curl -X POST https://api.agentverse.dev/perception/analyze \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://competitor.com/pricing",
    "question": "What are the prices for each plan tier? List them as JSON."
  }'
```

Or using a previously captured screenshot:

```bash
curl -X POST https://api.agentverse.dev/perception/analyze \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "screenshot_b64": "<base64-encoded-png>",
    "question": "How many items are in the shopping cart?"
  }'
```

**Request**

| Field | Type | Required | Note |
|---|---|---|---|
| `url` | `string` | One of url/screenshot_b64 | Takes a new screenshot |
| `screenshot_b64` | `string` | One of url/screenshot_b64 | Uses existing screenshot |
| `question` | `string` | No | Default: "What is the main purpose and content of this page?" |

**Response**

```json
{
  "analysis": "The pricing page shows three plans:\n- Starter: $29/month (5 users, 10GB)\n- Professional: $99/month (25 users, 100GB)\n- Enterprise: Custom pricing (unlimited users)"
}
```

---

### `POST /perception/extract`

```bash
curl -X POST https://api.agentverse.dev/perception/extract \
  -H "X-API-Key: $API_KEY" \
  -d '{"url": "https://news.ycombinator.com"}'
```

**Response**

```json
{
  "success":    true,
  "text":       "Hacker News\n1. Show HN: AgentVerse - AI agents at scale\n  247 points | 89 comments\n...",
  "char_count": 12847,
  "error":      null
}
```

---

## Execution Sequence

```mermaid
sequenceDiagram
    participant UI as Perception UI
    participant API as /perception/analyze
    participant BA as BrowserAgent
    participant PW as Playwright (Chromium)
    participant VLM as Vision LLM

    UI->>API: POST {url, question}
    API->>API: Validate URL scheme (http/https)
    API->>BA: take_screenshot(url)
    BA->>PW: page.goto(url)
    PW->>PW: Load page, render JavaScript
    PW-->>BA: screenshot PNG bytes
    BA->>BA: base64-encode PNG
    BA-->>API: {screenshot_b64, success}

    API->>BA: analyze_screenshot(screenshot_b64, question)
    BA->>VLM: [{image_url: "data:image/png;base64,..."},\n{text: question}]
    VLM-->>BA: analysis text
    BA-->>API: analysis string
    API-->>UI: {analysis: "The pricing page shows..."}

    Note over UI: Renders analysis text below screenshot
```

---

## Perception UI Walkthrough

The `PerceptionPage` component offers four functional areas:

### Provider Status Bar

Two status badges shown at the top:
- **Browser (Playwright)**: green if Playwright is installed on the server
- **Vision LLM**: green if a vision-capable provider is configured

### URL + Question Inputs

- `URL` field: must start with `http://` or `https://`
- `Analysis question`: defaults to "What is the main purpose and content of this page?"

### Three Action Buttons

| Button | API Call | When Enabled |
|---|---|---|
| Screenshot | `POST /perception/screenshot` | URL non-empty + Playwright available |
| Analyze | `POST /perception/analyze` | URL non-empty + Playwright available |
| Extract text | `POST /perception/extract` | URL non-empty + Playwright available |

The **Analyze** button is smart: if a screenshot was already captured in this session, it sends `{screenshot_b64, question}` (no second browser round-trip). If no screenshot exists, it sends `{url, question}` and the API takes the screenshot internally.

```tsx
// From PerceptionPage.tsx:33-36
mutationFn: () =>
  perceptionApi.analyze(
    screenshot ? { screenshot_b64: screenshot, question }
               : { url, question }
  ),
```

### Results Panels

- **Screenshot**: rendered as `<img src="data:image/png;base64,{b64}">` — full-resolution inline preview
- **Analysis**: pre-wrap text panel below screenshot
- **Extracted text**: monospace code block, max-height 96 lines with overflow scroll; shows character count in header

---

## Use Cases

| Use Case | Operation | Notes |
|---|---|---|
| Monitor competitor pricing | Analyze | Schedule via agent trigger |
| Validate UI after deployment | Screenshot + Analyze | "Does the checkout button appear?" |
| Scrape public data (no API) | Extract text | Faster than full analysis; no vision LLM cost |
| Debug broken web form | Screenshot | Visual evidence capture |
| Understand legacy app for RPA scripting | Analyze | "What fields does this form have?" |

---

## Agent Loop Integration

When an agent's Planner requests information about a web page, the agent loop invokes `PageAnalyzer.analyze_url()` and injects the result into the next planning cycle:

```python
analysis = await page_analyzer.analyze_url(
    url="https://competitor.com/pricing",
    question="What are the pricing tiers?",
)
# Injected into next planning prompt:
# ### Page: https://competitor.com/pricing
# Title: Competitor Pricing
# Analysis: The page shows three tiers...
context_block = analysis.to_context_block()
```

This allows a single agent to build multi-page research by analyzing multiple URLs concurrently via `PageAnalyzer.analyze_multiple()`.
