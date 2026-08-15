---
title: "Chat — Frontend Components & UX"
description: "All React components, UX patterns, keyboard shortcuts, accessibility, responsive layout, file handling, @mentions, /slash commands, dark mode, and output rendering."
outline: deep
---

# Frontend Components & UX

## Component Tree

```
ChatPage
├── ChatSidebar
│   ├── SearchInput
│   ├── NewChatButton
│   ├── AgentSelector
│   └── SessionList (virtualized)
│       └── SessionItem (pin icon, TTL badge, context menu)
└── ChatMain
    ├── ChatThread (virtualized, auto-scroll)
    │   ├── DateSeparator
    │   ├── ChatMessage (user bubble)
    │   ├── ChatMessage (assistant bubble — markdown + code)
    │   ├── ChatStepCard (agent step with status/expand)
    │   ├── ChatGoalSummary (completion card + follow-ups)
    │   ├── ChatClarifyCard (question + quick-reply chips)
    │   └── ChatHITLCard (approval gate)
    └── ChatInputArea
        ├── AttachmentChips
        ├── MentionAutocomplete (@agent)
        ├── SlashCommandMenu (/)
        └── ChatInput (auto-grow textarea + send button)
```

---

## Page Layout

```
┌──────────────────────────────────────────────────────────────┐
│  ☰ AgentVerse                                   [+ New Chat] │
├──────────────────────┬───────────────────────────────────────┤
│  SIDEBAR (280px)     │  THREAD (flex-1)                      │
│  ─────────────────   │  ─────────────────────────────────    │
│  🔍 Search           │  ─── Today ──────────────────────    │
│                      │                                       │
│  📌 Deploy prod      │  [user bubble]  "What is the loop?"  │
│  📌 Invoice proc.    │                                       │
│  ─────────────────   │  [assistant]  Token-by-token...      │
│  Today               │  [Copy] [👍] [👎]                     │
│    Q3 analysis  🔵   │                                       │
│    Test failures     │  [user]  "Deploy to staging"          │
│  Yesterday           │                                       │
│    Deploy to stage   │  🔵 Routing to agent...               │
│  ─────────────────   │  ⚙️ Step 1: Run tests     ✅  2.3s   │
│  [Agent: Default ▼]  │  ⚙️ Step 2: Build image   ⏳...      │
│                      │                       [■ Stop]        │
├──────────────────────┼───────────────────────────────────────┤
│                      │  [📎] [  Type a message...        ↵ ]│
└──────────────────────┴───────────────────────────────────────┘
```

---

## ChatSidebar

### Session Item
Each session shows:
- Title (auto-generated, editable on double-click)
- 📌 pin icon (top-right, toggles on click)
- **7d / 3d badge** — amber when session expires within 3 days
- Active goal indicator (🔵 pulsing dot)
- Context menu (right-click / ⋮): Rename, Pin/Unpin, Delete, Export

### Agent Selector
Dropdown at sidebar bottom. Persisted per session:
- Default (auto-route)
- Named agents (Customer Support, Code Review, Data Analysis, etc.)
- Typing `@agent-name` in the input also triggers a switch for that message

---

## ChatThread

### Virtualization
Both the session list and message thread use `react-virtual` (`useVirtualizer`) for O(1) rendering regardless of item count:
- Session list: virtualized at 50+ sessions
- Message thread: virtualized at 100+ messages

### Auto-Scroll
The thread auto-scrolls to bottom when:
- A new message is added
- A streaming token arrives (if user is already at bottom)

If the user has scrolled up to read history, auto-scroll pauses. A "↓ New messages" badge appears at the bottom — clicking it resumes auto-scroll.

### Message Bubble Layout

| Type | Position | Background |
|---|---|---|
| User | Right-aligned | `bg-blue-600` |
| Assistant (Q&A) | Left-aligned | `bg-zinc-800` |
| Assistant (Goal complete) | Left-aligned | `bg-emerald-900` |
| System (clarify/HITL) | Center-aligned | `bg-amber-900/50` |

---

## ChatMessage (Assistant)

Full markdown rendering via `react-markdown` + `remark-gfm` + `rehype-highlight`:

- **Headings** (`#`, `##`, `###`)
- **Bold/italic/strikethrough**
- **Ordered and unordered lists**
- **Tables** with proper borders
- **Blockquotes**
- **Inline code** with monospace styling
- **Code blocks** — language label + syntax highlighting + copy button
- **Links** — open in new tab with `rel="noopener noreferrer"`
- **Images** — rendered inline (from URLs in response)
- **Task lists** (`- [x]`)

### Message Actions (hover)
- **Copy** — copies raw markdown content to clipboard
- **👍 / 👎** — feedback sent to `POST /v1/chat/sessions/{id}/messages/{id}/feedback`
- **↺ Retry** — regenerates the response (Q&A only)
- **Share** — copies a permalink (if sharing is enabled for tenant)

---

## ChatInput

### Auto-Growing Textarea
- Min height: 1 line (~40px)
- Max height: 8 lines (~200px)
- Overflow: scroll after 8 lines

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Enter` | Send message |
| `Shift+Enter` | Insert newline |
| `Ctrl/Cmd+K` | New chat session |
| `Ctrl/Cmd+/` | Focus input |
| `Ctrl/Cmd+P` | Search sessions |
| `Escape` | Close sidebar (mobile) / cancel HITL confirmation |
| `↑` (in empty input) | Edit last user message |

### File Attachment
- Click paperclip icon or drag-and-drop into input area
- Accepted: `image/*`, `application/pdf`, `text/csv`, `application/json`, `text/plain`
- Shows attachment chip: `📎 filename.pdf ×`
- On send: uploads file, attaches `{file_id}` to message metadata
- Files stored in object storage (MinIO/S3), URL embedded in metadata
- OCR engine automatically invoked for PDF/image attachments

### Image Paste (Ctrl+V)
Pasting an image from clipboard is supported:

```typescript
const handlePaste = (e: ClipboardEvent) => {
  const items = Array.from(e.clipboardData?.items ?? []);
  const imageItem = items.find(i => i.type.startsWith('image/'));
  if (imageItem) {
    e.preventDefault();
    const file = imageItem.getAsFile()!;
    addAttachment(file);  // triggers file upload + attachment chip
  }
};
```

### @Agent Mentions
Type `@` → surfaces agent autocomplete list. Selecting an agent sets it as the executor for this specific message (overrides session agent).

### /Slash Commands
Type `/` → surfaces command palette:

| Command | Action |
|---|---|
| `/new` | Start new session |
| `/clear` | Clear current thread (local only) |
| `/pin` | Pin current session |
| `/export` | Export session as markdown |
| `/agent <name>` | Switch session agent |
| `/context <file>` | Inject file content as context |

### `#` Context References
Type `#` → surfaces workspace context items (if integration enabled):
- `#file:app/main.py` — injects file content as context for the next message
- `#selection` — injects current editor selection (VS Code extension)

---

## Dark / Light Mode

Follows `prefers-color-scheme` by default. Manual toggle stored in `localStorage`.

```css
/* CSS variables — all components use these */
:root {
  --bg-primary: #0d1117;   /* dark */
  --bg-secondary: #161b22;
  --text-primary: #e6edf3;
  --accent: #4a9eed;
}

[data-theme="light"] {
  --bg-primary: #ffffff;
  --bg-secondary: #f6f8fa;
  --text-primary: #1f2328;
  --accent: #0969da;
}
```

---

## Responsive Layout

| Breakpoint | Layout |
|---|---|
| Desktop (`>= 1024px`) | Sidebar (280px fixed) + Thread (flex-1) |
| Tablet (`640px–1024px`) | Sidebar collapses to icon rail (60px) + swipe/click to expand |
| Mobile (`< 640px`) | Single column; sidebar is a drawer triggered by hamburger menu |

Touch targets are minimum 44×44px. Swipe left on session item reveals delete action.

---

## Accessibility

- All interactive elements keyboard-navigable via `Tab`
- `aria-live="polite"` on message thread — screen reader announces new messages
- `aria-busy="true"` on thread while goal is running
- Focus management: when new assistant message arrives, focus moves to it
- Skip-to-main-content link at page top
- HITL/clarify cards have `role="alertdialog"` and trap focus until answered
- All icons have `aria-hidden="true"` with adjacent visible labels
- Color contrast meets WCAG 2.2 AA (4.5:1 normal text, 3:1 large text)

---

## Empty State

New sessions show a starter prompt grid:

```
┌─────────────────────┐  ┌─────────────────────┐
│ 💬 What can AgentVerse│  │ 🧪 Run all tests    │
│    do?               │  │    and fix failures │
└─────────────────────┘  └─────────────────────┘
┌─────────────────────┐  ┌─────────────────────┐
│ 📊 Analyze Q3 revenue│  │ 🚀 Deploy to staging│
└─────────────────────┘  └─────────────────────┘
```

Clicking a prompt pre-fills the input. These are configurable per tenant.

---

## Session Export

`/export` or the context menu "Export" action downloads the session as clean markdown:

```markdown
# Session: Deploy to staging
*AgentVerse Chat · 2026-08-15*

---
**User:** Deploy the app to staging

**Agent:** ✅ Deployment complete (4.3s)

Steps:
1. Run tests — 47 passed ✅
2. Build Docker image — v2.4.1 ✅  
3. Push to staging — healthy ✅

---
```

PDF export (via browser print dialog with `@media print` stylesheet) is also supported.
